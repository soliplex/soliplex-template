"""Generate the Docker secrets a Soliplex stack needs at first-time setup.

This is the reusable core behind the generated project's bundled
``scripts/generate_secrets.py`` -- a PEP 723 shim that ships in every stack and
delegates here. It ships in the published ``soliplex-template`` distribution so
the skill's project generator (and the shim) can import it as:
``from soliplex_template.secrets import generate_secrets``.

``generate_secrets`` builds the list of the stack's secrets by scanning
the stack's ``docker-compose.yml`` for the ``*.gen`` secret files
declared under ``secrets:``.

Its default behavior is to identify those secrets which are *missing*
(i.e., have no corresponding ``.secrets/*.gen`` file): for each missing
secret, the function writes a fresh random password, at mode ``0600``.

The function never replaces ("rotates") existing secrets implicitly: only
when explicitly requested by passing :data:`ROTATE_ALL` or a sequence of
secret names.

Some of those existing secrets are Postgres role passwords, initialized
via ``*_PASS_FILE`` compose entries passed to initdb.  Rotating any of
these secrets prints the ``ALTER ROLE`` commands matching the roles it
invalidated.

Written files are also re-owned to the ``PUID:PGID`` from ``.env`` (default
``1000:1000``) -- the uid/gid the built images run as, which must read the
files at mode ``0600`` when Compose bind-mounts them into the containers.
Re-owning to an arbitrary uid may need more privilege than the operator
has, so it runs in a throwaway root ``docker`` container; if docker is
unavailable, the function warns and leaves the owner alone.

Stdlib only -- randomness via :mod:`secrets`, no ``openssl``/``bash``.
"""

from __future__ import annotations

import collections.abc
import os
import pathlib
import re
import secrets
import shutil
import string
import subprocess

# Password shape: 32 chars from [A-Za-z0-9] (mirrors the old shell generator's
# `openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32`).
PASSWORD_LENGTH = 32
PASSWORD_ALPHABET = string.ascii_letters + string.digits

# UID/GID the built images run as (and that must own the secret files); read
# from the stack's .env, falling back to these when unset.
DEFAULT_PUID = "1000"
DEFAULT_PGID = "1000"

COMPOSE_FILE = "docker-compose.yml"
SECRETS_DIR = ".secrets"
ENV_FILE = ".env"
GEN_SUFFIX = ".gen"

# Sentinel asking generate_secrets() to rotate every declared secret.
ROTATE_ALL = object()

# Compose mount point of the Postgres init scripts.
_INITDB_TARGET = "/docker-entrypoint-initdb.d"

# init-script role creation: 'CREATE USER <role> WITH PASSWORD '$<VAR>''.
_CREATE_USER_RE = re.compile(
    r"CREATE USER\s+(\S+)\s+WITH PASSWORD\s+'\$\{?(\w+)\}?'"
)


class SecretsError(Exception):
    """A user-facing error (printed without a traceback)."""


class ComposeNotFound(SecretsError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"cannot find compose file at: {path} "
            "(run with --project-dir pointing at the stack directory)"
        )


class UnknownSecrets(SecretsError):
    def __init__(self, unknown, declared):
        self.unknown = [str(name) for name in unknown]
        self.declared = sorted(declared)
        super().__init__(
            "unknown secret(s): "
            + ", ".join(self.unknown)
            + " -- declared secrets: "
            + ", ".join(self.declared)
        )


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """A cryptographically-random ``[A-Za-z0-9]`` password of ``length``."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def discover_secret_files(
    compose_text: str,
) -> collections.abc.Iterator[str]:
    """Yield the ``*.gen`` secret paths declared in a compose file.

    Mirrors the old shell scan: lines of the form ``<indent>file: <path>``
    whose path ends in ``.gen``, with any leading ``./`` stripped.
    """
    for line in compose_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("file:"):
            value = stripped[len("file:") :].strip()
            if value.endswith(GEN_SUFFIX):
                yield value.removeprefix("./")


def discover_db_pass_vars(
    compose_text: str,
) -> dict[str, str]:
    """Map ``*.gen`` secret basenames to their ``*_PASS_FILE`` var stems.

    The generated stack wires each Postgres role password into the
    ``postgres`` service as ``<VAR>_PASS_FILE: /run/secrets/<base>``
    (e.g. ``AGUI_DB_PASS_FILE``); the stem (``AGUI_DB_PASS``) is the
    variable the init scripts read that role's password from.
    """
    wired: dict[str, str] = {}
    pattern = re.compile(r"^(\w+_(?:PASS|PASSWORD)_FILE):\s*(\S+)\s*$")
    for line in compose_text.splitlines():
        match = pattern.match(line.strip())
        if match is None:
            continue
        varname, value = match.groups()
        base = value.strip("\"'").rpartition("/")[2]
        wired[base + GEN_SUFFIX] = varname.removesuffix("_FILE")
    return wired


def discover_initdb_dir(compose_text: str) -> str | None:
    """The compose bind source mounted to ``/docker-entrypoint-initdb.d``."""
    source_line: tuple[int, str] | None = None
    for lineno, line in enumerate(compose_text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("source:"):
            value = stripped[len("source:") :].strip()
            source_line = (lineno, value.strip("\"'"))
            continue
        if (
            source_line is not None
            and lineno - source_line[0] <= 2
            and stripped.startswith("target:")
            and stripped[len("target:") :].strip().strip("\"'")
            == _INITDB_TARGET
        ):
            return source_line[1].removeprefix("./")
    return None


def discover_init_roles(init_text: str) -> dict[str, str]:
    """Map init-script password variables to the roles they seed."""
    roles: dict[str, str] = {}
    for match in _CREATE_USER_RE.finditer(init_text):
        role, var = match.groups()
        roles[var] = role
    return roles


def discover_secret_roles(
    compose_text: str,
    project_dir,
) -> dict[str, str]:
    """Map ``*.gen`` secret basenames to the Postgres role they seed.

    Joins two wirings: the compose ``<VAR>_PASS_FILE: /run/secrets/<base>``
    entries and the ``CREATE USER <role> WITH PASSWORD '$<VAR>'`` lines of
    the init scripts mounted at ``/docker-entrypoint-initdb.d``. The
    superuser's ``POSTGRES_PASSWORD_FILE`` is consumed by the postgres
    entrypoint instead of the init scripts; its role is the value of
    ``POSTGRES_USER``.
    """
    wired = discover_db_pass_vars(compose_text)
    roles: dict[str, str] = {}
    init_dir = discover_initdb_dir(compose_text)
    if init_dir is not None:
        init_root = pathlib.Path(project_dir) / init_dir
        for path in sorted(init_root.glob("*.sh")):
            init_roles = discover_init_roles(path.read_text())
            roles.update(
                {
                    secret: init_roles[var]
                    for secret, var in wired.items()
                    if var in init_roles
                }
            )
    for secret, var in wired.items():
        if var == "POSTGRES_PASSWORD" and secret not in roles:
            roles[secret] = discover_postgres_user(compose_text)
    return roles


def discover_postgres_user(compose_text: str) -> str:
    """Return the compose-declared ``POSTGRES_USER`` ('postgres' default).

    It is the superuser account used to run ``ALTER ROLE`` statements
    against the stack's database.
    """
    for line in compose_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("POSTGRES_USER:"):
            value = stripped[len("POSTGRES_USER:") :].strip()
            if value:
                return value.strip("\"'")
    return "postgres"


def read_puid_pgid(env_path: pathlib.Path) -> tuple[str, str]:
    """The ``PUID``/``PGID`` from ``.env`` (last wins), or the defaults."""
    puid, pgid = DEFAULT_PUID, DEFAULT_PGID
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("PUID="):
                value = line[len("PUID=") :].strip()
                if value:
                    puid = value
            elif line.startswith("PGID="):
                value = line[len("PGID=") :].strip()
                if value:
                    pgid = value
    return puid, pgid


def _maybe_reown(secrets_dir: pathlib.Path, env_path: pathlib.Path) -> None:
    puid, pgid = read_puid_pgid(env_path)
    cur_uid, cur_gid = str(os.getuid()), str(os.getgid())
    if (puid, pgid) == (cur_uid, cur_gid):
        return
    docker = shutil.which("docker")
    if docker is None:
        print(
            f"WARNING: secrets are owned by {cur_uid}:{cur_gid}, but services "
            f"run as {puid}:{pgid}."
        )
        print(
            f"Install docker, or chown {secrets_dir}/*{GEN_SUFFIX} to "
            f"{puid}:{pgid} manually, before 'up'."
        )
        return
    print(f"Re-owning secrets to {puid}:{pgid} (container uid/gid)...")
    subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-u",
            "0:0",
            "-v",
            f"{secrets_dir}:/secrets",
            "busybox",
            "chown",
            "-R",
            f"{puid}:{pgid}",
            "/secrets",
        ],
        check=True,
    )


def _index_declared(
    declared: collections.abc.Iterable[str],
) -> dict[str, str]:
    """Map user-facing secret names to their compose-declared paths."""
    by_name: dict[str, str] = {}
    for rel in declared:
        name = rel.rpartition("/")[2].removesuffix(GEN_SUFFIX)
        by_name.setdefault(name, rel)
    return by_name


def _select_rotations(
    rotate: object,
    declared: list[str],
) -> list[str]:
    """The declared paths to rotate: all, or the named ones, in order."""
    if rotate is ROTATE_ALL:
        return list(declared)
    by_name = _index_declared(declared)
    unknown: list[str] = []
    selected: list[str] = []
    for raw in rotate:
        rel = by_name.get(raw.removesuffix(GEN_SUFFIX))
        if rel is None:
            unknown.append(raw)
        elif rel not in selected:
            selected.append(rel)
    if unknown:
        raise UnknownSecrets(unknown, by_name)
    return selected


def _print_dry_run(
    selected: list[str],
    skipped: list[str],
    rotating: bool,
    wired: dict[str, str],
) -> None:
    """Print what a real run would write, without touching anything."""
    what = "rotate" if rotating else "generate"
    for rel in selected:
        name = rel.rpartition("/")[2]
        note = ""
        if rotating and name in wired:
            note = "  (will need 'ALTER ROLE')"
        print(f"would {what}: {name}{note}")
    for rel in skipped:
        print(f"would leave: {rel.rpartition('/')[2]} (exists)")
    print()
    print("=== Dry run: nothing written ===")


def _print_alter_role_block(
    generated: list[tuple[str, str]],
    wired: dict[str, str],
    roles: dict[str, str],
    db_user: str,
) -> None:
    """Print ``ALTER ROLE`` commands for rotated Postgres role passwords."""
    db_rotated = [(name, pwd) for name, pwd in generated if name in wired]
    if not db_rotated:
        return
    print()
    print("PostgreSQL note: the rotated secrets above are Postgres role")
    print("passwords, which Postgres reads only at initdb. If the")
    print("'postgres_data' volume already exists, its roles still hold the")
    print("old passwords. Copy-paste one command per rotated secret:")
    print()
    for name, password in db_rotated:
        role = roles.get(name)
        if role is None:
            print(f"  # {name}: role not derivable; edit the command's role")
            continue
        print(
            f"  docker compose exec -T postgres psql -U {db_user} "
            f"-c \"ALTER ROLE {role} PASSWORD '{password}';\""
        )
    print()
    print("or wipe the volume and let initdb re-create the roles:")
    print()
    print("  docker compose down -v")
    print()


def _print_summary(
    secrets_dir: pathlib.Path,
    generated: list[tuple[str, str]],
    project: pathlib.Path,
    *,
    verb: str,
    skipped: list[str],
) -> None:
    print()
    print(f"=== Successfully {verb.lower()} {len(generated)} secret(s) ===")
    if skipped:
        print(f"=== Left {len(skipped)} existing secret(s) unchanged ===")
    print(f"Secret files directory: {secrets_dir}")
    print()
    print(f"=== {verb} passwords ===")
    for name, password in generated:
        print(f"  {name.removesuffix(GEN_SUFFIX)}: {password}")
    print()
    print("IMPORTANT: save these securely; they will not be displayed again.")
    print()
    print("=== Next steps ===")
    print(f"  cd {project}")
    print("  docker compose build postgres")
    print("  docker compose up -d")


def generate_secrets(
    project_dir,
    *,
    rotate=None,
    dry_run=False,
) -> None:
    """Provision the ``*.gen`` secrets declared in the stack's compose file.

    By default only the files which do not exist yet are written (fresh
    ``0600`` password each, re-owned to ``PUID:PGID`` when needed), so
    re-running on an established stack never replaces a value. Pass
    :data:`ROTATE_ALL` or a sequence of secret names to instead overwrite
    those values -- a rotation, which prints ``ALTER ROLE`` commands for
    the rotated Postgres role passwords. With ``dry_run``, only the plan
    is printed. Raises :class:`SecretsError` if the compose file is
    missing or a requested secret name does not match a declared secret.
    """
    project = pathlib.Path(project_dir).resolve()
    compose = project / COMPOSE_FILE
    if not compose.is_file():
        raise ComposeNotFound(compose)

    compose_text = compose.read_text()
    declared = list(discover_secret_files(compose_text))
    wired = discover_db_pass_vars(compose_text)
    roles = discover_secret_roles(compose_text, project)
    db_user = discover_postgres_user(compose_text)

    if not declared:
        print(f"WARNING: no *{GEN_SUFFIX} files found in {COMPOSE_FILE}")
        return

    rotating = rotate is not None
    if rotating:
        selected = _select_rotations(rotate, declared)
        skipped: list[str] = []
    else:
        skipped = [rel for rel in declared if (project / rel).exists()]
        selected = [rel for rel in declared if rel not in skipped]
        if not selected:
            print(
                f"All {len(declared)} declared *{GEN_SUFFIX} secrets "
                "already exist; nothing written."
            )
            print(
                "Use '--rotate <name>' to replace one, or '--rotate-all' "
                "to replace all."
            )
            return

    if dry_run:
        _print_dry_run(selected, skipped, rotating, wired)
        return

    secrets_dir = project / SECRETS_DIR
    secrets_dir.mkdir(parents=True, exist_ok=True)

    verb = "Rotated" if rotating else "Generated"
    generated: list[tuple[str, str]] = []
    for rel in selected:
        secret_file = project / rel
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        password = generate_password()
        secret_file.write_text(password)
        secret_file.chmod(0o600)
        generated.append((secret_file.name, password))
        print(f"✓ {verb}: {secret_file.name}")
    for rel in skipped:
        print(f"= Existing: {rel.rpartition('/')[2]} (left unchanged)")

    _maybe_reown(secrets_dir, project / ENV_FILE)
    _print_summary(secrets_dir, generated, project, verb=verb, skipped=skipped)
    if rotating:
        _print_alter_role_block(generated, wired, roles, db_user)
