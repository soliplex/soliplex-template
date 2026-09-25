"""Report and apply pending Alembic migrations to a stack's Soliplex databases.

This is the reusable core behind the generated project's bundled
``scripts/migrate_soliplex_dbs.py`` -- a PEP 723 shim that ships in every stack
and delegates here. It ships in the published ``soliplex-template``
distribution so the shim can ``from soliplex_template.soliplex_migrations
import migrate_soliplex_dbs``.

The backend keeps two databases, ``agui`` (thread persistence) and ``authz``
(authorization policy), and tracks their schema with Alembic. In a stack
generated from this template each application role owns its own schema, so
the backend migrates both itself on its first writable open: most ``soliplex``
bumps need nothing but a rebuild and a restart. Two cases do need a hand:

- Databases created by ``soliplex <= 0.81`` carry no ``alembic_version`` row.
  From 0.82 on the backend refuses to start against them until soliplex's
  one-time ``scripts/bootstrap_alembic_version.py`` has stamped them. That
  script is not part of the installed package, so it is fetched at the tag
  matching the ``soliplex`` in the backend image.
- An operator who would rather see the migrations applied than have the
  next restart apply them silently.

Every step runs in the stack's own backend image, through
``docker compose run --rm --no-deps backend``, so the DBURIs and passwords
come from the installation config and its Docker secrets:

- ``soliplex-cli database status`` reports each database's state, and is
  safe while the stack is up;
- the bootstrap script, with ``--dry-run``, reports the revision it would
  stamp, and without it writes the stamp;
- ``soliplex-cli database upgrade`` brings both databases to head.

Writing needs the backend stopped: a backend still running an older release
would be serving against a schema changed underneath it.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import shutil
import subprocess

COMPOSE_FILE = "docker-compose.yml"
# The service whose image, mounts and secrets every step borrows.
SERVICE = "backend"
# Where the compose file binds 'backend/environment' in that service.
INSTALLATION_PATH = "/environment"
PYTHON = "/app/.venv/bin/python"
SOLIPLEX_CLI = "/app/.venv/bin/soliplex-cli"
# The first release with the 'soliplex-cli database' group.
MIN_VERSION = (0, 82)
BOOTSTRAP_URL = (
    "https://raw.githubusercontent.com/soliplex/soliplex/"
    "v{version}/scripts/bootstrap_alembic_version.py"
)
# Where the fetched bootstrap script lands inside the throwaway container.
BOOTSTRAP_PATH = "/tmp/bootstrap_alembic_version.py"
# The two writing steps, as named in failure messages.
BOOTSTRAP_STEP = "bootstrap_alembic_version.py"
UPGRADE_STEP = "soliplex-cli database upgrade"
# Rich wraps at the console width, which defaults to 80 columns without a
# TTY; widened, every state 'database status' reports is one line.
CONSOLE_COLUMNS = "1000"
# The stack-side shim that fronts this module, named in operator hints.
SHIM = "scripts/migrate_soliplex_dbs.py"

VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
# 'database status' opens a block per database with '- <name>: <dburi>',
# then reports either one 'ERROR:' line or 'applied:' / 'pending:' counts,
# each followed by one '<revision>  <message>' line per revision.
DATABASE_RE = re.compile(r"^- (?P<name>\w+): ")
HEAD_RE = re.compile(r"^head: (?P<revision>\w+)")
ERROR_RE = re.compile(r"^ERROR: (?:\w+: )?(?P<message>.*)$")
PENDING_RE = re.compile(r"^pending: (?P<count>\d+)$")
REVISION_RE = re.compile(r"^(?P<revision>[0-9a-f]+)\s+(?P<message>.*)$")
UNSTAMPED_MARK = "alembic_version is empty"
# The bootstrap script reports what it detected as 'revision: <id> (...)'.
BOOTSTRAP_REVISION_RE = re.compile(r"(?m)^revision: (?P<revision>\w+)")

CURRENT = "current"
BEHIND = "behind"
UNSTAMPED = "unstamped"
BROKEN = "broken"


class SoliplexMigrationsError(Exception):
    """A user-facing error (printed without a traceback)."""


class DockerMissing(SoliplexMigrationsError):
    def __init__(self):
        super().__init__(
            "docker not found on PATH (the Docker CLI is required)"
        )


class ComposeNotFound(SoliplexMigrationsError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"no {COMPOSE_FILE} at {path} "
            "(run from the stack directory or pass --project-dir)"
        )


class CommandFailed(SoliplexMigrationsError):
    def __init__(self, what, returncode):
        self.what = what
        self.returncode = returncode
        super().__init__(f"{what} exited {returncode} (output above)")


class UnparseableVersion(SoliplexMigrationsError):
    def __init__(self, text):
        self.text = text
        super().__init__(
            f"could not read the soliplex version in the {SERVICE} image "
            f"(got {text!r})"
        )


class ImageTooOld(SoliplexMigrationsError):
    def __init__(self, version):
        self.version = version
        minimum = ".".join(str(part) for part in MIN_VERSION)
        super().__init__(
            f"the {SERVICE} image has soliplex {version}, which predates "
            f"'soliplex-cli database' ({minimum}); raise the pin in "
            f"backend/constraints.txt and rebuild it first: "
            f"docker compose build {SERVICE}"
        )


class UnparseableStatus(SoliplexMigrationsError):
    def __init__(self):
        super().__init__(
            "'soliplex-cli database status' reported no databases "
            "(output above)"
        )


class BackendRunning(SoliplexMigrationsError):
    def __init__(self):
        super().__init__(
            f"the {SERVICE} service is running; stop it before migrating "
            f"(it would be serving against a schema changed underneath it): "
            f"docker compose stop {SERVICE}"
        )


class DatabasesBroken(SoliplexMigrationsError):
    def __init__(self, names):
        self.names = names
        super().__init__(
            f"cannot migrate {', '.join(names)} as they stand "
            f"(see above; 'uv run {SHIM} --check' reports the same)"
        )


@dataclasses.dataclass(frozen=True)
class DatabaseStatus:
    """One database's state, as ``soliplex-cli database status`` reports it.

    ``pending`` lists the ``<revision>  <message>`` entries still to apply;
    ``error`` is the reason a database which cannot be migrated as it stands
    was refused.
    """

    name: str
    state: str
    pending: tuple[str, ...] = ()
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class Status:
    head: str | None
    databases: tuple[DatabaseStatus, ...]

    def in_state(self, state: str) -> list[DatabaseStatus]:
        return [db for db in self.databases if db.state == state]


def docker_cli() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise DockerMissing()
    return docker


def resolve_project(project_dir) -> pathlib.Path:
    project = pathlib.Path(project_dir).resolve()
    compose = project / COMPOSE_FILE
    if not compose.is_file():
        raise ComposeNotFound(compose)
    return project


def compose_argv(project: pathlib.Path, *args: str) -> list[str]:
    return [
        docker_cli(),
        "compose",
        "--project-directory",
        str(project),
        *args,
    ]


def backend_argv(project: pathlib.Path, *command: str) -> list[str]:
    """The ``docker compose run`` argv for one command in the backend image.

    ``--no-deps`` leaves the rest of the stack alone: every step needs only
    Postgres, which a stopped backend leaves running.
    """
    return compose_argv(
        project,
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--env",
        f"COLUMNS={CONSOLE_COLUMNS}",
        SERVICE,
        *command,
    )


def _run_captured(argv: list[str], what: str) -> subprocess.CompletedProcess:
    """Run ``argv`` capturing its output; echo it and raise if it failed."""
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="")
        raise CommandFailed(what, result.returncode)
    return result


def backend_running(project: pathlib.Path) -> bool:
    """True if the backend service has a running container.

    ``docker compose ps -q`` lists ids of *running* containers for the service
    (stopped ones need ``-a``), so non-empty output means it is up.
    """
    result = subprocess.run(
        compose_argv(project, "ps", "-q", SERVICE),
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def parse_version(text: str) -> tuple[int, ...]:
    version = text.strip()
    if not VERSION_RE.match(version):
        raise UnparseableVersion(version)
    return tuple(int(part) for part in version.split("."))


def soliplex_version(project: pathlib.Path) -> str:
    """The ``soliplex`` release installed in the backend image.

    Refuses an image older than the first release with the
    ``soliplex-cli database`` group: its pin was raised without a rebuild.
    """
    result = _run_captured(
        backend_argv(
            project,
            PYTHON,
            "-c",
            "import importlib.metadata as m; print(m.version('soliplex'))",
        ),
        f"reading the soliplex version in the {SERVICE} image",
    )
    version = result.stdout.strip()
    if parse_version(version)[: len(MIN_VERSION)] < MIN_VERSION:
        raise ImageTooOld(version)
    return version


def _classify(name, error, pending) -> DatabaseStatus:
    if error is not None:
        state = UNSTAMPED if UNSTAMPED_MARK in error else BROKEN
        return DatabaseStatus(name, state, error=error)
    state = BEHIND if pending else CURRENT
    return DatabaseStatus(name, state, pending=tuple(pending))


def parse_status(text: str) -> Status:
    """The per-database state ``soliplex-cli database status`` reported."""
    head = None
    databases: list[DatabaseStatus] = []
    name = error = None
    pending: list[str] = []
    in_pending = False
    for raw in text.splitlines():
        line = raw.strip()
        if match := HEAD_RE.match(line):
            head = match["revision"]
        elif match := DATABASE_RE.match(line):
            if name is not None:
                databases.append(_classify(name, error, pending))
            name, error, pending, in_pending = match["name"], None, [], False
        elif name is None:
            continue
        elif match := ERROR_RE.match(line):
            error = match["message"]
        elif PENDING_RE.match(line):
            # 'pending:' is the last list in a database's block.
            in_pending = True
        elif in_pending and (match := REVISION_RE.match(line)):
            pending.append(f"{match['revision']}  {match['message']}")
    if name is not None:
        databases.append(_classify(name, error, pending))
    return Status(head, tuple(databases))


def database_status(project: pathlib.Path) -> Status:
    """Ask the backend image what each database has applied and owes.

    The command exits 1 for a database which cannot be migrated as it stands
    (unstamped, unreachable, stamped by a newer release) and says why on that
    database's ``ERROR:`` line, so a non-zero exit is not a failure here.
    """
    result = subprocess.run(
        backend_argv(
            project, SOLIPLEX_CLI, "database", "status", INSTALLATION_PATH
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    status = parse_status(result.stdout)
    if not status.databases:
        print(result.stdout, end="")
        print(result.stderr, end="")
        raise UnparseableStatus()
    return status


def bootstrap_argv(
    project: pathlib.Path, version: str, *, dry_run: bool
) -> list[str]:
    """Fetch soliplex's bootstrap script at ``version``'s tag and run it.

    Both happen in one throwaway container: the script resolves the DBURIs
    from the installation config, so it needs the backend's mounts, secrets
    and installed soliplex. ``version`` has been through ``parse_version``,
    and still reaches the shell only as a positional argument.
    """
    script = (
        'url="$1"; shift; '
        f'curl -fsSL "$url" -o {BOOTSTRAP_PATH} && '
        f'exec {PYTHON} {BOOTSTRAP_PATH} "$@"'
    )
    args = ["--installation-path", INSTALLATION_PATH]
    if dry_run:
        args.append("--dry-run")
    return backend_argv(
        project,
        "sh",
        "-c",
        script,
        "bootstrap",
        BOOTSTRAP_URL.format(version=version),
        *args,
    )


def bootstrap_revision(project: pathlib.Path, version: str) -> str | None:
    """The revision the bootstrap script would stamp; writes nothing."""
    result = _run_captured(
        bootstrap_argv(project, version, dry_run=True),
        "bootstrap_alembic_version.py --dry-run",
    )
    match = BOOTSTRAP_REVISION_RE.search(result.stdout)
    return match["revision"] if match else None


def bootstrap(project: pathlib.Path, version: str) -> None:
    """Stamp every unstamped database at the revision its schema matches."""
    result = subprocess.run(
        bootstrap_argv(project, version, dry_run=False), check=False
    )
    if result.returncode != 0:
        raise CommandFailed(BOOTSTRAP_STEP, result.returncode)


def upgrade(project: pathlib.Path) -> None:
    """Bring both databases to the image's head revision."""
    result = subprocess.run(
        backend_argv(
            project, SOLIPLEX_CLI, "database", "upgrade", INSTALLATION_PATH
        ),
        check=False,
    )
    if result.returncode != 0:
        raise CommandFailed(UPGRADE_STEP, result.returncode)


def _print_databases(status: Status, stamp: str | None = None) -> None:
    for db in status.databases:
        if db.state == CURRENT:
            print(f"✓ {db.name}: at head")
        elif db.state == BEHIND:
            print(f"! {db.name}: {len(db.pending)} migration(s) pending")
            for entry in db.pending:
                print(f"    {entry}")
        elif db.state == UNSTAMPED:
            detected = f" (would stamp {stamp})" if stamp else ""
            print(
                f"! {db.name}: unstamped, created by soliplex 0.81 or "
                f"earlier{detected}"
            )
        else:
            print(f"✗ {db.name}: {db.error}")


def _report(project: pathlib.Path, version: str) -> int:
    """Print per-database state; 1 if anything is owed or broken."""
    status = database_status(project)
    unstamped = status.in_state(UNSTAMPED)
    stamp = bootstrap_revision(project, version) if unstamped else None
    print(f"soliplex {version}; head {status.head}")
    _print_databases(status, stamp)
    owed = unstamped or status.in_state(BEHIND)
    broken = status.in_state(BROKEN)
    if owed and not broken:
        print()
        print("Stop the backend, then apply them:")
        print(f"  docker compose stop {SERVICE}")
        print(f"  uv run {SHIM}")
        print(f"  docker compose start {SERVICE}")
    if broken:
        print()
        print(
            f"{len(broken)} database(s) cannot be migrated as they stand "
            "(reasons above). One which 'did not open' usually means "
            "postgres is down: docker compose up -d postgres"
        )
    return 1 if owed or broken else 0


def _apply(project: pathlib.Path, version: str) -> int:
    if backend_running(project):
        raise BackendRunning()
    status = database_status(project)
    print(f"soliplex {version}; head {status.head}")
    _print_databases(status)
    broken = status.in_state(BROKEN)
    if broken:
        raise DatabasesBroken([db.name for db in broken])
    if status.in_state(UNSTAMPED):
        # The CLI writes straight to this process's stdout, so the header has
        # to be flushed or it lands after the output it introduces.
        print(f"=== {BOOTSTRAP_STEP} ===", flush=True)
        bootstrap(project, version)
        status = database_status(project)
        _print_databases(status)
    if status.in_state(BEHIND):
        print(f"=== {UPGRADE_STEP} ===", flush=True)
        upgrade(project)
    else:
        print("Nothing to migrate.")
    print()
    print("Start the backend:")
    print(f"  docker compose start {SERVICE}")
    return 0


def migrate_soliplex_dbs(project_dir, check=False) -> int:
    """Migrate (or with ``check``, report on) the stack's Soliplex databases.

    Returns the process exit status: ``check`` yields 1 when a database is
    unstamped, behind head, or cannot be migrated as it stands, so it gates a
    ``soliplex`` bump in a script or CI job.
    """
    project = resolve_project(project_dir)
    version = soliplex_version(project)
    if check:
        return _report(project, version)
    return _apply(project, version)
