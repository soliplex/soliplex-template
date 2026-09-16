"""Report and apply pending LanceDB store migrations for a Soliplex stack.

This is the reusable core behind the generated project's bundled
``scripts/migrate_rag_dbs.py`` -- a PEP 723 shim that ships in every stack and
delegates here. It ships in the published ``soliplex-template`` distribution so
the shim can ``from soliplex_template.rag_migrations import migrate_rag_dbs``.

haiku.rag ships a store *upgrade* only for the few releases that changed the
LanceDB layout, and checks a store against that set rather than against the
release number -- so most ``haiku.rag-slim`` bumps require no migration at all.
A bump that does cross an upgrade is a two-step operation: bump the tag, then
migrate every database under ``rag/db/`` that predates it, or the ingester
crash-loops at startup and every read fails.

Both steps go through the stack's ``haiku-rag`` service -- the one-shot CLI
runner defined alongside the ingester, which carries the same image, the
``rag/db`` mount and the queue-password secret, and passes whatever follows the
service name to ``haiku-rag``:

- ``info`` reports what is pending, and is safe while the ingester runs;
- ``migrate`` applies it, and is not: LanceDB allows a single writer, so the
  ingester has to be stopped first.

Running the CLI any other way (``docker compose run haiku-ingester
haiku-rag ...``) fails before the subcommand does anything, because
``haiku.rag.yaml`` interpolates ``INGESTER_DB_PASSWORD`` into the queue
``dburi`` and haiku.rag's loader expands the whole file up front. Exporting it
from the Docker secret is exactly what the ``haiku-rag`` service's entrypoint
exists to do.

Stdlib only.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

COMPOSE_FILE = "docker-compose.yml"
# Vector stores live here (bind-mounted at DATA_MOUNT in the container).
DB_DIR = "rag/db"
DB_SUFFIX = ".lancedb"
# The one-shot CLI runner, and the long-lived writer it has to displace.
SERVICE = "haiku-rag"
INGESTER_SERVICE = "haiku-ingester"
# Where the compose file binds 'rag/db' inside both of them.
DATA_MOUNT = "/data"
# The stack-side shim that fronts this module, named in operator hints.
SHIM = "scripts/migrate_rag_dbs.py"

# 'haiku-rag info' summarizes pending work as "N migration(s) pending", then
# lists one '→ <version>: <description>' entry per pending upgrade, closing
# the block with a horizontal rule. Rich wraps a long description onto
# continuation lines, which parse_pending() folds back into their entry.
PENDING_RE = re.compile(r"(\d+) migration\(s\) pending")
ENTRY_MARK = "→"
RULE_CHAR = "─"


class RagMigrationsError(Exception):
    """A user-facing error (printed without a traceback)."""


class DockerMissing(RagMigrationsError):
    def __init__(self):
        super().__init__(
            "docker not found on PATH (the Docker CLI is required)"
        )


class ComposeNotFound(RagMigrationsError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"no {COMPOSE_FILE} at {path} "
            "(run from the stack directory or pass --project-dir)"
        )


class DatabaseDirectoryMissing(RagMigrationsError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"RAG db directory not found at {path}")


class NoDatabases(RagMigrationsError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"no *{DB_SUFFIX} databases under {path} "
            "(nothing to migrate; the ingester creates its own on first run)"
        )


class UnknownDatabase(RagMigrationsError):
    def __init__(self, name, available):
        self.name = name
        self.available = available
        avail = ", ".join(available) or "(none found)"
        super().__init__(
            f"--db-name {name!r} not found under {DB_DIR}; available: {avail}"
        )


class IngesterRunning(RagMigrationsError):
    def __init__(self):
        super().__init__(
            f"the {INGESTER_SERVICE} service is running; stop it before "
            f"migrating (concurrent writers corrupt LanceDB): "
            f"docker compose stop {INGESTER_SERVICE}"
        )


class CommandFailed(RagMigrationsError):
    def __init__(self, db_name, returncode):
        self.db_name = db_name
        self.returncode = returncode
        super().__init__(
            f"haiku-rag exited {returncode} for {db_name} "
            "(output above; the store may need a newer image)"
        )


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
    db_dir = project / DB_DIR
    if not db_dir.is_dir():
        raise DatabaseDirectoryMissing(db_dir)
    return project


def discover_databases(project: pathlib.Path) -> list[pathlib.Path]:
    """Every ``*.lancedb`` store under the stack's ``rag/db/``, sorted."""
    db_dir = project / DB_DIR
    return sorted(db_dir.glob(f"*{DB_SUFFIX}"))


def select_databases(
    project: pathlib.Path, names: list[str] | None
) -> list[pathlib.Path]:
    """The stores to operate on: those named, else every one discovered."""
    found = discover_databases(project)
    if not found:
        raise NoDatabases(project / DB_DIR)
    if not names:
        return found
    by_stem = {db.name.removesuffix(DB_SUFFIX): db for db in found}
    selected = []
    for name in names:
        if name not in by_stem:
            raise UnknownDatabase(name, sorted(by_stem))
        selected.append(by_stem[name])
    return selected


def ingester_running(project: pathlib.Path) -> bool:
    """True if the continuous ingester service has a running container.

    ``docker compose ps -q`` lists ids of *running* containers for the service
    (stopped ones need ``-a``), so non-empty output means it is up.
    """
    result = subprocess.run(
        [
            docker_cli(),
            "compose",
            "--project-directory",
            str(project),
            "ps",
            "-q",
            INGESTER_SERVICE,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def haiku_rag_argv(
    project: pathlib.Path, db: pathlib.Path, cli_args: list[str]
) -> list[str]:
    """The ``docker compose run`` argv for one ``haiku-rag`` CLI call."""
    return [
        docker_cli(),
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-TTY",
        SERVICE,
        *cli_args,
        "--db",
        f"{DATA_MOUNT}/{db.name}",
    ]


def parse_pending(text: str) -> list[str]:
    """The ``<version>: <description>`` entries ``info`` reported pending.

    Folds Rich's wrapped continuation lines back into the entry they belong
    to, and stops at the rule closing the block.
    """
    entries: list[str] = []
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if PENDING_RE.search(line):
            inside = True
            continue
        if not inside:
            continue
        if not line or set(line) == {RULE_CHAR}:
            break
        if line.startswith(ENTRY_MARK):
            entries.append(line[len(ENTRY_MARK) :].strip())
        elif entries:
            entries[-1] = f"{entries[-1]} {line}"
    return entries


def check_database(
    project: pathlib.Path, db: pathlib.Path
) -> tuple[bool, list[str]]:
    """Ask the pinned image whether ``db`` has migrations pending."""
    result = subprocess.run(
        haiku_rag_argv(project, db, ["info"]),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="")
        raise CommandFailed(db.name, result.returncode)
    output = f"{result.stdout}{result.stderr}"
    return bool(PENDING_RE.search(output)), parse_pending(output)


def migrate_database(project: pathlib.Path, db: pathlib.Path) -> None:
    """Apply every pending migration to ``db`` (idempotent when clean)."""
    result = subprocess.run(
        haiku_rag_argv(project, db, ["migrate"]), check=False
    )
    if result.returncode != 0:
        raise CommandFailed(db.name, result.returncode)


def _report(project: pathlib.Path, databases: list[pathlib.Path]) -> int:
    """Print per-store migration state; 1 if any store is behind or broken.

    A store the CLI cannot read is reported and the survey continues: the
    point of a report is to cover every store, and one unreadable database
    should not hide the state of the rest.
    """
    stale, unreadable = [], []
    for db in databases:
        try:
            pending, entries = check_database(project, db)
        except CommandFailed as exc:
            unreadable.append(db)
            print(f"✗ {db.name}: {exc}")
            continue
        if not pending:
            print(f"✓ {db.name}: up to date")
            continue
        stale.append(db)
        print(f"! {db.name}: {len(entries)} migration(s) pending")
        for entry in entries:
            print(f"    {entry}")
    if stale:
        print()
        print(
            f"Stop the ingester, then apply them ({len(stale)} database(s)):"
        )
        print(f"  docker compose stop {INGESTER_SERVICE}")
        print(f"  uv run {SHIM}")
        print(f"  docker compose start {INGESTER_SERVICE}")
    if unreadable:
        print()
        print(
            f"{len(unreadable)} database(s) could not be read (output above); "
            "migrating is not safe until that is explained."
        )
    return 1 if stale or unreadable else 0


def _apply(project: pathlib.Path, databases: list[pathlib.Path]) -> int:
    if ingester_running(project):
        raise IngesterRunning()
    for db in databases:
        # The CLI writes straight to this process's stdout, so the header has
        # to be flushed or it lands after the output it introduces.
        print(f"=== {db.name} ===", flush=True)
        migrate_database(project, db)
    print()
    print(f"Migrated {len(databases)} database(s); restart the ingester:")
    print(f"  docker compose start {INGESTER_SERVICE}")
    return 0


def migrate_rag_dbs(project_dir, names=None, check=False) -> int:
    """Migrate (or with ``check``, report on) the stack's LanceDB stores.

    ``names`` selects stores by stem (``handbook`` for
    ``rag/db/handbook.lancedb``); all of them when empty. Returns the process
    exit status: ``check`` yields 1 when any store is behind, so it gates an
    image bump in a script or CI job.
    """
    project = resolve_project(project_dir)
    databases = select_databases(project, names)
    if check:
        return _report(project, databases)
    return _apply(project, databases)
