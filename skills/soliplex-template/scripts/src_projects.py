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
against what the backend image actually has -- missing, or installed at a
version the checkout does not accept -- reporting what to do about each.

Each project on the path is also watched: the backend's serve command carries
one ``--reload-dirs`` flag per ``PYTHONPATH`` entry, which ``add``, ``clone``
and ``remove`` keep in step, so editing any of them restarts the server.

And each project's ``dev`` dependency group (pytest and the like) is built
into the dev-mode ``soliplex-dev`` service, so its tests run against the
backend's own versions: ``dev-requirements`` regenerates
``soliplex-dev/requirements-dev.txt`` from every ``src/`` project, and
``add`` and ``clone`` do the same as they go.
"""

from __future__ import annotations

import argparse
import enum
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import typing

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
class DependencyStatus(enum.StrEnum):
    """How the backend image meets one of a checkout's requirements."""

    SATISFIED = enum.auto()  # installed, at a version the requirement accepts
    MISSING = enum.auto()  # not installed at all
    CONFLICT = enum.auto()  # installed, at a version the requirement rejects


class DependencyCheck(typing.NamedTuple):
    requirement: str
    status: DependencyStatus
    installed: str | None  # the image's version; None when MISSING


def declared_dependencies(project: pathlib.Path, name: str) -> list[str]:
    """The runtime requirements declared by the checkout at ``src/<name>``.

    Only ``[project].dependencies`` -- dependency *groups* are the checkout's
    own dev tooling, which the backend never imports. Only these *direct*
    requirements are checked: a conflict deeper in the checkout's tree shows
    up when it is imported. To catch one before the backend does, import the
    checkout in the backend's environment: see "Checking it in the backend's
    environment" in the stack's docs/custom-package.md, or
    https://soliplex.github.io/soliplex-template/users/custom-package/#checking-it-in-the-backends-environment
    """
    pyproject = project / "src" / name / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return []
    return [
        dep.strip()
        for dep in data.get("project", {}).get("dependencies", [])
        if isinstance(dep, str) and dep.strip()
    ]


# Run inside the backend image, which is where the requirements have to hold:
# each one's environment marker is evaluated for the image's Python, and its
# specifier against the version the image has installed. 'packaging' is one of
# soliplex's own dependencies, so the image always has it. A requirement it
# cannot parse is left out, as is one whose marker excludes this environment.
# Prints one '[requirement, status, installed version]' record per requirement.
_CHECK_DEPS = """\
import importlib.metadata as metadata, json, sys
from packaging import requirements
records = []
for text in json.loads(sys.argv[1]):
    try:
        req = requirements.Requirement(text)
    except requirements.InvalidRequirement:
        continue
    if req.marker is not None and not req.marker.evaluate({"extra": ""}):
        continue
    try:
        version = metadata.version(req.name)
    except metadata.PackageNotFoundError:
        records.append([text, "missing", None])
        continue
    ok = req.specifier.contains(version, prereleases=True)
    records.append([text, "satisfied" if ok else "conflict", version])
print(json.dumps(records))
"""


def backend_check(
    project: pathlib.Path, requirements: list[str]
) -> list[DependencyCheck] | None:
    """Check ``requirements`` inside the backend image; None if unknowable.

    A one-off container ('--no-deps' keeps postgres and friends out of it),
    the same shape soliplex_config.py and rag_db.py use. No docker, an image
    that is not built, or one that cannot run the check all come back as
    None, never as a guess.
    """
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
            _CHECK_DEPS,
            json.dumps(requirements),
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
        records = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return [
        DependencyCheck(text, DependencyStatus(status), installed)
        for text, status, installed in records
    ]


def check_dependencies(
    project: pathlib.Path, name: str
) -> tuple[list[str], list[DependencyCheck] | None]:
    """Return ``(declared requirements, their checks or None)`` for a checkout.

    The checks are None when the backend image could not be asked; the caller
    then lists what the checkout declares rather than claiming anything is
    missing.
    """
    declared = declared_dependencies(project, name)
    if not declared:
        return [], []
    return declared, backend_check(project, declared)


# --------------------------------------------------------------------------
# The soliplex-dev service's dev requirements: soliplex-template#213
# --------------------------------------------------------------------------
# soliplex-dev/Dockerfile installs this file into the backend's image, with
# the image's own versions frozen as constraints. It is committed, so a change
# to the dev set shows in review; this script is what writes it.
DEV_REQUIREMENTS = "soliplex-dev/requirements-dev.txt"
DEV_GROUP = "dev"
DEV_REQUIREMENTS_HEADER = """\
# Dev dependencies for the 'soliplex-dev' service: the 'dev' dependency group
# of every project under src/, includes expanded. Generated by src_projects.py
# from the projects' pyproject.toml files -- do not edit; regenerate with
# 'uv run <skill>/scripts/src_projects.py dev-requirements', then rebuild with
# 'docker compose build soliplex-dev'.
"""


class DevGroupError(SrcProjectError):
    """A project's dependency groups cannot be expanded."""


class GroupIncludesItself(DevGroupError):
    def __init__(self, name: str):
        super().__init__(f"dependency group {name!r} includes itself")


class IncludedGroupMissing(DevGroupError):
    def __init__(self, name: str):
        super().__init__(f"included dependency group {name!r} is missing")


class NoSoliplexDev(SrcProjectError):
    def __init__(self, project: pathlib.Path):
        super().__init__(
            f"{project}: no soliplex-dev/ directory. This stack predates the "
            "soliplex-dev build context; see 'Upgrading an existing stack' in "
            "the soliplex-template docs."
        )


def _normalize(name: str) -> str:
    """PEP 503 normalization, which PEP 735 applies to group names too."""
    return re.sub(r"[-_.]+", "-", name).lower()


def group_requirements(
    groups: dict, name: str, seen: tuple[str, ...] = ()
) -> list[str]:
    """The requirement strings of group ``name``, PEP 735 includes expanded.

    Group names normalize as distribution names do. A missing top-level
    group is simply empty; a missing *included* group, or a group that
    includes itself, is a :class:`DevGroupError`.
    """
    table = {_normalize(key): value for key, value in groups.items()}
    key = _normalize(name)
    if key in seen:
        raise GroupIncludesItself(name)
    if key not in table:
        _require(not seen, IncludedGroupMissing(name))
        return []

    requirements = []
    for entry in table[key]:
        if isinstance(entry, str):
            requirements.append(entry)
        elif isinstance(entry, dict) and "include-group" in entry:
            requirements += group_requirements(
                groups, entry["include-group"], (*seen, key)
            )
    return requirements


def collect_dev_requirements(project: pathlib.Path) -> str:
    """The text of soliplex-dev/requirements-dev.txt for this stack.

    Every project directly under ``src/`` contributes its ``dev`` group,
    under a ``# src/<project>`` line; one whose ``pyproject.toml`` does not
    parse, or whose groups are malformed, gets a line saying it was skipped.
    """
    lines = []
    for pyproject in sorted((project / "src").glob("*/pyproject.toml")):
        name = pyproject.parent.name
        try:
            data = tomllib.loads(pyproject.read_text())
            requirements = group_requirements(
                data.get("dependency-groups", {}), DEV_GROUP
            )
        except (tomllib.TOMLDecodeError, DevGroupError) as exc:
            lines.append(f"# src/{name}: skipped ({exc})")
            continue
        if requirements:
            lines += [f"# src/{name}", *requirements]
    return DEV_REQUIREMENTS_HEADER + "".join(f"{line}\n" for line in lines)


def sync_dev_requirements(project: pathlib.Path, dry_run: bool) -> bool | None:
    """Regenerate soliplex-dev/requirements-dev.txt.

    Returns whether it changed (or would, with ``dry_run``), or None for a
    stack without a soliplex-dev/ build context, which is left alone.
    """
    path = project / DEV_REQUIREMENTS
    if not path.parent.is_dir():
        return None
    text = collect_dev_requirements(project)
    if path.is_file() and path.read_text() == text:
        return False
    if not dry_run:
        path.write_text(text)
    return True


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


_INDENT = " " * 16


def _report_dependencies(project: pathlib.Path, name: str, skip: bool) -> bool:
    """Report the checkout's dependencies against the backend image.

    Returns True when a rebuild is needed, so the caller can lead with that
    rather than with 'up -d backend', which would not fix it.
    """
    if skip:
        return False

    declared, checks = check_dependencies(project, name)

    if checks is None:
        print(
            "  dependencies: could not check against the backend image "
            f"(no docker, or\n{_INDENT}the image is not built). "
            f"src/{name} declares:\n"
            + "".join(f"{_INDENT}  - {dep}\n" for dep in declared)
            + f"{_INDENT}Make sure the backend image has these before "
            "importing it."
        )
        return False

    def having(status: DependencyStatus) -> list[DependencyCheck]:
        return [check for check in checks if check.status is status]

    missing = having(DependencyStatus.MISSING)
    conflicts = having(DependencyStatus.CONFLICT)

    if not missing and not conflicts:
        print(
            f"  dependencies: every runtime dependency src/{name} declares is "
            f"already\n{_INDENT}in the backend image, at a version it accepts."
        )
        return False

    if missing:
        print(
            "  dependencies: the backend image is MISSING these, which "
            f"src/{name}\n{_INDENT}declares and will fail to import:\n"
            + "".join(f"{_INDENT}  - {c.requirement}\n" for c in missing)
            + f"{_INDENT}PYTHONPATH does not install anything, so this "
            "needs a rebuild."
        )
    if conflicts:
        print(
            f"  dependencies: CONFLICT: src/{name} rejects the version the "
            f"backend\n{_INDENT}image has of these:\n"
            + "".join(
                f"{_INDENT}  - {c.requirement} (the image has {c.installed})\n"
                for c in conflicts
            )
            + f"{_INDENT}backend/requirements.txt cannot fix this: the "
            f"image's own\n{_INDENT}versions are frozen as constraints. "
            f"Instead:\n"
            f"{_INDENT}  1. adapt src/{name} to the image's versions "
            f"(relax its\n{_INDENT}     requirement, port its code);\n"
            f"{_INDENT}  2. pick a soliplex image tag whose release has "
            f"versions it\n{_INDENT}     accepts (the FROM line in "
            "backend/Dockerfile);\n"
            f"{_INDENT}  3. customize backend/Dockerfile."
        )
    return bool(missing)


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

    dev_changed = sync_dev_requirements(project, dry_run)
    if dev_changed:
        notes = [
            *notes,
            f"  dev requirements: {'would update' if dry_run else 'updated'} "
            f"{DEV_REQUIREMENTS};\n{_INDENT}rebuild soliplex-dev to install "
            "them.",
        ]

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

    if not (changed or dev_changed):
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
    if dev_changed:
        steps.append(
            "  docker compose build soliplex-dev  # install dev requirements"
        )
    if changed:
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


def do_dev_requirements(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    path = project / DEV_REQUIREMENTS
    _require(path.parent.is_dir(), NoSoliplexDev(project))

    if args.check:
        current = path.read_text() if path.is_file() else ""
        if current == collect_dev_requirements(project):
            print(f"up to date: {DEV_REQUIREMENTS}")
            return 0
        print(
            f"stale: {DEV_REQUIREMENTS} differs from the src/ projects' dev "
            "groups;\nrun 'src_projects.py dev-requirements' to regenerate it."
        )
        return 1

    if not sync_dev_requirements(project, args.dry_run):
        print(f"unchanged: {DEV_REQUIREMENTS}")
        return 0
    print(
        f"{'would update' if args.dry_run else 'updated'}: {DEV_REQUIREMENTS}"
    )
    if args.dry_run:
        print("\n(dry run: nothing written)")
    else:
        print(
            "\nNext:\n"
            "  docker compose build soliplex-dev  # install dev requirements"
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

    dev = common(
        subs.add_parser(
            "dev-requirements",
            help=(
                "regenerate soliplex-dev/requirements-dev.txt from the src/ "
                "projects' dev groups"
            ),
        )
    )
    dev.add_argument(
        "--check",
        action="store_true",
        help="report whether it is up to date (exit 1 if not); write nothing",
    )
    dev.set_defaults(func=do_dev_requirements)

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
