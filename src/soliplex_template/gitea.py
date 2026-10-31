"""Provision the generated stack's local Gitea: admin user, token, repo, .env.

This is the reusable core behind the generated project's bundled
``scripts/init_gitea.py`` -- a PEP 723 shim that ships in stacks with the
opt-in gitea service and delegates here. It ships in the published
``soliplex-template`` distribution so the shim can
``from soliplex_template.gitea import provision_gitea``.

Run it AFTER ``docker compose up -d`` (postgres, gitea and nginx up). It waits
for Gitea, ensures an admin user (via ``docker compose exec``), mints a scoped
access token, a tracking repository and the private provisioned-rooms
repository (over the REST API), and writes ``GITEA_HOST`` /
``GITEA_ACCESS_TOKEN`` into ``.env`` for downstream consumers. Idempotent:
re-running resets the admin password and reuses the existing repos.

It reaches Gitea's REST API from the host (see :func:`find_endpoints`):

- through Gitea's own published HTTP port, when ``gitea_http_port`` is set
- otherwise through nginx, which serves Gitea under ``/gitea/`` on its
  HTTPS port; TLS is verified against the stack's own (self-signed) nginx
  certificate alone

Stdlib only -- HTTP via :mod:`urllib.request`, the admin user and the stack's
configuration via ``subprocess`` (``docker compose``); no ``curl``/``openssl``.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import pathlib
import secrets
import ssl
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# The backend reaches Gitea over the compose network at the internal URL,
# which is what lands in .env.
GITEA_INTERNAL_URL = "http://gitea:3000"
# Gitea's ports inside its container (the rootless image).
GITEA_HTTP_PORT = 3000
GITEA_SSH_PORT = 2222
# The stack's self-signed certificate, inside the nginx container.
NGINX_CERT = "/etc/nginx/ssl/server.crt"

ADMIN_USER = "soliplex-admin"
ADMIN_EMAIL = "admin@soliplex.localhost"
REPO_NAME = "soliplex-requests"
# The provisioned-rooms clone's origin (see soliplex_template.provision).
ROOMS_REPO_NAME = "rooms-provisioned"

# Gitea's built-in SSH server: the login user is 'git'; the host port is the
# one 'gitea_ssh_port' publishes.
GITEA_SSH_USER = "git"
GITEA_SSH_HOST = "localhost"

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


class ComposeFailed(GiteaError):
    def __init__(self, command, stderr):
        self.command, self.stderr = command, stderr
        super().__init__(
            f"'docker compose {' '.join(command)}' failed: {stderr}"
        )


class NoGiteaService(GiteaError):
    def __init__(self):
        super().__init__(
            "the stack has no 'gitea' service (was it generated with "
            "include_gitea?)"
        )


class SshPortUnpublished(GiteaError):
    def __init__(self):
        super().__init__(
            "--push-to-gitea pushes over Gitea's SSH port, which the stack "
            "does not publish: set the 'gitea_ssh_port' generator parameter "
            "(or publish the gitea service's port 2222 in docker-compose.yml)"
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


@dataclasses.dataclass(frozen=True)
class Endpoints:
    """Where the host reaches the stack's Gitea.

    ``http`` is the base URL of Gitea's web root (its REST API is under
    ``/api/v1``), ``context`` the TLS context for it (None for plain HTTP),
    and ``ssh_port`` the host port publishing Gitea's SSH, if any.
    """

    http: str
    context: ssl.SSLContext | None = None
    ssh_port: int | None = None


def _compose(*args, project_dir):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )


def _compose_checked(*args, project_dir) -> str:
    result = _compose(*args, project_dir=project_dir)
    if result.returncode != 0:
        raise ComposeFailed(args, result.stderr.strip())
    return result.stdout


def _published(service: dict, target: int) -> int | None:
    """The host port publishing ``service``'s ``target`` port, if any."""
    for port in service.get("ports") or ():
        if int(port["target"]) == target and port.get("published"):
            return int(port["published"])
    return None


def pinned_context(project_dir) -> ssl.SSLContext:
    """A TLS context trusting the stack's own nginx certificate, alone.

    The certificate is copied out of the running nginx container. Its name
    is the stack's ``server_name`` while the host connects to ``localhost``,
    so the hostname is not checked: the pinned certificate is the check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / "server.crt"
        _compose_checked(
            "cp", f"nginx:{NGINX_CERT}", str(dest), project_dir=project_dir
        )
        pem = dest.read_text()
    context = ssl.create_default_context(cadata=pem)
    context.check_hostname = False
    return context


def find_endpoints(project_dir) -> Endpoints:
    """Where the host reaches the stack's Gitea, from its compose config.

    Through Gitea's own published HTTP port, when there is one; otherwise
    through nginx, at Gitea's ``ROOT_URL`` with ``localhost`` for its host
    (nginx publishes its HTTPS port on the host under the same number).
    """
    config = json.loads(
        _compose_checked("config", "--format", "json", project_dir=project_dir)
    )
    gitea = config["services"].get("gitea")
    if gitea is None:
        raise NoGiteaService()
    ssh_port = _published(gitea, GITEA_SSH_PORT)
    http_port = _published(gitea, GITEA_HTTP_PORT)
    if http_port is not None:
        return Endpoints(f"http://localhost:{http_port}", ssh_port=ssh_port)

    root = urllib.parse.urlsplit(
        gitea["environment"]["GITEA__server__ROOT_URL"]
    )
    https = root.scheme == "https"
    port = root.port or (443 if https else 80)
    return Endpoints(
        f"{root.scheme}://localhost:{port}{root.path.rstrip('/')}",
        context=pinned_context(project_dir) if https else None,
        ssh_port=ssh_port,
    )


def _request(
    method, url, *, user=None, password=None, data=None, context=None
):
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
    return urllib.request.urlopen(req, context=context)


def wait_for_gitea(
    endpoints: Endpoints,
    *,
    attempts: int = READY_ATTEMPTS,
    delay: int = READY_DELAY,
    sleep=time.sleep,
) -> bool:
    """Poll Gitea's ``/api/v1/version`` until it answers; True if ready."""
    url = f"{endpoints.http}/api/v1/version"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(
                url, context=endpoints.context
            ) as resp:
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


def mint_token(password: str, *, token_name: str, endpoints: Endpoints) -> str:
    """Mint a scoped access token for the admin user; return its sha1."""
    url = f"{endpoints.http}/api/v1/users/{ADMIN_USER}/tokens"
    data = {"name": token_name, "scopes": TOKEN_SCOPES}
    try:
        with _request(
            "POST",
            url,
            user=ADMIN_USER,
            password=password,
            data=data,
            context=endpoints.context,
        ) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        raise TokenRequestFailed(exc.code, body) from exc
    return parse_token(body)


def create_repo(
    password: str,
    *,
    endpoints: Endpoints,
    name: str = REPO_NAME,
    auto_init: bool = True,
    private: bool = False,
) -> int:
    """Create a repo owned by the admin user (idempotent: 201 or 409)."""
    url = f"{endpoints.http}/api/v1/user/repos"
    data = {"name": name, "auto_init": auto_init, "private": private}
    try:
        with _request(
            "POST",
            url,
            user=ADMIN_USER,
            password=password,
            data=data,
            context=endpoints.context,
        ) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        code = exc.code
    if code not in (201, 409):
        raise RepoCreationFailed(code)
    return code


def stack_ssh_url(repo_name: str, *, port: int) -> str:
    """The SSH clone URL for a repo owned by the admin service account.

    ``port`` is the host port publishing Gitea's SSH.
    """
    return (
        f"ssh://{GITEA_SSH_USER}@{GITEA_SSH_HOST}:{port}"
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


def upload_ssh_key(
    public_key: str, *, password: str, title: str, endpoints: Endpoints
) -> None:
    """Register a public key on the admin account (idempotent)."""
    url = f"{endpoints.http}/api/v1/user/keys"
    data = {"title": title, "key": public_key}
    try:
        with _request(
            "POST",
            url,
            user=ADMIN_USER,
            password=password,
            data=data,
            context=endpoints.context,
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

    Every gitea SSH endpoint is ``localhost:<gitea_ssh_port>`` (stacks reuse
    port numbers, and SSH has no Host-based routing), so pinning the gitea
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
    project_dir, password, *, repo_name, endpoints, ssh_key=None
) -> None:
    """Create a Gitea repo for the stack, set ``origin``, push the commit.

    Registers the operator's SSH key(s) on the admin account, creates an empty
    repo ``soliplex-admin/<repo_name>``, points the stack's ``origin`` at its
    SSH URL (on ``endpoints.ssh_port``), and pushes the current branch. No
    token or password lands in the stack's git config -- the durable push
    credential is the operator's SSH key. git-over-SSH is routed through a
    stack-local ``known_hosts`` (see :func:`configure_stack_ssh`) so the
    global one is never touched.
    """
    project = pathlib.Path(project_dir)
    if not (project / ".git").is_dir():
        raise NotAGitRepo(project)
    keys = discover_ssh_keys(ssh_key=ssh_key)
    if not keys:
        raise No_SSH_hKeys()
    print(f"Registering {len(keys)} SSH key(s) with '{ADMIN_USER}' ...")
    for key in keys:
        upload_ssh_key(
            key, password=password, title=_key_title(key), endpoints=endpoints
        )
    print(f"Ensuring stack repository '{ADMIN_USER}/{repo_name}' ...")
    create_repo(password, endpoints=endpoints, name=repo_name, auto_init=False)
    url = stack_ssh_url(repo_name, port=endpoints.ssh_port)
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


def _print_summary(webui_user=None, stack_origin=None) -> None:
    print()
    print("=== Gitea provisioned ===")
    print(f"  service account : {ADMIN_USER} (rotating; token in .env)")
    print(f"  repository      : {ADMIN_USER}/{REPO_NAME}")
    print(f"  rooms repository: {ADMIN_USER}/{ROOMS_REPO_NAME} (private)")
    print("  GITEA_HOST / GITEA_ACCESS_TOKEN written to .env")
    if webui_user is not None:
        print(
            f"  web-UI admin    : {webui_user} "
            "(log in with the password you just set)"
        )
    if stack_origin is not None:
        print(f"  stack origin    : {stack_origin} (push with your SSH key)")
    print()
    print("Recreate the services that read '.env' so they pick it up:")
    print("  docker compose up -d backend soliplex-provisioner")


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

    Gitea is reached as :func:`find_endpoints` says. ``push_to_gitea`` needs
    Gitea's SSH port published (``gitea_ssh_port``); without it, nothing is
    changed. When ``push_to_gitea`` is set, the stack's own git repository is
    backed by Gitea: the operator's SSH key(s) are registered on the service
    account, an empty repo ``soliplex-admin/<stack_repo>`` is created
    (``stack_repo`` defaults to the stack directory name), the stack's
    ``origin`` is pointed at its SSH URL, and the current branch is pushed.
    ``ssh_key`` overrides the key auto-discovery. No secret is written to the
    stack's git config.

    Nothing is returned: the side effects (the updated ``.env`` and the Gitea
    accounts) are the result.
    """
    if webui_user == ADMIN_USER:
        raise ReservedUser(webui_user)
    project = pathlib.Path(project_dir).resolve()
    endpoints = find_endpoints(project)
    if push_to_gitea and endpoints.ssh_port is None:
        raise SshPortUnpublished()
    print("=== Gitea provisioning ===")
    print(f"Waiting for Gitea at {endpoints.http} ...")
    if not wait_for_gitea(endpoints):
        raise GiteaNotReady(endpoints.http)

    password = generate_admin_password()
    print(f"Ensuring service account '{ADMIN_USER}' ...")
    ensure_admin_user(ADMIN_USER, ADMIN_EMAIL, password, project_dir=project)

    print("Minting access token ...")
    token = mint_token(
        password,
        token_name=f"concierge-{int(time.time())}",
        endpoints=endpoints,
    )

    print(f"Ensuring repository '{ADMIN_USER}/{REPO_NAME}' ...")
    create_repo(password, endpoints=endpoints)

    print(f"Ensuring repository '{ADMIN_USER}/{ROOMS_REPO_NAME}' ...")
    create_repo(
        password,
        endpoints=endpoints,
        name=ROOMS_REPO_NAME,
        auto_init=False,
        private=True,
    )

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

    stack_origin = None
    if push_to_gitea:
        repo_name = stack_repo or project.name
        push_stack_to_gitea(
            project,
            password,
            repo_name=repo_name,
            endpoints=endpoints,
            ssh_key=ssh_key,
        )
        stack_origin = stack_ssh_url(repo_name, port=endpoints.ssh_port)

    _print_summary(webui_user, stack_origin)
