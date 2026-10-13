#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-plumber>=0.4"]
# ///
"""Manage the project directories under a Soliplex stack's ``src/``.

Since #176 every entry under a stack's ``src/`` is a self-contained project
directory, and the backend's ``PYTHONPATH`` names the ones whose code should
be importable::

    src/
      <package>/            # the stack's own project
      <other>/              # a repo cloned alongside it

    # docker-compose.yml, backend service:
    PYTHONPATH: /app/src/<package>/src:/app/src/<other>/src

Cloning a repo into ``src/`` needs no git work -- ``.gitignore`` already
ignores everything there except the stack's own project -- but making its code
importable by the backend means editing that one compose line, with the right
trailing ``/src`` for the checkout's layout. This does both::

    uv run src_projects.py clone https://github.com/soliplex/soliplex
    uv run src_projects.py list
    uv run src_projects.py add <name>        # a checkout you cloned yourself
    uv run src_projects.py remove <name>     # off the path; checkout untouched

``PYTHONPATH`` makes a checkout's *source* importable; it does not install
its dependencies, which the backend image resolves at build time. So ``add``
and ``clone`` read the checkout's declared runtime dependencies and check them
against what the backend image actually has, reporting any that would fail at
import time and what to do about them.

Each project on the path is also watched: the backend's serve command carries
one ``--reload-dirs`` flag per ``PYTHONPATH`` entry, which ``add``, ``clone``
and ``remove`` keep in step, so editing any of them restarts the server.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

from soliplex_plumber import rooms

# Where the stack's ``src/`` is mounted inside the backend container.
CONTAINER_SRC = "/app/src"

# The backend's PYTHONPATH assignment. Exactly one line in a generated stack
# (other mentions are comments, which this deliberately does not match).
_PYTHONPATH = re.compile(
    r"^(?P<indent>[ \t]*)PYTHONPATH:[ \t]*(?P<value>\S*)[ \t]*$", re.M
)


class SrcProjectError(Exception):
    """A src/ project directory could not be managed."""


class PythonPathMissing(SrcProjectError):
    def __init__(self, compose: pathlib.Path):
        super().__init__(
            f"{compose}: no 'PYTHONPATH:' line found in the backend service. "
            "This stack predates the src/ project layout (run "
            "migrate_layout.py) or has drifted from the exemplar."
        )


class PythonPathAmbiguous(SrcProjectError):
    def __init__(self, compose: pathlib.Path, count: int):
        super().__init__(
            f"{compose}: found {count} 'PYTHONPATH:' lines; expected exactly "
            "one (the backend's). Edit it by hand."
        )


class UrlPassedToAdd(SrcProjectError):
    def __init__(self, url: str):
        super().__init__(
            f"'{url}' looks like a clone URL, and 'add' takes the name of a "
            f"directory already under src/. To fetch it, use:\n"
            f"    src_projects.py clone {url}"
        )


class ProjectMissing(SrcProjectError):
    def __init__(self, path: pathlib.Path):
        super().__init__(
            f"{path} does not exist; clone it first (or use 'clone')"
        )


class ProjectExists(SrcProjectError):
    def __init__(self, path: pathlib.Path):
        super().__init__(f"{path} already exists; remove it or pass --name")


class CloneFailed(SrcProjectError):
    def __init__(self, url: str, detail: str):
        super().__init__(f"git clone {url} failed: {detail.strip()}")


class RefusingOwnProject(SrcProjectError):
    def __init__(self, name: str):
        super().__init__(
            f"'{name}' is this stack's own project; taking it off PYTHONPATH "
            "would break the dotted tool / router names in "
            "backend/environment/ (pass --force if you mean it)"
        )


def _require(cond: bool, exc: SrcProjectError) -> None:
    if not cond:
        raise exc


# --------------------------------------------------------------------------
# Stack inspection
# --------------------------------------------------------------------------
def own_package(project: pathlib.Path) -> str | None:
    """Return the stack's own package name from its generation manifest."""
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return None
    params = (
        data.get("tool", {}).get("soliplex-template", {}).get("params", {})
    )
    name = params.get("package_name")
    return name if isinstance(name, str) and name else None


def project_dirs(project: pathlib.Path) -> list[str]:
    """Return the names of the project directories under ``src/``."""
    src = project / "src"
    if not src.is_dir():
        return []
    return sorted(child.name for child in src.iterdir() if child.is_dir())


def container_path(project: pathlib.Path, name: str) -> str:
    """Return the import root for ``src/<name>`` as the container sees it.

    A checkout using a ``src/`` layout (most modern projects) puts its
    packages one level down; a flat-layout repo has them at its root. Point
    at whichever directory *contains* the importable package.
    """
    base = f"{CONTAINER_SRC}/{name}"
    return f"{base}/src" if (project / "src" / name / "src").is_dir() else base


# --------------------------------------------------------------------------
# Dependency check: PYTHONPATH makes source importable, not installed
# --------------------------------------------------------------------------
# The leading distribution name of a PEP 508 requirement ("httpx[http2]>=0.27",
# "pkg ; python_version < '3.13'", "pkg @ https://..." all yield "pkg").
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """PEP 503 normalization, so 'Foo_Bar' and 'foo-bar' compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_dependencies(project: pathlib.Path, name: str) -> list[str]:
    """Runtime distribution names declared by the checkout at ``src/<name>``.

    Only ``[project].dependencies`` -- dependency *groups* are the checkout's
    own dev tooling, which the backend never imports.
    """
    pyproject = project / "src" / name / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return []
    names = []
    for dep in data.get("project", {}).get("dependencies", []):
        match = _REQ_NAME.match(dep) if isinstance(dep, str) else None
        if match:
            names.append(match.group(1))
    return names


# Listing the backend image's own distributions, from inside it. '--no-deps'
# keeps postgres and friends out of it; this is a read-only one-off container,
# the same shape soliplex_config.py and rag_db.py use.
_LIST_DISTS = (
    "import importlib.metadata as m, json; "
    "print(json.dumps(sorted(d.name for d in m.distributions() if d.name)))"
)


def backend_distributions(project: pathlib.Path) -> set[str] | None:
    """Return what the backend image has installed, or None if unknowable."""
    if shutil.which("docker") is None:
        return None
    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "backend",
            "/app/.venv/bin/python",
            "-c",
            _LIST_DISTS,
        ],
        cwd=project,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        names = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return {_normalize(name) for name in names}


def unsatisfied_dependencies(
    project: pathlib.Path, name: str
) -> tuple[list[str], bool]:
    """Return ``(names the backend image lacks, whether it could be checked)``.

    An unverifiable check (no docker, image not built, compose refused) is
    reported as such rather than guessed at: the caller lists the declared
    dependencies instead of claiming they are missing.
    """
    declared = declared_dependencies(project, name)
    if not declared:
        return [], True
    installed = backend_distributions(project)
    if installed is None:
        return declared, False
    return [dep for dep in declared if _normalize(dep) not in installed], True


# --------------------------------------------------------------------------
# The PYTHONPATH line
# --------------------------------------------------------------------------
def read_entries(text: str, compose: pathlib.Path) -> list[str]:
    matches = _PYTHONPATH.findall(text)
    _require(bool(matches), PythonPathMissing(compose))
    _require(len(matches) == 1, PythonPathAmbiguous(compose, len(matches)))
    value = matches[0][1]
    return [entry for entry in value.split(":") if entry]


def write_entries(text: str, entries: list[str]) -> str:
    joined = ":".join(entries)
    return _PYTHONPATH.sub(
        lambda m: f"{m.group('indent')}PYTHONPATH: {joined}", text, count=1
    )


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------
# The serve command's reload flags.
#
# 'soliplex-cli serve' reloads only when given a '--reload=<mode>':
# - 'config' watches backend/environment/
# - 'python' watches 'soliplex.__path__'
# - 'both' watches the two.
#
# Any mode counts as reload on, including ones this script does not know:
# for instance, soliplex#1442 proposes a new 'explicit' mode that watches
# only explicitly-passed '--reload-dirs'.
#
# Each '--reload-dirs=<dir>' adds a directory to whatever the mode watches
# (for uvicorn's default '*.py' pattern), and does nothing without a mode.
#
# This script keeps one '--reload-dirs' per PYTHONPATH entry, so editing any
# project on the path restarts the server.
# --------------------------------------------------------------------------

# A generated stack folds the serve command ('>-'), one flag per line;
# older stacks keep it on one quoted line.
# Both patterns capture the separator before their flag -- a newline and
# indent, or spaces -- so a flag added after one takes the same form.
_RELOAD = re.compile(
    r"(?P<sep>\n[ \t]*|[ \t]+)--reload=(?P<mode>[A-Za-z][\w-]*)"
)
_RELOAD_DIR = re.compile(
    r"(?P<sep>\n[ \t]*|[ \t]+)--reload-dirs=(?P<dir>[^\s\"']+)"
)


def read_reload(text: str) -> str | None:
    """The serve command's reload mode, or None without a '--reload'."""
    match = _RELOAD.search(text)
    return match.group("mode") if match else None


def write_reload(text: str, mode: str) -> str:
    return _RELOAD.sub(
        lambda m: f"{m.group('sep')}--reload={mode}", text, count=1
    )


def read_reload_dirs(text: str) -> list[str]:
    return [m.group("dir") for m in _RELOAD_DIR.finditer(text)]


def add_reload_dir(text: str, path: str) -> str:
    """Add '--reload-dirs=<path>' after the last one, else after '--reload'.

    The caller has checked that the command has a '--reload' flag. The new
    flag copies the separator of the one it follows.
    """
    anchor = ([*_RELOAD_DIR.finditer(text)] or [_RELOAD.search(text)])[-1]
    flag = f"{anchor.group('sep')}--reload-dirs={path}"
    return text[: anchor.end()] + flag + text[anchor.end() :]


def remove_reload_dir(text: str, path: str) -> str:
    return _RELOAD_DIR.sub(
        lambda m: "" if m.group("dir") == path else m.group(0), text
    )


_BOTH_NOTE = (
    "  reload: '--reload=both' -> '--reload=config':\n"
    "         each project on the path is (or can be) watched through its\n"
    "         own '--reload-dirs', so the 'python' half (soliplex.__path__)\n"
    "         is no longer needed."
)
_PYTHON_NOTE = (
    "  reload: keeping '--reload=python':\n"
    "         it turns reload on, which the '--reload-dirs' flags need,\n"
    "         without watching backend/environment/.\n"
    "         '--reload=config' would work as well, and also restart on\n"
    "         config edits."
)
_OFF_NOTE = (
    "  reload: the backend serves without '--reload', so nothing is watched:\n"
    "         restart it to pick up edits, or add '--reload=config' to its\n"
    "         serve command and re-run 'add' to watch them."
)


def sync_reload(text: str, path: str, *, watch: bool) -> tuple[str, list[str]]:
    """Bring the reload flags in line with ``path`` on (or off) the path.

    Returns the updated compose text and the lines to print.

    - '--reload=both' becomes '--reload=config' (the 'python' half is
      what '--reload-dirs' now does).

    - '--reload=python' is kept, enabling reload without watching the config;

    - Any other mode is left as it is.

    - A config without any '--reload' means nothing is watched, so no
      '--reload-dirs' flag is added -- the caller still edits PYTHONPATH.
    """
    notes = []
    mode = read_reload(text)
    if mode == "both":
        text = write_reload(text, "config")
        notes.append(_BOTH_NOTE)
    elif mode == "python":
        notes.append(_PYTHON_NOTE)

    watched = path in read_reload_dirs(text)
    if watch and not watched:
        if mode is None:
            notes.append(_OFF_NOTE)
        else:
            text = add_reload_dir(text, path)
            notes.append(f"  reload: watching {path} ('--reload-dirs')")
    elif not watch and watched:
        text = remove_reload_dir(text, path)
        notes.append(f"  reload: no longer watching {path}")
    return text, notes


def _compose_path(project: pathlib.Path) -> pathlib.Path:
    return project / "docker-compose.yml"


def do_list(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    compose = _compose_path(project)
    text = compose.read_text()
    entries = read_entries(text, compose)
    watched = read_reload_dirs(text)
    mine = own_package(project)

    names = project_dirs(project)
    if not names:
        print("no project directories under src/")
        return 0

    def yes(cond: bool) -> str:
        return "yes" if cond else "no"

    print(f"{'project':<24} {'import root':<36} {'on PYTHONPATH':<14} watched")
    for name in names:
        path = container_path(project, name)
        mark = "*" if name == mine else " "
        print(
            f"{mark}{name:<23} {path:<36} {yes(path in entries):<14} "
            f"{yes(path in watched)}"
        )

    unknown = [
        e
        for e in entries
        if e not in {container_path(project, n) for n in names}
    ]
    for entry in unknown:
        print(
            f" {'(no such directory)':<23} {entry:<36} {'yes':<14} "
            f"{yes(entry in watched)}"
        )

    if mine in names:
        print("\n* this stack's own project")
    if read_reload(text) is None:
        print("\n(the backend serves without '--reload': nothing is watched)")
    return 0


# A clone URL rather than a directory name: 'scheme://...' or the scp-like
# 'git@host:owner/repo' form.
_URL_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")


def _add_entry(
    project: pathlib.Path, name: str, dry_run: bool
) -> tuple[str, str, list[str], bool]:
    """Put ``src/<name>`` on the backend's PYTHONPATH, and watch it.

    Returns (path, verb, reload notes, whether the compose file changed).
    Every check runs before the one write, so a refusal leaves the file as
    it was. An entry already on the path still gets its '--reload-dirs', so
    re-running 'add' brings an older stack's watch list into step.
    """
    _require(not _URL_LIKE.search(name), UrlPassedToAdd(name))
    _require(
        (project / "src" / name).is_dir(),
        ProjectMissing(project / "src" / name),
    )
    compose = _compose_path(project)
    before = compose.read_text()
    entries = read_entries(before, compose)
    path = container_path(project, name)

    fresh = path not in entries
    text = write_entries(before, [*entries, path]) if fresh else before
    text, notes = sync_reload(text, path, watch=True)

    changed = text != before
    if changed and not dry_run:
        compose.write_text(text)
    if not fresh:
        verb = "unchanged"
    else:
        verb = "would add" if dry_run else "added"
    return path, verb, notes, changed


def _report_dependencies(project: pathlib.Path, name: str, skip: bool) -> bool:
    """Report the checkout's dependencies against the backend image.

    Returns True when a rebuild is needed, so the caller can lead with that
    rather than with 'up -d backend', which would not fix it.
    """
    if skip:
        return False

    missing, checked = unsatisfied_dependencies(project, name)

    if checked and not missing:
        print(
            f"  dependencies: every runtime dependency src/{name} declares is "
            "already\n                in the backend image."
        )
        return False

    if not checked:
        print(
            "  dependencies: could not check against the backend image "
            "(no docker, or\n                the image is not built). "
            f"src/{name} declares:\n"
            + "".join(f"                  - {dep}\n" for dep in missing)
            + "                Make sure the backend image has these before "
            "importing it."
        )
        return False

    print(
        "  dependencies: the backend image is MISSING these, which "
        f"src/{name}\n                declares and will fail to import:\n"
        + "".join(f"                  - {dep}\n" for dep in missing)
        + "                PYTHONPATH does not install anything, so this "
        "needs a rebuild."
    )
    return True


def _report_added(
    project: pathlib.Path,
    name: str,
    path: str,
    verb: str,
    notes: list[str],
    changed: bool,
    dry_run: bool,
    skip_deps: bool,
) -> None:
    print(f"{verb}: {path}")
    fresh = verb != "unchanged"
    if not fresh:
        print(f"  src/{name} was already on the backend's PYTHONPATH")
    if dry_run:
        for line in notes:
            print(line)
        print("\n(dry run: nothing written)")
        return

    # The dependency report is about a checkout the backend is newly able to
    # import, so it only applies to a fresh entry.
    rebuild = (
        _report_dependencies(project, name, skip_deps) if fresh else False
    )
    for line in notes:
        print(line)

    if not changed:
        return

    steps = []
    if fresh:
        steps.append(
            f"  cd src/{name} && uv sync        # host-side work on it"
        )
    if rebuild:
        steps += [
            "  # add the missing distributions to "
            "backend/requirements.txt, then:",
            "  docker compose build backend",
        ]
    steps.append(
        "  docker compose up -d backend    # apply the compose change"
    )
    print("\nNext:\n" + "\n".join(steps))
    if fresh:
        print(
            "\nNothing to do for git: .gitignore already ignores everything "
            "under src/\nexcept this stack's own project."
        )


def do_add(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    path, verb, notes, changed = _add_entry(project, args.name, args.dry_run)
    _report_added(
        project,
        args.name,
        path,
        verb,
        notes,
        changed,
        args.dry_run,
        args.no_dep_check,
    )
    return 0


def derive_name(url: str) -> str:
    """Default directory name for a clone: the repo name in the URL."""
    return url.rstrip("/").split("/")[-1].removesuffix(".git")


def do_clone(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    name = args.name or derive_name(args.url)
    target = project / "src" / name
    _require(not target.exists(), ProjectExists(target))

    if args.dry_run:
        print(f"would clone {args.url} into src/{name}")
        print("would then put its package directory on the backend PYTHONPATH")
        print("\n(dry run: nothing written)")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", args.url, str(target)],
        capture_output=True,
        text=True,
    )
    _require(result.returncode == 0, CloneFailed(args.url, result.stderr))
    print(f"cloned {args.url} into src/{name}")

    if args.no_path:
        print("  (left off the backend's PYTHONPATH, as asked)")
        return 0

    path, verb, notes, changed = _add_entry(project, name, dry_run=False)
    _report_added(
        project,
        name,
        path,
        verb,
        notes,
        changed,
        False,
        args.no_dep_check,
    )
    return 0


def do_remove(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    compose = _compose_path(project)
    before = compose.read_text()
    entries = read_entries(before, compose)
    path = container_path(project, args.name)

    if not args.force:
        _require(
            args.name != own_package(project),
            RefusingOwnProject(args.name),
        )

    on_path = path in entries
    text = (
        write_entries(before, [e for e in entries if e != path])
        if on_path
        else before
    )
    text, notes = sync_reload(text, path, watch=False)

    if text == before:
        print(f"unchanged: src/{args.name} is not on the backend's PYTHONPATH")
        return 0

    if on_path:
        print(f"{'would remove' if args.dry_run else 'removed'}: {path}")
    else:
        print(f"src/{args.name} was not on the backend's PYTHONPATH")
    for line in notes:
        print(line)
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    compose.write_text(text)
    print(
        f"\nThe checkout at src/{args.name} is untouched; only the backend's "
        "import path\nand watch list changed. Run 'docker compose up -d "
        "backend' to apply it."
    )
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage the project directories under a Soliplex stack's src/ "
            "and the backend's PYTHONPATH."
        )
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def _dep_check_flag(sub):
        sub.add_argument(
            "--no-dep-check",
            action="store_true",
            help=(
                "skip checking the checkout's declared dependencies against "
                "the backend image (that check runs a one-off container)"
            ),
        )

    def common(sub):
        sub.add_argument(
            "--project-dir",
            default=".",
            help="the stack root (default: the current directory)",
        )
        sub.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change and write nothing",
        )
        return sub

    listing = common(subs.add_parser("list", help="show src/ projects"))
    listing.set_defaults(func=do_list)

    clone = common(
        subs.add_parser("clone", help="clone a repo into src/ and wire it up")
    )
    clone.add_argument("url", help="the repository to clone")
    clone.add_argument(
        "--name",
        default=None,
        help="directory name under src/ (default: the repo name)",
    )
    clone.add_argument(
        "--no-path",
        action="store_true",
        help="clone only; leave the backend's PYTHONPATH alone",
    )
    _dep_check_flag(clone)
    clone.set_defaults(func=do_clone)

    add = common(
        subs.add_parser("add", help="put an existing src/ project on the path")
    )
    add.add_argument(
        "name",
        help="directory name under src/ (not a clone URL -- see 'clone')",
    )
    _dep_check_flag(add)
    add.set_defaults(func=do_add)

    remove = common(
        subs.add_parser("remove", help="take a src/ project off the path")
    )
    remove.add_argument("name", help="directory name under src/")
    remove.add_argument(
        "--force",
        action="store_true",
        help="allow removing this stack's own project",
    )
    remove.set_defaults(func=do_remove)

    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except (SrcProjectError, rooms.AddRoomError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
