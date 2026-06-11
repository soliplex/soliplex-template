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
import json
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

    @classmethod
    def not_ready(cls, url):
        return cls(
            f"Gitea did not become ready at {url} "
            "(is the stack up? 'docker compose up -d gitea')"
        )

    @classmethod
    def reserved_user(cls, name):
        return cls(
            f"web-UI admin user may not be {name!r}: it is the rotating "
            "service account"
        )

    @classmethod
    def password_mismatch(cls):
        return cls("passwords did not match")

    @classmethod
    def token_request_failed(cls, code, body):
        return cls(f"token request failed: HTTP {code}: {body}")

    @classmethod
    def no_token(cls, body):
        return cls(f"could not parse token from response: {body}")

    @classmethod
    def repo_failed(cls, code):
        return cls(f"repo creation failed (HTTP {code})")


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
        raise GiteaError.no_token(body) from exc
    token = data.get("sha1")
    if not token:
        raise GiteaError.no_token(body)
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
        raise GiteaError.token_request_failed(exc.code, body) from exc
    return parse_token(body)


def create_repo(password: str) -> int:
    """Create the tracking repo (idempotent: 201 created or 409 exists)."""
    url = f"{GITEA_HTTP}/api/v1/user/repos"
    data = {"name": REPO_NAME, "auto_init": True, "private": False}
    try:
        with _request(
            "POST", url, user=ADMIN_USER, password=password, data=data
        ) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        code = exc.code
    if code not in (201, 409):
        raise GiteaError.repo_failed(code)
    return code


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


def _print_summary(webui_user=None) -> None:
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
    print()
    print("Restart the backend so it picks up the new '.env':")
    print("  docker compose up -d backend")


def provision_gitea(
    project_dir, *, webui_user=None, webui_password=None
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

    Nothing is returned: the side effects (the updated ``.env`` and the Gitea
    accounts) are the result.
    """
    if webui_user == ADMIN_USER:
        raise GiteaError.reserved_user(webui_user)
    project = pathlib.Path(project_dir).resolve()
    print("=== Gitea provisioning ===")
    print(f"Waiting for Gitea at {GITEA_HTTP} ...")
    if not wait_for_gitea():
        raise GiteaError.not_ready(GITEA_HTTP)

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

    _print_summary(webui_user)
