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
# The backend's 'soliplex-cli serve --reload=<mode>' flag. 'python' watches
# 'soliplex.__path__', so it only earns its keep when a checkout providing the
# 'soliplex' package is on PYTHONPATH -- which is exactly what this script
# arranges. One line in a generated stack.
_RELOAD = re.compile(r"--reload=(?P<mode>config|python|both)")


class ReloadFlagMissing(SrcProjectError):
    def __init__(self, compose: pathlib.Path):
        super().__init__(
            f"{compose}: no '--reload=' flag found on the backend's serve "
            "command; set the reload mode by hand."
        )


def read_reload(text: str, compose: pathlib.Path) -> str:
    match = _RELOAD.search(text)
    _require(match is not None, ReloadFlagMissing(compose))
    return match.group("mode")


def write_reload(text: str, mode: str) -> str:
    return _RELOAD.sub(f"--reload={mode}", text, count=1)


def provides_soliplex(project: pathlib.Path, name: str) -> bool:
    """True when ``src/<name>``'s import root holds a ``soliplex`` package.

    That is the case dev-mode cares about: the backend then imports soliplex
    from the checkout, and '--reload=python' can restart the server when it
    is edited.
    """
    root = container_path(project, name).removeprefix(f"{CONTAINER_SRC}/")
    return (project / "src" / root / "soliplex").is_dir()


def _compose_path(project: pathlib.Path) -> pathlib.Path:
    return project / "docker-compose.yml"


def do_list(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    compose = _compose_path(project)
    entries = read_entries(compose.read_text(), compose)
    mine = own_package(project)

    names = project_dirs(project)
    if not names:
        print("no project directories under src/")
        return 0

    print(f"{'project':<24} {'import root':<36} on PYTHONPATH")
    for name in names:
        path = container_path(project, name)
        flag = "yes" if path in entries else "no"
        mark = "*" if name == mine else " "
        print(f"{mark}{name:<23} {path:<36} {flag}")

    unknown = [
        e
        for e in entries
        if e not in {container_path(project, n) for n in names}
    ]
    for entry in unknown:
        print(f" {'(no such directory)':<23} {entry:<36} yes")

    if mine in names:
        print("\n* this stack's own project")
    return 0


def _apply(project: pathlib.Path, entries: list[str], dry_run: bool) -> None:
    compose = _compose_path(project)
    updated = write_entries(compose.read_text(), entries)
    if not dry_run:
        compose.write_text(updated)


# A clone URL rather than a directory name: 'scheme://...' or the scp-like
# 'git@host:owner/repo' form.
_URL_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")


def _add_entry(
    project: pathlib.Path, name: str, dry_run: bool
) -> tuple[str, str]:
    """Put ``src/<name>`` on the backend's PYTHONPATH; return (path, verb)."""
    _require(not _URL_LIKE.search(name), UrlPassedToAdd(name))
    _require(
        (project / "src" / name).is_dir(),
        ProjectMissing(project / "src" / name),
    )
    compose = _compose_path(project)
    entries = read_entries(compose.read_text(), compose)
    path = container_path(project, name)

    if path in entries:
        return path, "unchanged"

    _apply(project, [*entries, path], dry_run)
    return path, "would add" if dry_run else "added"


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


def _apply_reload(
    project: pathlib.Path, name: str, requested: bool
) -> str | None:
    """Switch the backend to '--reload=both', or hint that it could be.

    Returns a line to print, or None when the checkout has nothing to do with
    reloading. Only acts when asked: silently rewriting the serve command
    because of what a checkout happens to contain would be too much magic.
    """
    compose = _compose_path(project)
    text = compose.read_text()

    if not requested:
        if (
            provides_soliplex(project, name)
            and read_reload(text, compose) == "config"
        ):
            return (
                f"  reload: src/{name} provides the 'soliplex' package. Pass "
                "--reload-python to have\n          the backend restart when "
                "you edit it ('--reload=both')."
            )
        return None

    mode = read_reload(text, compose)
    if mode == "both":
        return "  reload: the backend already serves with '--reload=both'"
    compose.write_text(write_reload(text, "both"))
    return (
        f"  reload: backend serve flag '--reload={mode}' -> '--reload=both'; "
        "edits under\n          the checkout now restart the server."
    )


def _report_added(
    project: pathlib.Path,
    name: str,
    path: str,
    verb: str,
    dry_run: bool,
    skip_deps: bool,
    reload_python: bool = False,
) -> None:
    print(f"{verb}: {path}")
    fresh = verb != "unchanged"
    if not fresh:
        print(f"  src/{name} was already on the backend's PYTHONPATH")
    if dry_run:
        print("\n(dry run: nothing written)")
        return

    # The dependency report is about a checkout the backend is newly able to
    # import, so it only applies to a fresh entry.
    rebuild = (
        _report_dependencies(project, name, skip_deps) if fresh else False
    )

    # --reload-python is an explicit request, so honour it even when the path
    # entry was already there: re-running purely to change the reload mode is
    # a reasonable thing to do, and silently ignoring the flag would not be.
    reload_line = _apply_reload(project, name, reload_python)
    if reload_line:
        print(reload_line)

    if not fresh and not reload_python:
        return

    steps = []
    if fresh:
        steps.append(
            f"  cd src/{name} && uv sync        # host-side work on it"
        )
    if rebuild:
        steps += [
            "  # add the missing distributions to backend/constraints.txt "
            "and the",
            "  # 'uv add' line in backend/Dockerfile, then:",
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
    path, verb = _add_entry(project, args.name, args.dry_run)
    _report_added(
        project,
        args.name,
        path,
        verb,
        args.dry_run,
        args.no_dep_check,
        args.reload_python,
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

    path, verb = _add_entry(project, name, dry_run=False)
    _report_added(
        project,
        name,
        path,
        verb,
        False,
        args.no_dep_check,
        args.reload_python,
    )
    return 0


def do_remove(args: argparse.Namespace) -> int:
    project = rooms.resolve_project(args.project_dir)
    compose = _compose_path(project)
    entries = read_entries(compose.read_text(), compose)
    path = container_path(project, args.name)

    if not args.force:
        _require(
            args.name != own_package(project),
            RefusingOwnProject(args.name),
        )

    if path not in entries:
        print(f"unchanged: src/{args.name} is not on the backend's PYTHONPATH")
        return 0

    _apply(project, [e for e in entries if e != path], args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    print(f"{verb}: {path}")
    if args.dry_run:
        print("\n(dry run: nothing written)")
    else:
        print(
            f"\nThe checkout at src/{args.name} is untouched; only the import "
            "path changed.\nRun 'docker compose up -d backend' to apply it."
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

    def _reload_flag(sub):
        sub.add_argument(
            "--reload-python",
            action="store_true",
            help=(
                "also switch the backend's serve command to '--reload=both', "
                "so edits to a checkout providing the 'soliplex' package "
                "restart the server"
            ),
        )

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
    _reload_flag(clone)
    clone.set_defaults(func=do_clone)

    add = common(
        subs.add_parser("add", help="put an existing src/ project on the path")
    )
    add.add_argument(
        "name",
        help="directory name under src/ (not a clone URL -- see 'clone')",
    )
    _dep_check_flag(add)
    _reload_flag(add)
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
