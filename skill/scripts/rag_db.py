#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Create or update a one-off (static) Soliplex RAG database.

The stack's ``haiku-ingester`` service *continuously* maintains a single
LanceDB from changing sources. For installations with many rooms backed by
different but largely *static* RAG databases, running an ingester per database
is overkill and trips the single-writer constraint. This script builds (or
later updates) a standalone database with the ``haiku-rag`` CLI instead.

It reuses the existing ``haiku-ingester`` service definition via
``docker compose run --rm`` -- the same image (which ships the ``haiku-rag``
CLI), the ``./rag/db -> /data`` and ``./rag/docs -> /docs`` bind mounts, the
``OLLAMA_BASE_URL`` env, the RO-mounted ``haiku.rag.yaml`` config, and the
``docling-serve`` dependency. The one-off container writes to a *different*
``--db /data/<name>.lancedb`` than the running ingester's database, so there is
no single-writer conflict. Reusing the same config means the new database's
embeddings/chunking match the rest of the stack.

Run it from the stack directory (or pass ``--project-dir``)::

    # create a new database and populate it from a source
    python3 rag_db.py create --db-name handbook --source rag/docs/handbook/

    # later: add more documents / re-index / remove a document
    python3 rag_db.py update --db-name handbook --source rag/docs/2026-q2/
    python3 rag_db.py update --db-name handbook --rebuild --rechunk
    python3 rag_db.py update --db-name handbook --delete <document_id>

    # wire the database into one or more rooms (by room id)
    python3 rag_db.py add-rag-to-room --db-name handbook --room chat

``add-rag-to-room`` sets ``rag_lancedb_stem: "<name>"`` on the
``haiku.rag.skills.rag`` skill config of each named room's
``room_config.yaml``, editing the file in place while preserving its comments
and layout. Rooms are resolved by id through the installation's loaded
``room_paths`` (via ``soliplex_config``), so it honors limited/shared room
sets rather than globbing the rooms tree.

Writing the ingester's *own* database (``--db-name haiku.rag``) is refused
while the ``haiku-ingester`` service is running, since two writers corrupt
LanceDB; stop that service first and it is allowed (with a warning).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse

# Room wiring resolves room ids via the sibling ``soliplex_config`` helper
# (same skill/scripts/ directory). Put that directory on the path so the lazy
# ``import soliplex_config`` in ``do_add_to_room`` resolves whether this script
# is run directly or loaded by path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# The continuous ingester writes this LanceDB stem; a one-off database must use
# a different stem, or the two writers collide (single-writer constraint).
INGESTER_STEM = "haiku.rag"
# A LanceDB stem usable as a path segment: no '/', no '..', no leading dot.
DB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_SERVICE = "haiku-ingester"
# The service's existing read-only config mount inside the container.
DEFAULT_CONFIG = "/app/haiku.rag.yaml"

# Room wiring: the rag skill we attach the LanceDB stem to, and the key.
RAG_SKILL_KIND = "haiku.rag.skills.rag"
_STEM_KEY = "rag_lancedb_stem"
_KIND_RE = re.compile(
    r"""^(?P<indent>\s*)-\s+kind:\s*["']?"""
    + re.escape(RAG_SKILL_KIND)
    + r"""["']?\s*$"""
)
_TOP_SKILLS_RE = re.compile(r"^skills:\s*$")


class RagDbError(Exception):
    """A user-facing error (printed without a traceback).

    Message construction lives in these classmethod factories so call sites
    read ``raise RagDbError.<reason>(...)`` with no inline message string.
    """

    @classmethod
    def docker_missing(cls):
        return cls("docker not found on PATH (the Docker CLI is required)")

    @classmethod
    def compose_not_found(cls, path):
        return cls(
            f"no docker-compose.yml at {path} "
            "(run from the stack directory or pass --project-dir)"
        )

    @classmethod
    def ragdb_dir_missing(cls, path):
        return cls(f"RAG db directory not found at {path}")

    @classmethod
    def bad_db_name(cls, name):
        return cls(
            f"--db-name {name!r} must match {DB_NAME_RE.pattern} "
            "(letters, digits, '.', '_', '-'; no leading dot)"
        )

    @classmethod
    def reserved_db_name(cls, name):
        return cls(
            f"--db-name {name!r} is the continuous ingester's database and "
            f"the {DEFAULT_SERVICE} service is running; stop it first "
            "(concurrent writers corrupt LanceDB) or pick a different stem"
        )

    @classmethod
    def db_exists(cls, path):
        return cls(f"{path} already exists (use --force to add into it)")

    @classmethod
    def db_missing(cls, path):
        return cls(f"{path} does not exist (create it first)")

    @classmethod
    def source_missing(cls, path):
        return cls(f"--source {path} does not exist")

    @classmethod
    def no_update_op(cls):
        return cls(
            "update needs at least one of --source, --rebuild, or --delete"
        )

    @classmethod
    def modifier_without_rebuild(cls):
        return cls(
            "--rechunk/--embed-only/--title-only are only valid with --rebuild"
        )

    @classmethod
    def room_not_found(cls, room_id, available):
        avail = ", ".join(sorted(available)) or "(none found)"
        return cls(f"room id {room_id!r} not found; available: {avail}")

    @classmethod
    def room_skills_present_no_rag(cls, room_id):
        return cls(
            f"room {room_id!r} has a 'skills:' block but no "
            f"{RAG_SKILL_KIND!r} skill config; add that skill config and its "
            f"{_STEM_KEY} by hand"
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def validate_db_name(name: str) -> None:
    if not DB_NAME_RE.match(name):
        raise RagDbError.bad_db_name(name)


def ingester_running(project: pathlib.Path) -> bool:
    """True if the continuous ingester service has a running container.

    ``docker compose ps -q`` lists ids of *running* containers for the service
    (stopped ones need ``-a``), so non-empty output means it is up.
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(project),
            "ps",
            "-q",
            DEFAULT_SERVICE,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def guard_reserved_stem(project: pathlib.Path, db_name: str) -> None:
    """Refuse writing the ingester's own stem while its service is running.

    Writing ``haiku.rag.lancedb`` from a one-off container is only safe when
    the long-lived ingester is stopped; otherwise the two writers collide (the
    single-writer constraint). When it is stopped we allow it, with a warning.
    """
    if db_name != INGESTER_STEM:
        return
    if ingester_running(project):
        raise RagDbError.reserved_db_name(db_name)
    print(
        f"warning: writing the ingester's database {db_name!r}; safe only "
        f"because the {DEFAULT_SERVICE} service is not running."
    )


def resolve_project(project_dir: str) -> pathlib.Path:
    project = pathlib.Path(project_dir).resolve()
    compose = project / "docker-compose.yml"
    if not compose.is_file():
        raise RagDbError.compose_not_found(compose)
    ragdb = project / "rag" / "db"
    if not ragdb.is_dir():
        raise RagDbError.ragdb_dir_missing(ragdb)
    return project


def db_lancedb_path(project: pathlib.Path, db_name: str) -> pathlib.Path:
    return project / "rag" / "db" / f"{db_name}.lancedb"


def resolve_source(
    project: pathlib.Path, source: str
) -> tuple[list[str], str]:
    """Map a host source to (extra ``docker run`` args, container path).

    Remote URIs (a non-empty URL scheme) pass through to ``add-src``
    untouched. A local path already under ``rag/docs/`` reuses the service's
    ``/docs`` mount; anything else is auto-bind-mounted read-only at ``/src``.
    """
    if urllib.parse.urlsplit(source).scheme:
        return [], source

    path = pathlib.Path(source).resolve()
    if not path.exists():
        raise RagDbError.source_missing(path)

    docs_root = (project / "rag" / "docs").resolve()
    if path == docs_root or docs_root in path.parents:
        rel = path.relative_to(docs_root)
        return [], str(pathlib.PurePosixPath("/docs", *rel.parts))

    if path.is_dir():
        return ["-v", f"{path}:/src:ro"], "/src"
    return ["-v", f"{path}:/src/{path.name}:ro"], f"/src/{path.name}"


def compose_run(
    project: pathlib.Path,
    service: str,
    config: str,
    db_name: str,
    mounts: list[str],
    cli_args: list[str],
) -> None:
    """Run one ``haiku-rag`` invocation in a one-off service container."""
    db = f"/data/{db_name}.lancedb"
    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-TTY",
        *mounts,
        service,
        "haiku-rag",
        "--config",
        config,
        "--db",
        db,
        *cli_args,
    ]
    subprocess.run(cmd, check=True)


def rebuild_modifier(args: argparse.Namespace) -> str | None:
    if args.rechunk:
        return "--rechunk"
    if args.embed_only:
        return "--embed-only"
    if args.title_only:
        return "--title-only"
    return None


def print_wiring_hint(db_name: str) -> None:
    print("To wire it into one or more rooms, run:")
    print(
        f"  rag_db.py add-rag-to-room --db-name {db_name} "
        "--room <room_id> [--room <room_id> ...]"
    )
    print("(or set rag_lancedb_stem in a room's room_config.yaml by hand).")


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _join(lines: list[str], original: str) -> str:
    """Re-join lines, preserving the original file's trailing newline."""
    text = "\n".join(lines)
    if original.endswith("\n"):
        text += "\n"
    return text


def wire_room_stem(text: str, db_name: str, room_id: str) -> tuple[str, str]:
    """Set ``rag_lancedb_stem: "<db_name>"`` on the room's rag skill config.

    Returns ``(new_text, action)`` where action is ``"unchanged"``,
    ``"updated"``, ``"inserted"``, or ``"appended"``. The edit is line-based
    (not a YAML round-trip), so comments and unrelated layout are preserved.

    Raises ``RagDbError`` when the file already has a top-level ``skills:``
    block but no ``haiku.rag.skills.rag`` entry: that shape is ambiguous to
    splice safely, so the caller is told to wire it by hand.
    """
    lines = text.splitlines()
    stem = f'{_STEM_KEY}: "{db_name}"'

    kind_idx = next(
        (i for i, ln in enumerate(lines) if _KIND_RE.match(ln)), None
    )
    if kind_idx is not None:
        # Sibling keys of the matched list item align two columns past its '-'.
        key_indent = _leading_spaces(lines[kind_idx]) + 2
        j = kind_idx + 1
        while j < len(lines):
            stripped = lines[j].strip()
            indent = _leading_spaces(lines[j])
            if stripped and indent < key_indent:
                break  # left the mapping that owns this kind: line
            if indent == key_indent and stripped.startswith(f"{_STEM_KEY}:"):
                if stripped == stem:
                    return text, "unchanged"
                lines[j] = " " * key_indent + stem
                return _join(lines, text), "updated"
            j += 1
        lines.insert(kind_idx + 1, " " * key_indent + stem)
        return _join(lines, text), "inserted"

    if any(_TOP_SKILLS_RE.match(ln) for ln in lines):
        raise RagDbError.room_skills_present_no_rag(room_id)

    block = [
        "skills:",
        "  skill_configs:",
        f'    - kind: "{RAG_SKILL_KIND}"',
        f"      {stem}",
    ]
    sep = [] if (not lines or not lines[-1].strip()) else [""]
    return _join(lines + sep + block, text), "appended"


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------
def do_create(args: argparse.Namespace) -> int:
    if shutil.which("docker") is None:
        raise RagDbError.docker_missing()
    project = resolve_project(args.project_dir)
    validate_db_name(args.db_name)
    guard_reserved_stem(project, args.db_name)

    db_path = db_lancedb_path(project, args.db_name)
    if db_path.exists() and not args.force:
        raise RagDbError.db_exists(db_path)

    # Resolve (and validate) the source before creating anything.
    mounts, container_src = resolve_source(project, args.source)

    compose_run(project, args.service, args.config, args.db_name, [], ["init"])
    compose_run(
        project,
        args.service,
        args.config,
        args.db_name,
        mounts,
        ["add-src", container_src],
    )

    print(f"\n✓ Created RAG database {args.db_name!r} at {db_path}\n")
    print_wiring_hint(args.db_name)
    return 0


def do_update(args: argparse.Namespace) -> int:
    if shutil.which("docker") is None:
        raise RagDbError.docker_missing()
    project = resolve_project(args.project_dir)
    validate_db_name(args.db_name)

    if rebuild_modifier(args) and not args.rebuild:
        raise RagDbError.modifier_without_rebuild()
    if not (args.source or args.rebuild or args.delete):
        raise RagDbError.no_update_op()
    guard_reserved_stem(project, args.db_name)

    db_path = db_lancedb_path(project, args.db_name)
    if not db_path.exists():
        raise RagDbError.db_missing(db_path)

    # Resolve the source up front so a bad path fails before any mutation.
    mounts: list[str] = []
    container_src = ""
    if args.source:
        mounts, container_src = resolve_source(project, args.source)

    # Order: prune, then ingest new content, then re-index.
    for doc_id in args.delete or []:
        compose_run(
            project,
            args.service,
            args.config,
            args.db_name,
            [],
            ["delete", doc_id],
        )
    if args.source:
        compose_run(
            project,
            args.service,
            args.config,
            args.db_name,
            mounts,
            ["add-src", container_src],
        )
    if args.rebuild:
        rebuild_args = ["rebuild"]
        modifier = rebuild_modifier(args)
        if modifier:
            rebuild_args.append(modifier)
        compose_run(
            project,
            args.service,
            args.config,
            args.db_name,
            [],
            rebuild_args,
        )

    done = []
    if args.delete:
        done.append(f"deleted {len(args.delete)} document(s)")
    if args.source:
        done.append(f"added {args.source}")
    if args.rebuild:
        done.append("rebuilt index")
    print(
        f"\n✓ Updated RAG database {args.db_name!r} "
        f"({', '.join(done)}) at {db_path}\n"
    )
    return 0


def do_add_to_room(args: argparse.Namespace) -> int:
    if shutil.which("docker") is None:
        raise RagDbError.docker_missing()
    project = resolve_project(args.project_dir)
    validate_db_name(args.db_name)

    # Resolve room id -> host room_config.yaml via the installation's loaded
    # room_paths (soliplex-cli config), not a rooms/* glob -- honors limited /
    # shared room sets. Imported lazily so create/update stay yaml-free.
    import soliplex_config

    # ``_resolve_room_configs`` yields ``(meta, host_path)`` per loaded room;
    # the public ``resolve_rooms`` drops the path, which we need to edit the
    # file. ``meta`` is ``{room_id, name, description}`` -- we key on room_id.
    entries, _unmapped = soliplex_config._resolve_room_configs(
        project,
        args.service,
        args.cli,
        args.installation,
        args.host_environment,
    )
    rooms = {meta["room_id"]: cfg for meta, cfg in entries}

    # Validate every requested room up front, before touching any file.
    missing = [room_id for room_id in args.room if room_id not in rooms]
    if missing:
        raise RagDbError.room_not_found(missing[0], rooms.keys())

    # A typo'd or not-yet-built database is wireable, but worth flagging.
    db_path = db_lancedb_path(project, args.db_name)
    if args.db_name != INGESTER_STEM and not db_path.exists():
        print(
            f"warning: {db_path} does not exist yet; wiring rooms to it "
            "anyway (create it with 'rag_db.py create')."
        )

    for room_id in args.room:
        cfg = rooms[room_id]
        new_text, action = wire_room_stem(
            cfg.read_text(), args.db_name, room_id
        )
        if action != "unchanged":
            cfg.write_text(new_text)
        print(f"  {room_id}: {action} ({_STEM_KEY}: {args.db_name!r})")

    print(
        f"\n✓ Wired {len(args.room)} room(s) to RAG database "
        f"{args.db_name!r}.\n"
    )
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db-name", required=True, help="LanceDB stem (rag/db/<name>.lancedb)"
    )
    common.add_argument(
        "--project-dir",
        default=".",
        help="stack directory (default: current directory)",
    )
    common.add_argument(
        "--service",
        default=DEFAULT_SERVICE,
        help=f"compose service to reuse (default: {DEFAULT_SERVICE})",
    )
    common.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"in-container haiku.rag config (default: {DEFAULT_CONFIG})",
    )

    parser = argparse.ArgumentParser(
        description="Create or update a one-off Soliplex RAG database."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser(
        "create",
        parents=[common],
        help="create a new database and populate it from a source",
    )
    create.add_argument(
        "--source", required=True, help="path under rag/docs/, URL, or path"
    )
    create.add_argument(
        "--force",
        action="store_true",
        help="add into the database even if it already exists",
    )
    create.set_defaults(func=do_create)

    update = sub.add_parser(
        "update",
        parents=[common],
        help="add documents to / re-index / delete from an existing database",
    )
    update.add_argument(
        "--source", help="path under rag/docs/, URL, or path to add"
    )
    update.add_argument(
        "--rebuild", action="store_true", help="re-index existing documents"
    )
    mod = update.add_mutually_exclusive_group()
    mod.add_argument(
        "--rechunk", action="store_true", help="(with --rebuild) re-chunk"
    )
    mod.add_argument(
        "--embed-only",
        action="store_true",
        help="(with --rebuild) recompute embeddings only",
    )
    mod.add_argument(
        "--title-only",
        action="store_true",
        help="(with --rebuild) recompute titles only",
    )
    update.add_argument(
        "--delete",
        action="append",
        metavar="ID",
        help="document id to delete (repeatable)",
    )
    update.set_defaults(func=do_update)

    addroom = sub.add_parser(
        "add-rag-to-room",
        help="wire an existing RAG database into one or more rooms",
    )
    addroom.add_argument(
        "--db-name",
        required=True,
        help="LanceDB stem to wire as the room's rag_lancedb_stem",
    )
    addroom.add_argument(
        "--project-dir",
        default=".",
        help="stack directory (default: current directory)",
    )
    addroom.add_argument(
        "--room",
        action="append",
        required=True,
        metavar="ID",
        help="room id to wire (repeatable)",
    )
    # soliplex_config passthrough for room id -> path resolution. Defaults
    # mirror soliplex_config's (kept inline so parsing create/update needs no
    # yaml import).
    addroom.add_argument(
        "--service",
        default="backend",
        help="compose service running soliplex-cli (default: backend)",
    )
    addroom.add_argument(
        "--cli",
        default="/app/.venv/bin/soliplex-cli",
        help="in-container soliplex-cli path",
    )
    addroom.add_argument(
        "--installation",
        default="/environment",
        help="in-container installation path (default: /environment)",
    )
    addroom.add_argument(
        "--host-environment",
        default="backend/environment",
        help="host dir bind-mounted onto --installation",
    )
    addroom.set_defaults(func=do_add_to_room)

    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except RagDbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc})", file=sys.stderr)
        sys.exit(2)
