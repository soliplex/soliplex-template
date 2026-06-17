"""Provision the generated stack's local Gitea: admin user, token, repo, .env.

This is the reusable core behind the generated project's bundled
``scripts/init_gitea.py`` -- a PEP 723 shim that ships in stacks with the
opt-in gitea service and delegates here. It ships in the published
``soliplex-template`` distribution so the shim can
``from soliplex_template.gitea import provision_gitea``.

Run it AFTER ``docker compose up -d`` (postgres + gitea healthy). It waits for
Gitea, ensures an admin user (via ``docker compose exec``), mints a scoped
access token and a tracking repository (over the REST API), and writes
``GITEA_HOST`` / ``GITEA_ACCESS_TOKEN`` into ``.env`` for downstream consumers.
Idempotent: re-running resets the admin password and reuses the existing repo.

Stdlib only -- HTTP via :mod:`urllib.request`, the admin user via
``subprocess`` (``docker compose exec``); no ``curl``/``openssl``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import secrets
import string
import subprocess
import time
import urllib.error
import urllib.request

# Host-published Gitea URL (used for provisioning from the host). The backend
# reaches Gitea over the compose network at the internal URL, which is what
# lands in .env.
GITEA_HTTP = "http://localhost:3000"
GITEA_INTERNAL_URL = "http://gitea:3000"

ADMIN_USER = "soliplex-admin"
ADMIN_EMAIL = "admin@soliplex.localhost"
REPO_NAME = "soliplex-requests"

# Gitea's built-in SSH server (rootless image): host port and in-container
# port are both 2222, the login user is 'git'. The template hardcodes these
# (GITEA__server__SSH_PORT / the 2222:2222 mapping), so they are not
# parameterized here.
GITEA_SSH_USER = "git"
GITEA_SSH_HOST = "localhost"
GITEA_SSH_PORT = 2222

TOKEN_SCOPES = ["write:repository", "write:issue"]
ENV_FILE = ".env"

# Appended to the random admin password so it always satisfies Gitea's
# complexity rule (upper + lower + digit + symbol), whatever the random part.
PASSWORD_COMPLEXITY_SUFFIX = "Aa1!"

# Readiness poll: up to ATTEMPTS tries, DELAY seconds apart (~2 min).
READY_ATTEMPTS = 60
READY_DELAY = 2


class GiteaError(Exception):
    """A user-facing error (printed without a traceback)."""


class GiteaNotReady(GiteaError):
    def __init__(self, url):
        self.url = url
        super().__init__(
            f"Gitea did not become ready at {url} "
            "(is the stack up? 'docker compose up -d gitea')"
        )


class ReservedUser(GiteaError):
    def __init__(self, name):
        self.name = name
        super().__init__(
            f"web-UI admin user may not be {name!r}: it is the rotating "
            "service account"
        )


class PasswordMismatch(GiteaError):
    def __init__(self):
        super().__init__("passwords did not match")


class TokenRequestFailed(GiteaError):
    def __init__(self, code, body):
        self.code = code
        self.body = body
        super().__init__(f"token request failed: HTTP {code}: {body}")


class NoToken(GiteaError):
    def __init__(self, body):
        self.body = body
        super().__init__(f"could not parse token from response: {body}")


class RepoCreationFailed(GiteaError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"repo creation failed (HTTP {code})")


class NotAGitRepo(GiteaError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"{path} is not a git repository "
            "(was the stack scaffolded with --no-git?)"
        )


class No_SSH_hKeys(GiteaError):
    def __init__(self):
        super().__init__(
            "no SSH public key found to register with Gitea: load one into "
            "your agent (ssh-add), pass --ssh-key PATH, or create one "
            "(ssh-keygen)"
        )


class KeyUploadFailed(GiteaError):
    def __init__(self, code, body):
        self.code = code
        self.body = body
        super().__init__(f"SSH key upload failed: HTTP {code}: {body}")


class PushFailed(GiteaError):
    def __init__(self, stderr):
        self.stderr = stderr
        super().__init__(f"git push to Gitea failed: {stderr}")


def generate_admin_password(length: int = 32) -> str:
    """A random admin password that satisfies Gitea's complexity rule.

    Never persisted -- only the minted token is written out.
    """
    alphabet = string.ascii_letters + string.digits
    base = "".join(secrets.choice(alphabet) for _ in range(length))
    return base + PASSWORD_COMPLEXITY_SUFFIX


def _request(method, url, *, user=None, password=None, data=None):
    headers = {}
    if user is not None:
        raw = f"{user}:{password}".encode()
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode()}"
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=body, headers=headers, method=method
    )
    return urllib.request.urlopen(req)


def wait_for_gitea(
    base_url: str = GITEA_HTTP,
    *,
    attempts: int = READY_ATTEMPTS,
    delay: int = READY_DELAY,
    sleep=time.sleep,
) -> bool:
    """Poll ``<base_url>/api/v1/version`` until it answers; True if ready."""
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/version") as resp:
                resp.read()
        except (urllib.error.URLError, OSError):
            sleep(delay)
        else:
            return True
    return False


def parse_token(body: str) -> str:
    """The ``sha1`` token from a Gitea token-creation response.

    Raises :class:`GiteaError` if the body is not JSON or carries no ``sha1``.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise NoToken(body) from exc
    token = data.get("sha1")
    if not token:
        raise NoToken(body)
    return token


def _docker_compose_gitea(*args, project_dir):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "git", "gitea", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def ensure_admin_user(username, email, password, *, project_dir) -> None:
    """Create the named site-admin user, or reset its password if it exists."""
    created = _docker_compose_gitea(
        "gitea",
        "admin",
        "user",
        "create",
        "--admin",
        "--username",
        username,
        "--email",
        email,
        "--password",
        password,
        "--must-change-password=false",
        project_dir=project_dir,
    )
    if created.returncode == 0:
        return
    print(f"  user {username!r} exists; resetting password")
    result = _docker_compose_gitea(
        "gitea",
        "admin",
        "user",
        "change-password",
        "--username",
        username,
        "--password",
        password,
        "--must-change-password=false",
        project_dir=project_dir,
    )
    result.check_returncode()


def mint_token(password: str, *, token_name: str) -> str:
    """Mint a scoped access token for the admin user; return its sha1."""
    url = f"{GITEA_HTTP}/api/v1/users/{ADMIN_USER}/tokens"
    data = {"name": token_name, "scopes": TOKEN_SCOPES}
    try:
        with _request(
            "POST", url, user=ADMIN_USER, password=password, data=data
        ) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise TokenRequestFailed(exc.code, body) from exc
    return parse_token(body)


def create_repo(
    password: str,
    *,
    name: str = REPO_NAME,
    auto_init: bool = True,
    private: bool = False,
) -> int:
    """Create a repo owned by the admin user (idempotent: 201 or 409)."""
    url = f"{GITEA_HTTP}/api/v1/user/repos"
    data = {"name": name, "auto_init": auto_init, "private": private}
    try:
        with _request(
            "POST", url, user=ADMIN_USER, password=password, data=data
        ) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        code = exc.code
    if code not in (201, 409):
        raise RepoCreationFailed(code)
    return code


def stack_ssh_url(repo_name: str) -> str:
    """The SSH clone URL for a repo owned by the admin service account."""
    return (
        f"ssh://{GITEA_SSH_USER}@{GITEA_SSH_HOST}:{GITEA_SSH_PORT}"
        f"/{ADMIN_USER}/{repo_name}.git"
    )


def discover_ssh_keys(*, ssh_key=None) -> list[str]:
    """The operator's SSH public key(s) to register with Gitea.

    Precedence: an explicit ``--ssh-key`` file, else the keys loaded in the
    running ssh-agent (``ssh-add -L``), else every ``~/.ssh/*.pub``. Returns an
    empty list when nothing is found.
    """
    if ssh_key is not None:
        return [pathlib.Path(ssh_key).read_text().strip()]
    try:
        agent = subprocess.run(
            ["ssh-add", "-L"], capture_output=True, text=True
        )
        # A non-zero exit means no agent or no identities (the "has no
        # identities" notice goes to stdout with exit 1) -- never a real key.
        agent_keys = (
            [line for line in agent.stdout.splitlines() if line.strip()]
            if agent.returncode == 0
            else []
        )
    except OSError:
        agent_keys = []
    if agent_keys:
        return agent_keys
    ssh_dir = pathlib.Path.home() / ".ssh"
    return [p.read_text().strip() for p in sorted(ssh_dir.glob("*.pub"))]


def _key_title(public_key: str) -> str:
    # Gitea requires a unique title per key; derive a stable one from the key
    # body so re-runs and multiple keys never collide.
    digest = hashlib.sha256(public_key.encode()).hexdigest()[:12]
    return f"soliplex-template-{digest}"


def upload_ssh_key(public_key: str, *, password: str, title: str) -> None:
    """Register a public key on the admin account (idempotent)."""
    url = f"{GITEA_HTTP}/api/v1/user/keys"
    data = {"title": title, "key": public_key}
    try:
        with _request(
            "POST", url, user=ADMIN_USER, password=password, data=data
        ):
            pass
    except urllib.error.HTTPError as exc:
        # 422 == key (or title) already registered on the account; fine.
        if exc.code == 422:
            return
        body = exc.read().decode()
        raise KeyUploadFailed(exc.code, body) from exc


def _git(*args, project_dir, env=None):
    return subprocess.run(
        ["git", "-C", str(project_dir), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def current_branch(project_dir) -> str:
    """The name of the stack repo's current branch."""
    result = _git("rev-parse", "--abbrev-ref", "HEAD", project_dir=project_dir)
    result.check_returncode()
    return result.stdout.strip()


def set_origin(project_dir, url: str) -> None:
    """Point the stack repo's ``origin`` remote at ``url`` (add or update)."""
    existing = _git("remote", project_dir=project_dir)
    existing.check_returncode()
    verb = "set-url" if "origin" in existing.stdout.split() else "add"
    _git(
        "remote", verb, "origin", url, project_dir=project_dir
    ).check_returncode()


def configure_stack_ssh(project_dir) -> None:
    """Persist a stack-local ``known_hosts`` for git-over-SSH to this gitea.

    Every gitea SSH endpoint is ``localhost:2222`` (one stack can bind the host
    port at a time, and SSH has no Host-based routing), so pinning the gitea
    host key in the operator's ``~/.ssh/known_hosts`` makes each new stack
    collide with the previous one's key ("HOST IDENTIFICATION HAS CHANGED").

    Instead, route this repo's git-over-SSH through a known_hosts file under
    ``.git`` via ``core.sshCommand`` -- so pushes/pulls never read or write the
    global file. The file is cleared each run, so a recreated gitea (a new host
    key) is re-accepted on the next provision via ``accept-new``.
    """
    project = pathlib.Path(project_dir)
    known_hosts = project / ".git" / "gitea_known_hosts"
    known_hosts.unlink(missing_ok=True)
    ssh_command = (
        f'ssh -o UserKnownHostsFile="{known_hosts}" '
        "-o StrictHostKeyChecking=accept-new"
    )
    _git(
        "config", "core.sshCommand", ssh_command, project_dir=project
    ).check_returncode()


def push_initial(project_dir, branch: str) -> None:
    """Push ``branch`` to ``origin`` (SSH opts from ``core.sshCommand``)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = _git(
        "push", "-u", "origin", branch, project_dir=project_dir, env=env
    )
    if result.returncode != 0:
        raise PushFailed(result.stderr.strip())


def push_stack_to_gitea(
    project_dir, password, *, repo_name, ssh_key=None
) -> None:
    """Create a Gitea repo for the stack, set ``origin``, push the commit.

    Registers the operator's SSH key(s) on the admin account, creates an empty
    repo ``soliplex-admin/<repo_name>``, points the stack's ``origin`` at its
    SSH URL, and pushes the current branch. No token or password lands in the
    stack's git config -- the durable push credential is the operator's SSH
    key. git-over-SSH is routed through a stack-local ``known_hosts`` (see
    :func:`configure_stack_ssh`) so the global one is never touched.
    """
    project = pathlib.Path(project_dir)
    if not (project / ".git").is_dir():
        raise NotAGitRepo(project)
    keys = discover_ssh_keys(ssh_key=ssh_key)
    if not keys:
        raise No_SSH_hKeys()
    print(f"Registering {len(keys)} SSH key(s) with '{ADMIN_USER}' ...")
    for key in keys:
        upload_ssh_key(key, password=password, title=_key_title(key))
    print(f"Ensuring stack repository '{ADMIN_USER}/{repo_name}' ...")
    create_repo(password, name=repo_name, auto_init=False)
    url = stack_ssh_url(repo_name)
    print(f"Setting 'origin' to {url} ...")
    set_origin(project, url)
    configure_stack_ssh(project)
    branch = current_branch(project)
    print(f"Pushing '{branch}' to Gitea ...")
    push_initial(project, branch)


def set_env_var(env_path, key: str, value: str) -> None:
    """Set ``key=value`` in ``.env`` (replace the line, or append it)."""
    env_path = pathlib.Path(env_path)
    lines = env_path.read_text().splitlines() if env_path.is_file() else []
    prefix = f"{key}="
    out, replaced = [], False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n")


def _print_summary(webui_user=None, stack_repo=None) -> None:
    print()
    print("=== Gitea provisioned ===")
    print(f"  service account : {ADMIN_USER} (rotating; token in .env)")
    print(f"  repository      : {ADMIN_USER}/{REPO_NAME}")
    print("  GITEA_HOST / GITEA_ACCESS_TOKEN written to .env")
    if webui_user is not None:
        print(
            f"  web-UI admin    : {webui_user} "
            "(log in with the password you just set)"
        )
    if stack_repo is not None:
        print(
            f"  stack origin    : {stack_ssh_url(stack_repo)} "
            "(push with your SSH key)"
        )
    print()
    print("Restart the backend so it picks up the new '.env':")
    print("  docker compose up -d backend")


def provision_gitea(
    project_dir,
    *,
    webui_user=None,
    webui_password=None,
    push_to_gitea=False,
    stack_repo=None,
    ssh_key=None,
) -> None:
    """Provision the stack's Gitea: service account, token, repo, ``.env``.

    The ``soliplex-admin`` **service account** is transient: its password is
    created fresh on every run (rotating any existing one), used only
    in-process to mint the token and create the repo, then discarded -- it is
    never printed, persisted, or returned, so the operator cannot recover it.
    The durable credential is the minted access token, written -- with
    ``GITEA_HOST`` -- into the stack's ``.env`` for the backend to read.

    When ``webui_user`` is given (and must not be the service account), a
    *distinct* site-admin user is created-or-updated with the operator-supplied
    ``webui_password`` -- a stable, known login for the Gitea web UI. No token
    is minted for it and its password is not persisted by this script.

    When ``push_to_gitea`` is set, the stack's own git repository is backed by
    Gitea: the operator's SSH key(s) are registered on the service account, an
    empty repo ``soliplex-admin/<stack_repo>`` is created (``stack_repo``
    defaults to the stack directory name), the stack's ``origin`` is pointed at
    its SSH URL, and the current branch is pushed. ``ssh_key`` overrides the
    key auto-discovery. No secret is written to the stack's git config.

    Nothing is returned: the side effects (the updated ``.env`` and the Gitea
    accounts) are the result.
    """
    if webui_user == ADMIN_USER:
        raise ReservedUser(webui_user)
    project = pathlib.Path(project_dir).resolve()
    print("=== Gitea provisioning ===")
    print(f"Waiting for Gitea at {GITEA_HTTP} ...")
    if not wait_for_gitea():
        raise GiteaNotReady(GITEA_HTTP)

    password = generate_admin_password()
    print(f"Ensuring service account '{ADMIN_USER}' ...")
    ensure_admin_user(ADMIN_USER, ADMIN_EMAIL, password, project_dir=project)

    print("Minting access token ...")
    token = mint_token(password, token_name=f"concierge-{int(time.time())}")

    print(f"Ensuring repository '{ADMIN_USER}/{REPO_NAME}' ...")
    create_repo(password)

    if webui_user is not None:
        print(f"Ensuring web-UI admin user '{webui_user}' ...")
        ensure_admin_user(
            webui_user,
            f"{webui_user}@soliplex.localhost",
            webui_password,
            project_dir=project,
        )

    env_path = project / ENV_FILE
    set_env_var(env_path, "GITEA_HOST", GITEA_INTERNAL_URL)
    set_env_var(env_path, "GITEA_ACCESS_TOKEN", token)

    repo_name = None
    if push_to_gitea:
        repo_name = stack_repo or project.name
        push_stack_to_gitea(
            project, password, repo_name=repo_name, ssh_key=ssh_key
        )

    _print_summary(webui_user, repo_name)
