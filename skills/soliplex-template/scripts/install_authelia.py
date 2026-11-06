# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml",
#   "mako",
# ]
# ///
"""Install Authelia as an in-stack OIDC provider for a Soliplex stack.

Run from the stack directory (the one containing ``docker-compose.yml``):

    python3 scripts/install_authelia.py --admin-email admin@example.local

The command is idempotent: re-running on a stack that already has Authelia
will refresh the provider entry and secrets, but will not recreate users.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import secrets
import string
import subprocess
import sys
import tomllib
from typing import Any

import yaml

# Authelia release verified to issue RS256 access tokens for a public
# ``soliplex`` client and to accept the filesystem notifier for self-service
# identity confirmation.
AUTHELIA_IMAGE = "authelia/authelia:4.39.28"

# Files written / edited by this script, relative to the stack root.
COMPOSE_FRAGMENT = pathlib.PurePosixPath("compose.authelia.yaml")
AUTHELIA_CONFIG = pathlib.PurePosixPath("authelia/configuration.yml")
AUTHELIA_USERS = pathlib.PurePosixPath("authelia/users.yml")
AUTHELIA_IDENTIFIERS = pathlib.PurePosixPath("authelia/identifiers.yml")
AUTHELIA_NOTIFICATION = pathlib.PurePosixPath("authelia/notification.txt")
NGINX_FRAGMENT = pathlib.PurePosixPath("nginx/conf.d/authelia.conf")
DOCKER_COMPOSE = pathlib.PurePosixPath("docker-compose.yml")
CACERT_PEM = pathlib.PurePosixPath("backend/environment/oidc/cacert.pem")
OIDC_CONFIG = pathlib.PurePosixPath("backend/environment/oidc/config.yaml")
ENV_FILE = pathlib.PurePosixPath(".env")
SECRETS_DIR = pathlib.PurePosixPath(".secrets")
INITD_SCRIPT = pathlib.PurePosixPath("stack-init/init.d/authelia_db.py")

# Compose secrets declared by the Authelia service fragment, generated as
# random passwords and reached through 'AUTHELIA_*_FILE' environment
# variables (the configuration.yml holds no matching key: defining it in
# both sources is rejected).
AUTHELIA_SECRET_NAMES = (
    "authelia_session_secret",
    "authelia_storage_encryption_key",
    "authelia_storage_password",
    "authelia_oidc_hmac_secret",
    "authelia_reset_password_jwt_secret",
)

_ALPHANUM = string.ascii_letters + string.digits


class AutheliaError(Exception):
    """A user-facing error (printed without a traceback)."""


class StackNotFound(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"not a Soliplex stack: {path} (no docker-compose.yml)"
        )


class ManifestNotFound(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"no generation manifest at {path}")


class EnvNotFound(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"no .env at {path}; run the generator first")


class MissingEnvValue(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"{path}: PUID and/or PGID missing")


class MissingCACert(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"{path}: OIDC CA trust list missing; run the generator"
        )


class BadComposeFile(AutheliaError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"{path}: cannot find 'name:' line")


class DockerCommandFailed(AutheliaError):
    def __init__(self, cmd, returncode, stderr):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"command failed ({returncode}): {' '.join(cmd)}\n{stderr}"
        )


@dataclasses.dataclass(frozen=True)
class StackParams:
    """Parameters pulled from the stack's generation manifest and .env."""

    project_name: str
    server_name: str
    nginx_https: int
    puid: int
    pgid: int
    ollama_base_url: str


def generate_password(length: int = 32) -> str:
    """A cryptographically-random ``[A-Za-z0-9]`` password of ``length``."""
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def load_manifest(stack: pathlib.Path) -> dict:
    """Read the generation manifest table from pyproject.toml."""
    path = stack / "pyproject.toml"
    if not path.is_file():
        raise ManifestNotFound(path)
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    try:
        return data["tool"]["soliplex-template"]["params"]
    except KeyError as exc:
        raise ManifestNotFound(path) from exc


def discover_puid_pgid(stack: pathlib.Path) -> tuple[int, int]:
    """Return ``(PUID, PGID)`` from the stack's ``.env``."""
    path = stack / ENV_FILE
    if not path.is_file():
        raise EnvNotFound(path)
    puid = pgid = None
    for line in path.read_text().splitlines():
        if line.startswith("PUID="):
            puid = int(line[len("PUID=") :].strip())
        elif line.startswith("PGID="):
            pgid = int(line[len("PGID=") :].strip())
    if puid is None or pgid is None:
        raise MissingEnvValue(path)
    return puid, pgid


def resolve_params(stack: pathlib.Path) -> StackParams:
    """Combine the generation manifest with ``.env`` runtime values."""
    manifest = load_manifest(stack)
    puid, pgid = discover_puid_pgid(stack)
    return StackParams(
        project_name=str(manifest["project_name"]),
        server_name=str(
            manifest.get("server_name")
            or f"{manifest['project_name']}.localhost"
        ),
        nginx_https=int(manifest["nginx_https"]),
        puid=puid,
        pgid=pgid,
        ollama_base_url=str(manifest["ollama_base_url"]),
    )


def run_cmd(
    cmd: list[str],
    *,
    cwd: pathlib.Path | None = None,
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing stderr on failure."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture or check,
        text=True,
        input=input_text,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise DockerCommandFailed(
            cmd, proc.returncode, proc.stderr or proc.stdout or ""
        )
    return proc


def generate_issuer_key(stack: pathlib.Path) -> tuple[str, str, str]:
    """Return ``(private_key_pem, public_key_pem, cert_pem)``.

    The private key is the OIDC issuer's RSA signing key, wired into
    Authelia's ``jwks`` through a ``file:`` value. An existing key pair is
    preserved, so re-running does not rotate the issuer key or the
    corresponding provider entry.
    """
    key_path = stack / SECRETS_DIR / "authelia_oidc_issuer_private_key.gen"
    cert_path = stack / SECRETS_DIR / "authelia_oidc_issuer_certificate.pem"
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.is_file() and cert_path.is_file():
        private_key = key_path.read_text()
        cert_pem = cert_path.read_text()
        return private_key, _cert_public_key(stack, cert_path), cert_pem

    run_cmd(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-days",
            "365",
            "-newkey",
            "rsa:4096",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-subj",
            f"/CN={stack.name}-authelia",
        ],
        cwd=stack,
    )

    private_key = key_path.read_text()
    cert_pem = cert_path.read_text()

    # Tighten permissions on the generated files before docker secrets
    # sees them.
    key_path.chmod(0o600)
    cert_path.chmod(0o600)

    return private_key, _cert_public_key(stack, cert_path), cert_pem


def generate_tls_key(stack: pathlib.Path, server_name: str) -> str:
    """Generate (or return) the path of Authelia's server TLS key.

    Authelia serves its own TLS on the issuer's port, so the backend can
    reach it directly; the matching certificate is appended to the
    backend's OIDC CA trust list (see :func:`append_cert_to_cacert`).
    An existing key pair is preserved.
    """
    key_path = stack / SECRETS_DIR / "authelia_tls_key.gen"
    cert_path = stack / SECRETS_DIR / "authelia_tls_certificate.pem"
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if not (key_path.is_file() and cert_path.is_file()):
        run_cmd(
            [
                "openssl",
                "req",
                "-x509",
                "-nodes",
                "-days",
                "365",
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-subj",
                f"/CN={server_name}",
                "-addext",
                f"subjectAltName = DNS:{server_name}",
            ],
            cwd=stack,
        )
        key_path.chmod(0o600)
        cert_path.chmod(0o600)

    return str(key_path)


def _cert_public_key(stack: pathlib.Path, cert_path: pathlib.Path) -> str:
    """The public key PEM held by an issuer certificate."""
    proc = run_cmd(
        ["openssl", "x509", "-in", str(cert_path), "-pubkey", "-noout"],
        cwd=stack,
        capture=True,
    )
    return proc.stdout


def ensure_authelia_secrets(stack: pathlib.Path) -> dict[str, pathlib.Path]:
    """Generate Authelia's random secrets if they are missing.

    Existing secrets are preserved so re-running does not invalidate a
    running Authelia deployment.
    """
    secret_paths: dict[str, pathlib.Path] = {}
    for name in AUTHELIA_SECRET_NAMES:
        path = stack / SECRETS_DIR / f"{name}.gen"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text(generate_password(64))
            path.chmod(0o600)
        secret_paths[name] = path
    return secret_paths


def append_cert_to_cacert(stack: pathlib.Path, cert_pem: str) -> None:
    """Append Authelia's server certificate to the backend trust list."""
    path = stack / CACERT_PEM
    if not path.is_file():
        raise MissingCACert(path)
    text = path.read_text()
    marker = "# Authelia server certificate"
    if marker in text:
        # Replace the existing block instead of appending duplicates.
        text = re.sub(
            re.escape(marker) + r".*?" + re.escape(marker + " end"),
            "",
            text,
            flags=re.DOTALL,
        )
    block = f"\n{marker}\n{cert_pem.strip()}\n{marker} end\n"
    path.write_text(text.rstrip() + "\n" + block)


class _Literal(str):
    """A string to dump as a YAML literal block ('|')."""


yaml.add_representer(
    _Literal,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|"
    ),
)


def _compose_fragment(params: StackParams) -> dict[str, Any]:
    """The Authelia service fragment as a data structure."""
    return {
        "services": {
            "authelia": {
                "image": AUTHELIA_IMAGE,
                "user": f"{params.puid}:{params.pgid}",
                "restart": "unless-stopped",
                # The issuer URL must resolve on the compose network, so
                # the backend reaches it directly (self-signed TLS);
                # browsers reach it through nginx.
                "networks": {"default": {"aliases": [params.server_name]}},
                "depends_on": {"postgres": {"condition": "service_healthy"}},
                "secrets": [
                    *AUTHELIA_SECRET_NAMES,
                    "authelia_oidc_issuer_private_key",
                    "authelia_tls_certificate",
                    "authelia_tls_key",
                ],
                "environment": {
                    "PUID": params.puid,
                    "PGID": params.pgid,
                    "X_AUTHELIA_CONFIG_FILTERS": "template",
                    "AUTHELIA_SESSION_SECRET_FILE": (
                        "/run/secrets/authelia_session_secret"
                    ),
                    "AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE": (
                        "/run/secrets/authelia_storage_encryption_key"
                    ),
                    "AUTHELIA_STORAGE_POSTGRES_PASSWORD_FILE": (
                        "/run/secrets/authelia_storage_password"
                    ),
                    "AUTHELIA_IDENTITY_PROVIDERS_OIDC_HMAC_SECRET_FILE": (
                        "/run/secrets/authelia_oidc_hmac_secret"
                    ),
                    "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE": (  # noqa: E501
                        "/run/secrets/authelia_reset_password_jwt_secret"
                    ),
                    "AUTHELIA_STORAGE_POSTGRES_ADDRESS": "tcp://postgres:5432",
                    "AUTHELIA_STORAGE_POSTGRES_DATABASE": "soliplex_authelia",
                    "AUTHELIA_STORAGE_POSTGRES_USERNAME": "soliplex_authelia",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": "./authelia",
                        "target": "/config",
                        "read_only": False,
                    }
                ],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "wget",
                        "--no-verbose",
                        "--tries=1",
                        "--spider",
                        "--no-check-certificate",
                        "https://localhost:9443/api/health",
                    ],
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                    "start_period": "60s",
                },
            }
        },
        "secrets": {
            **{
                name: {"file": f"./.secrets/{name}.gen"}
                for name in AUTHELIA_SECRET_NAMES
            },
            "authelia_oidc_issuer_private_key": {
                "file": "./.secrets/authelia_oidc_issuer_private_key.gen"
            },
            "authelia_tls_certificate": {
                "file": "./.secrets/authelia_tls_certificate.pem"
            },
            "authelia_tls_key": {"file": "./.secrets/authelia_tls_key.gen"},
        },
    }


def write_compose_fragment(stack: pathlib.Path, params: StackParams) -> None:
    """Write the Authelia service fragment."""
    path = stack / COMPOSE_FRAGMENT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            _compose_fragment(params),
            sort_keys=False,
            default_flow_style=False,
        )
    )


_CONFIG_EXPRESSION = (
    '{{ secret "/run/secrets/authelia_oidc_issuer_private_key" '
    '| mindent 10 "|" | msquote }}'
)


_AUTHELIA_CONFIG_TEMPLATE = """\
# Authelia configuration for the Soliplex in-stack OIDC provider.
#
# Random secrets (session, storage password, encryption key, hmac,
# reset-password JWT) are reached through 'AUTHELIA_*_FILE' environment
# variables declared in 'compose.authelia.yaml': they are absent here, and
# defining a secret in more than one source is rejected by Authelia.
server:
  address: 'tcp://:9443/authelia'
  disable_healthcheck: false
  tls:
    # Paths of the Docker secrets holding the server's own TLS material;
    # the certificate is also appended to the backend's OIDC CA trust list
    # so it can reach the issuer directly.
    key: '/run/secrets/authelia_tls_key'
    certificate: '/run/secrets/authelia_tls_certificate'
log:
  level: info
  format: text
default_2fa_method: ''
theme: auto
identity_validation:
  reset_password:
    # 'jwt_secret' is another env-only secret.
    jwt_lifespan: '5 minutes'
authentication_backend:
  file:
    path: /config/users.yml
    watch: true
password_policy:
  standard:
    enabled: true
    min_length: 8
    max_length: 0
    require_uppercase: true
    require_lowercase: true
    require_number: true
    require_special: true
access_control:
  default_policy: one_factor
  networks:
    - name: internal
      networks:
        - '172.0.0.0/8'
  rules:
    - domain: '{server_name}'
      policy: one_factor
session:
  name: authelia_session
  domain: '{server_name}'
  same_site: lax
  inactivity: '1 hour'
  expiration: '1 day'
  remember_me: '1 month'
regulation:
  max_retries: 5
  find_time: '2 minutes'
  ban_time: '5 minutes'
storage:
  postgres:
    address: tcp://postgres:5432
    database: soliplex_authelia
    schema: public
    username: soliplex_authelia
    timeout: '5 seconds'
notifier:
  filesystem:
    filename: /config/notification.txt
identity_providers:
  oidc:
    jwks:
      - key: {jwks_key_expression}
    enable_client_debug_messages: false
    minimum_parameter_entropy: 8
    claims_policies:
      soliplex:
        id_token: [email, name, preferred_username]
        access_token: [email, name, preferred_username]
    clients:
      - client_id: soliplex
        client_name: Soliplex
        client_secret: ''
        public: true
        consent_mode: implicit
        scopes:
          - openid
          - email
          - profile
        redirect_uris:
          - 'https://{server_name}:{nginx_https}/api/auth/authelia'
        response_types:
          - code
        token_endpoint_auth_method: 'none'
        pkce_challenge_method: 'S256'
        access_token_signed_response_alg: 'RS256'
        claims_policy: 'soliplex'
        authorization_policy: 'one_factor'
"""


def write_authelia_config(stack: pathlib.Path, params: StackParams) -> None:
    """Write Authelia's ``configuration.yml``."""
    path = stack / AUTHELIA_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _AUTHELIA_CONFIG_TEMPLATE.format(
            jwks_key_expression=_CONFIG_EXPRESSION, **params.__dict__
        )
    )


_NGINX_FRAGMENT_TEMPLATE = """\
# Proxy the in-stack Authelia OIDC provider at /authelia/. Authelia serves
# its own TLS (see compose.authelia.yaml); nginx does not verify the
# upstream certificate. X-Forwarded-Proto is set to $scheme explicitly:
# Authelia refuses an http issuer, and the stack's $backend_proto map
# defaults to http for direct requests even on the HTTPS port.
location /authelia/ {
    proxy_pass https://authelia:9443/authelia/;
    proxy_http_version 1.1;
    proxy_ssl_server_name on;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Method $request_method;
    proxy_set_header X-Forwarded-Uri $request_uri;
    proxy_connect_timeout 10s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
}
"""


def write_nginx_fragment(stack: pathlib.Path) -> None:
    """Write the nginx route fragment for ``/authelia/``."""
    path = stack / NGINX_FRAGMENT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_NGINX_FRAGMENT_TEMPLATE)


def _discover_postgres_user(stack: pathlib.Path) -> str:
    """The compose-declared ``POSTGRES_USER`` ('postgres' default)."""
    for line in (stack / DOCKER_COMPOSE).read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("POSTGRES_USER:"):
            value = stripped[len("POSTGRES_USER:") :].strip().strip("\"'")
            if value:
                return value
    return "postgres"


def create_authelia_database(stack: pathlib.Path) -> None:
    """Create the Authelia Postgres role and database via the running service.

    ``postgres/config/init.sh`` only runs on a fresh volume, so a stack that
    already had its Postgres initialised needs this explicit step. The
    superuser password rides through ``exec -e PGPASSWORD``: the stack's
    initdb arguments turn SCRAM on for local sockets too, so TCP with the
    password beats a password prompt that gets EOF under ``-T``.
    """
    postgres_password = (
        (stack / SECRETS_DIR / "postgres_password.gen").read_text().strip()
    )
    authelia_password = (
        (stack / SECRETS_DIR / "authelia_storage_password.gen")
        .read_text()
        .strip()
    )

    safe_password = authelia_password.replace("'", "''")
    sql_lines = [
        "DO $$",
        "BEGIN",
        (
            "    CREATE USER soliplex_authelia "
            f"WITH PASSWORD '{safe_password}';"
        ),
        "EXCEPTION WHEN duplicate_object THEN",
        (f"    ALTER USER soliplex_authelia WITH PASSWORD '{safe_password}';"),
        "END $$;",
        (
            "SELECT 'CREATE DATABASE soliplex_authelia "
            "OWNER soliplex_authelia' "
            "WHERE NOT EXISTS ("
            "SELECT FROM pg_database WHERE datname = 'soliplex_authelia'"
            ")"
        ),
        "\\gexec",
    ]
    run_cmd(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={postgres_password}",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "localhost",
            "-U",
            _discover_postgres_user(stack),
            "-d",
            "postgres",
        ],
        cwd=stack,
        input_text="\n".join(sql_lines),
    )


_INCLUDE_LINE = "include:\n  - compose.authelia.yaml\n"


def add_include_to_compose(stack: pathlib.Path) -> None:
    """Add the top-level ``include:`` block to ``docker-compose.yml``."""
    path = stack / DOCKER_COMPOSE
    text = path.read_text()
    if "compose.authelia.yaml" in text:
        return
    # Insert after the leading comment block and ``name:`` line.
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("name:"):
            insert_at = i + 1
            break
    if insert_at == 0:
        raise BadComposeFile(path)
    lines.insert(insert_at, "\n" + _INCLUDE_LINE)
    path.write_text("".join(lines))


def add_provider_entry(
    stack: pathlib.Path, params: StackParams, public_key_pem: str
) -> None:
    """Add the Authelia ``auth_systems`` entry via ``oidc_providers.py``."""
    # The provider-entry writer is a sibling script in the skill bundle.
    module_dir = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(module_dir))
    try:
        import oidc_providers
    finally:
        sys.path.pop(0)

    provider = oidc_providers.Provider(
        id="authelia",
        title="Authelia",
        server_url=f"https://{params.server_name}:{params.nginx_https}/authelia",
        client_id="soliplex",
        token_validation_pem=public_key_pem,
        scope="openid email profile",
        client_secret="",
    )
    try:
        oidc_providers.add_provider(stack, provider, use_for_signin=True)
    except oidc_providers.ProviderExists:
        # Already registered, and the signing key is preserved, so the
        # existing entry matches what this run would write.
        print("  Authelia is already registered as a provider; reusing it")


def run_stack_init(stack: pathlib.Path) -> None:
    """Run the stack-init one-shot to create the Authelia database."""
    run_cmd(
        ["docker", "compose", "run", "--rm", "--quiet-build", "stack-init"],
        cwd=stack,
    )


def start_authelia(stack: pathlib.Path) -> None:
    """Start (or restart) Authelia and wait for it to be healthy."""
    run_cmd(
        ["docker", "compose", "up", "-d", "--wait", "authelia"],
        cwd=stack,
    )


def create_admin_user(stack: pathlib.Path, email: str) -> tuple[str, str]:
    """Create the first Authelia admin by driving ``authelia_users.py``.

    The sibling script runs in a child process with the same interpreter;
    ``add --quiet`` prints a JSON summary, whose ``sub`` and
    ``password_file`` this returns.
    """
    script = pathlib.Path(__file__).resolve().parent / "authelia_users.py"
    proc = run_cmd(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(stack),
            "add",
            "--admin",
            "--email",
            email,
            "--quiet",
        ],
        cwd=stack,
        capture=True,
    )
    summary = json.loads(proc.stdout)
    return summary["sub"], summary["password_file"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_authelia.py",
        description="Install Authelia as the in-stack OIDC provider.",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="the stack directory (default: current directory)",
    )
    parser.add_argument(
        "--admin-email",
        required=True,
        help="email address for the first Authelia admin user",
    )
    parser.add_argument(
        "--no-up",
        action="store_true",
        help="write configuration but do not start services or create users",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    stack = pathlib.Path(args.project_dir).resolve()
    if not (stack / DOCKER_COMPOSE).is_file():
        raise StackNotFound(stack)

    params = resolve_params(stack)
    print(
        f"Installing Authelia for {params.project_name} ({params.server_name})"
    )

    print("  generating secrets and signing key...")
    ensure_authelia_secrets(stack)
    _, public_key_pem, cert_pem = generate_issuer_key(stack)
    generate_tls_key(stack, params.server_name)

    print("  writing Authelia service and configuration...")
    write_compose_fragment(stack, params)
    write_authelia_config(stack, params)
    write_nginx_fragment(stack)
    tls_cert = (
        stack / SECRETS_DIR / "authelia_tls_certificate.pem"
    ).read_text()
    append_cert_to_cacert(stack, tls_cert)
    add_include_to_compose(stack)

    print("  registering Authelia as the OIDC provider...")
    add_provider_entry(stack, params, public_key_pem)

    if args.no_up:
        print("\nAuthelia configuration written. Run:")
        print("  docker compose run --rm --quiet-build stack-init")
        print("  docker compose up -d")
        print(
            "  python3 scripts/authelia_users.py add --admin --email "
            f"{args.admin_email}"
        )
        return 0

    print("  running stack-init to bring Soliplex databases current...")
    run_stack_init(stack)

    print("  creating the Authelia Postgres role and database...")
    create_authelia_database(stack)

    print("  starting Authelia...")
    start_authelia(stack)

    print(f"  creating admin user {args.admin_email!r}...")
    sub, password_file = create_admin_user(stack, args.admin_email)
    print("\nAuthelia is installed and signing in is required.")
    print(f"Admin user: {args.admin_email}")
    print(f"Authelia 'sub': {sub}")
    print(f"Password file: {password_file}")
    print("\nNext steps:")
    print("  docker compose restart nginx")
    print("  give the password file contents to the admin out of band")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AutheliaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
