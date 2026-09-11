"""Unit tests for the bundled ``skill/scripts/migrate_layout.py`` CLI.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``
(mirroring ``test_add_room.py``).

Hermetic: every stack is synthesized under ``tmp_path``; the only subprocess
is ``git``, run against those throwaway checkouts. AAA layout, single act per
test.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import tomllib

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "migrate_layout.py"
)
_spec = importlib.util.spec_from_file_location("migrate_layout", _MODULE_PATH)
migrate_layout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_layout)


# The shape of a stack scaffolded before the src/ project layout.
_OLD_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "acme-widgets"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "soliplex >= 0.79, < 0.80",
    "psycopg[binary]",
    "asyncpg",
]

[dependency-groups]
dev = [
    "pytest",
    # Builds the documentation site.
    "zensical",
]

[tool.hatch.build.targets.wheel]
packages = ["src/acme_widgets"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]

[tool.soliplex-template]
skill_name = "soliplex-template"

[tool.soliplex-template.params]
project_name = "acme-widgets"
package_name = "acme_widgets"
soliplex_backend_constraint = ">= 0.79, < 0.80"
"""

_OLD_COMPOSE = """\
services:
  backend:
    environment:
      OLLAMA_BASE_URL: http://h:11434
      PYTHONPATH: /app/src
"""

_OLD_GITIGNORE = "# Docker secrets\n.secrets/\n\n*.pyc\n"


# Committing identity for the throwaway repos these tests build.
# gpgsign=false because the host's commit.gpgsign would otherwise invoke a
# real signing key and block on a pinentry prompt -- the same reason the
# generator has --disable-gpg-sign.
_GIT = [
    "git",
    "-c",
    "user.email=a@b",
    "-c",
    "user.name=a",
    "-c",
    "commit.gpgsign=false",
]


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def old_stack(tmp_path):
    """A minimal stack in the pre-#176 layout (no git)."""
    _write(tmp_path / "docker-compose.yml", _OLD_COMPOSE)
    _write(tmp_path / "backend/environment/installation.yaml", "id: x\n")
    _write(tmp_path / "pyproject.toml", _OLD_PYPROJECT)
    _write(tmp_path / ".gitignore", _OLD_GITIGNORE)
    _write(
        tmp_path / "src/acme_widgets/tools.py",
        "def greeting(n):\n    return n\n",
    )
    _write(tmp_path / "src/acme_widgets/views.py", "router = None\n")
    _write(tmp_path / "tests/unit/test_tools.py", "def test_x():\n    pass\n")
    return tmp_path


@pytest.fixture
def old_stack_git(old_stack):
    """The same stack, as a clean git checkout."""
    subprocess.run(["git", "init", "-q", "."], cwd=old_stack, check=True)
    subprocess.run(["git", "add", "-A"], cwd=old_stack, check=True)
    subprocess.run(
        [*_GIT, "commit", "-q", "-m", "pre"], cwd=old_stack, check=True
    )
    return old_stack


# --------------------------------------------------------------------------
# read_manifest
# --------------------------------------------------------------------------
def test_read_manifest_returns_params(old_stack):
    result = migrate_layout.read_manifest(old_stack)

    assert result["package_name"] == "acme_widgets"


def test_read_manifest_missing_file(tmp_path):
    result = migrate_layout.read_manifest(tmp_path)

    assert result == {}


def test_read_manifest_unparseable(tmp_path):
    _write(tmp_path / "pyproject.toml", "this is not = = toml\n")

    result = migrate_layout.read_manifest(tmp_path)

    assert result == {}


def test_read_manifest_without_the_table(tmp_path):
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')

    result = migrate_layout.read_manifest(tmp_path)

    assert result == {}


# --------------------------------------------------------------------------
# resolve_package
# --------------------------------------------------------------------------
def test_resolve_package_override_wins(old_stack):
    result = migrate_layout.resolve_package(old_stack, "explicit")

    assert result == "explicit"


def test_resolve_package_from_manifest(old_stack):
    # The manifest records it verbatim, so no filesystem guessing is needed.
    result = migrate_layout.resolve_package(old_stack, None)

    assert result == "acme_widgets"


def test_resolve_package_inferred_without_manifest(old_stack):
    (old_stack / "pyproject.toml").write_text('[project]\nname = "x"\n')

    result = migrate_layout.resolve_package(old_stack, None)

    assert result == "acme_widgets"


def test_resolve_package_none_found(tmp_path):
    (tmp_path / "src").mkdir()

    with pytest.raises(migrate_layout.NotOldLayout, match="tools.py"):
        migrate_layout.resolve_package(tmp_path, None)


def test_resolve_package_ambiguous(tmp_path):
    for name in ("one", "two"):
        _write(tmp_path / "src" / name / "tools.py", "")

    with pytest.raises(migrate_layout.AmbiguousPackage, match="one, two"):
        migrate_layout.resolve_package(tmp_path, None)


# --------------------------------------------------------------------------
# is_migrated / git probes
# --------------------------------------------------------------------------
def test_is_migrated_false_for_old_layout(old_stack):
    result = migrate_layout.is_migrated(old_stack, "acme_widgets")

    assert result is False


def test_is_migrated_true_once_nested(old_stack):
    (old_stack / "src/acme_widgets/src/acme_widgets").mkdir(parents=True)

    result = migrate_layout.is_migrated(old_stack, "acme_widgets")

    assert result is True


def test_git_tracked_false_outside_a_checkout(tmp_path):
    result = migrate_layout.git_tracked(tmp_path)

    assert result is False


def test_git_tracked_true_inside_a_checkout(old_stack_git):
    result = migrate_layout.git_tracked(old_stack_git)

    assert result is True


def test_git_dirty_false_when_clean(old_stack_git):
    result = migrate_layout.git_dirty(old_stack_git)

    assert result is False


def test_git_dirty_true_with_changes(old_stack_git):
    (old_stack_git / "pyproject.toml").write_text("# edited\n")

    result = migrate_layout.git_dirty(old_stack_git)

    assert result is True


# --------------------------------------------------------------------------
# split_tables
# --------------------------------------------------------------------------
def test_split_tables_keeps_comments_with_their_table():
    text = "[a]\nx = 1\n\n# about b\n[b]\ny = 2\n"

    result = migrate_layout.split_tables(text)

    names = [name for name, _ in result]
    assert names == [None, "a", "b"]
    assert "# about b" in dict(result[1:])["b"]


def test_split_tables_preserves_a_trailing_comment():
    text = "[a]\nx = 1\n\n# dangling\n"

    result = migrate_layout.split_tables(text)

    assert "# dangling" in result[-1][1]


# --------------------------------------------------------------------------
# plan_root_pyproject
# --------------------------------------------------------------------------
def test_plan_root_pyproject_drops_only_the_build_config():
    result = migrate_layout.plan_root_pyproject(_OLD_PYPROJECT, "acme_widgets")

    data = tomllib.loads(result)
    assert "build-system" not in data
    assert "hatch" not in data.get("tool", {})
    assert "pytest" not in data.get("tool", {})
    # The owner's own tables survive untouched.
    assert data["project"]["dependencies"][0].startswith("soliplex")
    assert "zensical" in data["dependency-groups"]["dev"]
    assert data["tool"]["soliplex-template"]["params"]["package_name"] == (
        "acme_widgets"
    )
    assert data["tool"]["uv"]["package"] is False


def test_plan_root_pyproject_puts_uv_before_the_manifest():
    result = migrate_layout.plan_root_pyproject(_OLD_PYPROJECT, "acme_widgets")

    assert result.index("[tool.uv]") < result.index("[tool.soliplex-template]")


def test_plan_root_pyproject_appends_uv_without_a_manifest():
    text = _OLD_PYPROJECT.split("[tool.soliplex-template]")[0]

    result = migrate_layout.plan_root_pyproject(text, "acme_widgets")

    assert tomllib.loads(result)["tool"]["uv"]["package"] is False


def test_plan_root_pyproject_starts_at_the_first_table():
    result = migrate_layout.plan_root_pyproject(_OLD_PYPROJECT, "acme_widgets")

    assert result.startswith("[project]")


def test_plan_root_pyproject_requires_the_build_tables():
    text = '[project]\nname = "x"\n'

    with pytest.raises(migrate_layout.AnchorMissing, match="build-system"):
        migrate_layout.plan_root_pyproject(text, "acme_widgets")


def test_plan_root_pyproject_requires_a_project_table():
    text = _OLD_PYPROJECT.replace("[project]\n", "[other]\n", 1)

    with pytest.raises(migrate_layout.AnchorMissing, match=r"\[project\]"):
        migrate_layout.plan_root_pyproject(text, "acme_widgets")


# --------------------------------------------------------------------------
# plan_project_pyproject
# --------------------------------------------------------------------------
def test_plan_project_pyproject_uses_the_manifest(old_stack):
    result = migrate_layout.plan_project_pyproject(old_stack, "acme_widgets")

    data = tomllib.loads(result)
    assert data["project"]["name"] == "acme-widgets"
    assert data["project"]["dependencies"] == ["soliplex >= 0.79, < 0.80"]
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/acme_widgets"
    ]


def test_plan_project_pyproject_defaults_without_a_manifest(tmp_path):
    result = migrate_layout.plan_project_pyproject(tmp_path, "acme_widgets")

    assert tomllib.loads(result)["project"]["name"] == "acme_widgets"


# --------------------------------------------------------------------------
# plan_compose / plan_gitignore
# --------------------------------------------------------------------------
def test_plan_compose_repoints_pythonpath():
    result = migrate_layout.plan_compose(_OLD_COMPOSE, "acme_widgets")

    assert "PYTHONPATH: /app/src/acme_widgets/src\n" in result
    assert "PYTHONPATH: /app/src\n" not in result


def test_plan_compose_requires_the_anchor():
    with pytest.raises(migrate_layout.AnchorMissing, match="PYTHONPATH"):
        migrate_layout.plan_compose("services:\n", "acme_widgets")


def test_plan_gitignore_appends_the_rule_and_venv():
    result = migrate_layout.plan_gitignore(_OLD_GITIGNORE, "acme_widgets")

    assert result.endswith("/src/*\n!/src/acme_widgets/\n")
    assert ".venv/\n" in result


def test_plan_gitignore_leaves_an_existing_venv_rule_alone():
    result = migrate_layout.plan_gitignore(
        _OLD_GITIGNORE + ".venv/\n", "acme_widgets"
    )

    assert result.count(".venv/") == 1


def test_plan_gitignore_refuses_an_existing_src_rule():
    text = _OLD_GITIGNORE + "/src/soliplex/\n"

    with pytest.raises(migrate_layout.AnchorMissing, match="/src/"):
        migrate_layout.plan_gitignore(text, "acme_widgets")


# --------------------------------------------------------------------------
# move_tree
# --------------------------------------------------------------------------
def test_move_tree_without_git(old_stack):
    migrate_layout.move_tree(old_stack, "acme_widgets", use_git=False)

    assert (old_stack / "src/acme_widgets/src/acme_widgets/tools.py").is_file()
    assert (old_stack / "src/acme_widgets/tests/unit/test_tools.py").is_file()
    assert not (old_stack / "tests").exists()


def test_move_tree_with_git_records_renames(old_stack_git):
    migrate_layout.move_tree(old_stack_git, "acme_widgets", use_git=True)

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=old_stack_git,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status
    assert all(line.startswith("R") for line in status.splitlines())


def test_move_tree_tolerates_a_missing_tests_tree(old_stack):
    import shutil

    shutil.rmtree(old_stack / "tests")

    migrate_layout.move_tree(old_stack, "acme_widgets", use_git=False)

    assert (old_stack / "src/acme_widgets/src/acme_widgets/tools.py").is_file()


def test_move_tree_refuses_a_leftover_stage(old_stack):
    (old_stack / migrate_layout._STAGE).mkdir()

    with pytest.raises(migrate_layout.StagePresent, match="already exists"):
        migrate_layout.move_tree(old_stack, "acme_widgets", use_git=False)


# --------------------------------------------------------------------------
# migrate
# --------------------------------------------------------------------------
def test_migrate_dry_run_writes_nothing(old_stack):
    before = (old_stack / "pyproject.toml").read_text()

    actions = migrate_layout.migrate(
        old_stack, "acme_widgets", use_git=False, dry_run=True
    )

    assert len(actions) == 5
    assert (old_stack / "pyproject.toml").read_text() == before
    assert (old_stack / "src/acme_widgets/tools.py").is_file()


def test_migrate_applies_every_change(old_stack):
    actions = migrate_layout.migrate(
        old_stack, "acme_widgets", use_git=False, dry_run=False
    )

    assert len(actions) == 5
    root = tomllib.loads((old_stack / "pyproject.toml").read_text())
    assert "build-system" not in root
    assert (old_stack / "src/acme_widgets/pyproject.toml").is_file()
    assert (
        "PYTHONPATH: /app/src/acme_widgets/src"
        in (old_stack / "docker-compose.yml").read_text()
    )
    assert "!/src/acme_widgets/" in (old_stack / ".gitignore").read_text()


def test_migrate_without_a_gitignore(old_stack):
    (old_stack / ".gitignore").unlink()

    actions = migrate_layout.migrate(
        old_stack, "acme_widgets", use_git=False, dry_run=False
    )

    assert len(actions) == 4
    assert not (old_stack / ".gitignore").exists()


def test_migrate_leaves_the_stack_untouched_when_an_anchor_is_missing(
    old_stack,
):
    # Planning happens before any write, so a drifted compose file aborts the
    # whole migration rather than half-converting it.
    (old_stack / "docker-compose.yml").write_text("services:\n")

    with pytest.raises(migrate_layout.AnchorMissing, match="PYTHONPATH"):
        migrate_layout.migrate(
            old_stack, "acme_widgets", use_git=False, dry_run=False
        )

    assert (old_stack / "src/acme_widgets/tools.py").is_file()
    assert "[build-system]" in (old_stack / "pyproject.toml").read_text()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_parse_args_defaults():
    args = migrate_layout.parse_args([])

    assert args.project_dir == "."
    assert args.package_name is None
    assert args.dry_run is False
    assert args.force is False


def test_main_dry_run_reports_the_plan(old_stack_git, capsys):
    result = migrate_layout.main(
        ["--project-dir", str(old_stack_git), "--dry-run"]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "would, for package 'acme_widgets'" in out
    assert "(dry run: nothing written)" in out


def test_main_migrates_a_clean_checkout(old_stack_git, capsys):
    result = migrate_layout.main(["--project-dir", str(old_stack_git)])

    assert result == 0
    assert "Next:" in capsys.readouterr().out
    assert (
        old_stack_git / "src/acme_widgets/src/acme_widgets/tools.py"
    ).is_file()


def test_main_honours_package_name_override(old_stack_git):
    # A second candidate would make inference ambiguous; the override settles
    # it. Committed, so the tree stays clean.
    (old_stack_git / "src/other").mkdir()
    (old_stack_git / "src/other/tools.py").write_text("")
    (old_stack_git / "pyproject.toml").write_text(
        _OLD_PYPROJECT.replace('package_name = "acme_widgets"\n', "")
    )
    subprocess.run(["git", "add", "-A"], cwd=old_stack_git, check=True)
    subprocess.run(
        [*_GIT, "commit", "-q", "-m", "second candidate"],
        cwd=old_stack_git,
        check=True,
    )

    result = migrate_layout.main(
        ["--project-dir", str(old_stack_git), "--package-name", "acme_widgets"]
    )

    assert result == 0
    assert (old_stack_git / "src/acme_widgets/pyproject.toml").is_file()


def test_main_refuses_an_already_migrated_stack(old_stack_git):
    migrate_layout.main(["--project-dir", str(old_stack_git)])

    with pytest.raises(migrate_layout.AlreadyMigrated, match="nothing to do"):
        migrate_layout.main(["--project-dir", str(old_stack_git)])


def test_main_refuses_a_stack_without_the_old_layout(old_stack_git):
    (old_stack_git / "src/acme_widgets/tools.py").unlink()

    with pytest.raises(migrate_layout.NotOldLayout, match="tools.py"):
        migrate_layout.main(["--project-dir", str(old_stack_git)])


def test_main_refuses_a_dirty_tree(old_stack_git):
    (old_stack_git / "pyproject.toml").write_text(
        _OLD_PYPROJECT + "# edited\n"
    )

    with pytest.raises(migrate_layout.DirtyTree, match="uncommitted"):
        migrate_layout.main(["--project-dir", str(old_stack_git)])


def test_main_refuses_a_non_git_stack(old_stack):
    with pytest.raises(migrate_layout.NotAGitRepo, match="not a git checkout"):
        migrate_layout.main(["--project-dir", str(old_stack)])


def test_main_force_migrates_a_non_git_stack(old_stack):
    result = migrate_layout.main(["--project-dir", str(old_stack), "--force"])

    assert result == 0
    assert (old_stack / "src/acme_widgets/src/acme_widgets/tools.py").is_file()
