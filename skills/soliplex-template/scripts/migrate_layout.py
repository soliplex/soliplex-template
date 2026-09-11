#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-plumber>=0.4"]
# ///
"""Convert a generated Soliplex stack to the src/ project layout (#176).

Stacks scaffolded before soliplex-template v0.16 put the project's package
directly under ``src/``, with its tests at the stack root::

    <stack>/
      pyproject.toml
      src/<package>/            # tools.py, views.py
      tests/unit/

Since #176 every entry under ``src/`` is a *project directory* of the same
shape, so a checkout cloned alongside this project needs no special casing::

    <stack>/
      pyproject.toml            # stack tooling only
      src/
        <package>/
          pyproject.toml
          src/<package>/        # tools.py, views.py
          tests/unit/
        <other>/                # anything you clone

Run it against a stack (``--project-dir`` defaults to the current
directory)::

    uv run migrate_layout.py --dry-run      # report the plan, change nothing
    uv run migrate_layout.py

Every edit asserts on the text it expects to find, so a stack that has
drifted from the exemplar fails loudly with the file and the anchor rather
than being half-converted. Nothing is written until every check has passed.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tomllib

from soliplex_plumber import rooms

# Scratch directory used to stage the move; it becomes the new ``src/``.
_STAGE = ".soliplex-migrate"

# The stack root stops being a distribution: these tables move to the
# project's own pyproject.toml.
_ROOT_DROPS = (
    "build-system",
    "tool.hatch.build.targets.wheel",
    "tool.pytest.ini_options",
)

_UV_TABLE = """\
# Nothing at the stack root is importable, so there is nothing to build here.
# This project's own library lives in its own project directory,
# src/{package}/ -- run `uv sync` / `uv run pytest` from there.
[tool.uv]
package = false
"""

_PROJECT_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# What the code under src/{package}/ imports. Host-side use:
#   cd src/{package}
#   uv sync                 # create/refresh the dev environment
#   uv run pytest           # run this project's tests
#   uv pip install -e .     # or a plain editable install
dependencies = [
    "soliplex {constraint}",
]

[dependency-groups]
dev = [
    "pytest",
]

# src/ layout: the importable package is src/{package}/ *within this
# project directory*. The Soliplex backend puts that directory on PYTHONPATH
# as /app/src/{package}/src (see the stack's docker-compose.yml); the
# build + test config below points at the same layout for host-side use.
[tool.hatch.build.targets.wheel]
packages = ["src/{package}"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]
"""

_GITIGNORE_RULE = """\

# Every entry under src/ is a project directory. This project's own
# package is tracked; anything else cloned there (the soliplex
# checkout, a third-party repo you want to hack on) belongs to its
# own repo, so it is ignored without needing a line per clone.
/src/*
!/src/{package}/
"""

_VENV_RULE = """\

# Virtualenvs (uv). Unanchored, so it also covers the per-project
# .venv under each project directory, not just the root one.
.venv/
"""


class MigrateError(Exception):
    """A migration could not be performed."""


class AlreadyMigrated(MigrateError):
    def __init__(self, project: pathlib.Path):
        super().__init__(
            f"{project} already uses the src/ project layout; nothing to do"
        )


class NotOldLayout(MigrateError):
    def __init__(self, project: pathlib.Path):
        super().__init__(
            f"{project} has no src/<package>/tools.py: it does not look like "
            "a stack scaffolded before the src/ project layout"
        )


class AmbiguousPackage(MigrateError):
    def __init__(self, names):
        joined = ", ".join(sorted(names))
        super().__init__(
            f"cannot tell which package is the stack's own ({joined}); "
            "pass --package-name"
        )


class DirtyTree(MigrateError):
    def __init__(self, project: pathlib.Path):
        super().__init__(
            f"{project} has uncommitted changes; commit or stash them so the "
            "migration is reviewable as a diff (or pass --force)"
        )


class NotAGitRepo(MigrateError):
    def __init__(self, project: pathlib.Path):
        super().__init__(
            f"{project} is not a git checkout; the migration moves tracked "
            "files with 'git mv' (or pass --force to move them in place)"
        )


class AnchorMissing(MigrateError):
    def __init__(self, rel: str, anchor: str):
        super().__init__(
            f"{rel}: expected to find {anchor!r}. This stack has drifted from "
            "the exemplar; reconcile that file by hand, then re-run (see the "
            "'Migrating to the src/ project layout' page)"
        )


class StagePresent(MigrateError):
    def __init__(self, path: pathlib.Path):
        super().__init__(
            f"{path} already exists; remove it (a previous run was "
            "interrupted) and try again"
        )


def _require(cond: bool, exc: MigrateError) -> None:
    if not cond:
        raise exc


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------
def read_manifest(project: pathlib.Path) -> dict:
    """Return ``[tool.soliplex-template.params]``, or an empty mapping."""
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError:
        return {}
    return data.get("tool", {}).get("soliplex-template", {}).get("params", {})


def resolve_package(project: pathlib.Path, override: str | None) -> str:
    """Return the import name of the stack's own package.

    Prefer the generation manifest, which records it verbatim; fall back to
    the single directory under ``src/`` holding a ``tools.py``.
    """
    if override is not None:
        return override

    recorded = read_manifest(project).get("package_name")
    if isinstance(recorded, str) and recorded:
        return recorded

    src = project / "src"
    candidates = [
        child.name
        for child in sorted(src.iterdir())
        if (child / "tools.py").is_file()
    ]
    _require(bool(candidates), NotOldLayout(project))
    _require(len(candidates) == 1, AmbiguousPackage(candidates))
    return candidates[0]


def is_migrated(project: pathlib.Path, package: str) -> bool:
    """True when the package already sits inside a project directory."""
    nested = project / "src" / package / "src" / package
    return nested.is_dir()


def git_tracked(project: pathlib.Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_dirty(project: pathlib.Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


# --------------------------------------------------------------------------
# Planning: each step returns the text it would write, asserting its anchors
# --------------------------------------------------------------------------
def split_tables(text: str) -> list[tuple[str | None, str]]:
    """Split TOML into ``(table name or None, chunk)`` in file order.

    A chunk carries the comment lines that precede its header, so comments
    travel with the table they document.
    """
    chunks: list[tuple[str | None, str]] = []
    name: str | None = None
    buf: list[str] = []
    pending: list[str] = []

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            chunks.append((name, "".join(buf)))
            name = stripped.strip("[]").strip()
            buf = pending + [line]
            pending = []
            continue
        if stripped.startswith("#") or not stripped:
            pending.append(line)
            continue
        buf.extend(pending)
        pending = []
        buf.append(line)

    chunks.append((name, "".join(buf) + "".join(pending)))
    return chunks


def plan_root_pyproject(text: str, package: str) -> str:
    """Return the stack root's pyproject.toml, stripped of the build config.

    ``[project]``, ``[dependency-groups]`` and the ``[tool.soliplex-template]``
    manifest stay exactly as the owner had them -- the root remains a tooling
    environment, it merely stops being a distribution.
    """
    chunks = split_tables(text)
    names = {name for name, _ in chunks if name}
    for dropped in _ROOT_DROPS:
        _require(dropped in names, AnchorMissing("pyproject.toml", dropped))
    _require("project" in names, AnchorMissing("pyproject.toml", "[project]"))

    kept = [chunk for name, chunk in chunks if name not in _ROOT_DROPS]
    out = "".join(kept).strip("\n") + "\n"

    # Slot [tool.uv] in ahead of the manifest, which stays last.
    marker = "[tool.soliplex-template]"
    uv_table = _UV_TABLE.format(package=package)
    if marker in out:
        head, _, tail = out.partition(marker)
        return f"{head.rstrip(chr(10))}\n\n{uv_table}\n{marker}{tail}"
    return f"{out}\n{uv_table}"


def plan_project_pyproject(project: pathlib.Path, package: str) -> str:
    params = read_manifest(project)
    return _PROJECT_PYPROJECT.format(
        project_name=params.get("project_name", package),
        package=package,
        constraint=params.get(
            "soliplex_backend_constraint", ">= 0.79, < 0.80"
        ),
    )


def plan_compose(text: str, package: str) -> str:
    """Repoint the backend's PYTHONPATH at the project's package directory."""
    old = "PYTHONPATH: /app/src\n"
    _require(
        text.count(old) == 1,
        AnchorMissing("docker-compose.yml", old.strip()),
    )
    return text.replace(old, f"PYTHONPATH: /app/src/{package}/src\n")


def plan_gitignore(text: str, package: str) -> str:
    """Append the src/ project-directory rule (and .venv/, if absent)."""
    _require(
        "/src/" not in text,
        AnchorMissing(".gitignore", "no existing /src/ rule"),
    )
    out = text.rstrip("\n") + "\n"
    if ".venv" not in out:
        out += _VENV_RULE
    return out + _GITIGNORE_RULE.format(package=package)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def move_tree(project: pathlib.Path, package: str, *, use_git: bool) -> None:
    """Reshape src/ and tests/ into src/<package>/{src,tests}."""
    stage = project / _STAGE
    _require(not stage.exists(), StagePresent(stage))

    holder = stage / package
    holder.mkdir(parents=True)

    def move(rel: str) -> None:
        source = project / rel
        if not source.exists():
            return
        if use_git:
            subprocess.run(
                ["git", "mv", rel, f"{_STAGE}/{package}"],
                cwd=project,
                check=True,
                capture_output=True,
            )
        else:
            shutil.move(str(source), str(holder / rel))

    move("src")
    move("tests")

    if use_git:
        subprocess.run(
            ["git", "mv", _STAGE, "src"],
            cwd=project,
            check=True,
            capture_output=True,
        )
    else:
        shutil.move(str(stage), str(project / "src"))


def migrate(
    project: pathlib.Path,
    package: str,
    *,
    use_git: bool,
    dry_run: bool,
) -> list[str]:
    """Run the migration; return the human-readable list of actions.

    Every file edit is *planned* (and its anchors asserted) before anything
    is written, so an unmigratable stack is left untouched.
    """
    compose_path = project / "docker-compose.yml"
    root_path = project / "pyproject.toml"
    gitignore_path = project / ".gitignore"

    root_text = plan_root_pyproject(root_path.read_text(), package)
    project_text = plan_project_pyproject(project, package)
    compose_text = plan_compose(compose_path.read_text(), package)
    gitignore_text = (
        plan_gitignore(gitignore_path.read_text(), package)
        if gitignore_path.is_file()
        else None
    )

    actions = [
        f"move src/ and tests/ into src/{package}/",
        "rewrite pyproject.toml (stack root: tooling only)",
        f"write src/{package}/pyproject.toml (the project's own)",
        f"repoint docker-compose.yml PYTHONPATH at /app/src/{package}/src",
    ]
    if gitignore_text is not None:
        actions.append("append the src/ project-directory rule to .gitignore")

    if dry_run:
        return actions

    move_tree(project, package, use_git=use_git)
    root_path.write_text(root_text)
    (project / "src" / package / "pyproject.toml").write_text(project_text)
    compose_path.write_text(compose_text)
    if gitignore_text is not None:
        gitignore_path.write_text(gitignore_text)
    return actions


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a generated Soliplex stack to the src/ project layout."
        )
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="the stack root (default: the current directory)",
    )
    parser.add_argument(
        "--package-name",
        default=None,
        help=(
            "the stack's own package (default: from the generation manifest, "
            "else inferred from src/<pkg>/tools.py)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and write nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "proceed with a dirty or non-git working tree (the migration is "
            "then not reviewable as a diff)"
        ),
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    project = rooms.resolve_project(args.project_dir)
    package = resolve_package(project, args.package_name)

    _require(not is_migrated(project, package), AlreadyMigrated(project))
    _require(
        (project / "src" / package / "tools.py").is_file(),
        NotOldLayout(project),
    )

    use_git = git_tracked(project)
    if not args.force and not args.dry_run:
        _require(use_git, NotAGitRepo(project))
        _require(not git_dirty(project), DirtyTree(project))

    actions = migrate(project, package, use_git=use_git, dry_run=args.dry_run)

    lead = "would" if args.dry_run else "did"
    print(f"{lead}, for package '{package}' in {project}:")
    for action in actions:
        print(f"  - {action}")

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    print(
        "\nNext:\n"
        f"  cd src/{package} && uv sync && uv run pytest\n"
        "  uv sync && uv run zensical build   # from the stack root\n"
        "  docker compose up -d backend       # pick up the new PYTHONPATH\n"
        "\nReview the diff before committing. Comments in installation.yaml "
        "and\nrooms/custom/room_config.yaml still name the old paths; update "
        "them at\nyour leisure."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except (MigrateError, rooms.AddRoomError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
