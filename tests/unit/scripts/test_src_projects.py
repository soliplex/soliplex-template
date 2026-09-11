"""Unit tests for the bundled ``skill/scripts/src_projects.py`` CLI.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``
(mirroring ``test_add_room.py``).

Hermetic: stacks are synthesized under ``tmp_path``; ``git`` runs only against
throwaway checkouts, and the docker probe is stubbed -- no daemon, no network.
AAA layout, single act per test.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "src_projects.py"
)
_spec = importlib.util.spec_from_file_location("src_projects", _MODULE_PATH)
src_projects = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(src_projects)


_COMPOSE = """\
services:
  backend:
    environment:
      OLLAMA_BASE_URL: http://h:11434
      # Append ':/app/src/<other>/src' for each further project directory.
      PYTHONPATH: /app/src/acme_widgets/src
"""

_MANIFEST = """\
[project]
name = "acme-widgets"

[tool.soliplex-template.params]
package_name = "acme_widgets"
"""


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def stack(tmp_path):
    """A stack in the src/ project layout, with its own project present."""
    _write(tmp_path / "docker-compose.yml", _COMPOSE)
    _write(tmp_path / "backend/environment/installation.yaml", "id: x\n")
    _write(tmp_path / "pyproject.toml", _MANIFEST)
    _write(
        tmp_path / "src/acme_widgets/src/acme_widgets/tools.py",
        "def greeting(n):\n    return n\n",
    )
    return tmp_path


@pytest.fixture
def checkout(stack):
    """A src-layout checkout sitting beside the stack's own project."""
    _write(
        stack / "src/widgetlib/pyproject.toml",
        '[project]\nname = "widgetlib"\ndependencies = ["httpx>=0.27"]\n',
    )
    (stack / "src/widgetlib/src/widgetlib").mkdir(parents=True)
    return stack


@pytest.fixture
def no_docker(monkeypatch):
    """Make the backend probe report 'cannot check'."""
    monkeypatch.setattr(src_projects.shutil, "which", lambda _name: None)


def _stub_dists(monkeypatch, names):
    """Stub the one-off backend container to report ``names`` installed."""
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/bin/docker")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(names) + "\n", stderr=""
        )

    monkeypatch.setattr(src_projects.subprocess, "run", fake_run)


# --------------------------------------------------------------------------
# own_package / project_dirs / container_path
# --------------------------------------------------------------------------
def test_own_package_from_manifest(stack):
    result = src_projects.own_package(stack)

    assert result == "acme_widgets"


def test_own_package_without_a_pyproject(tmp_path):
    result = src_projects.own_package(tmp_path)

    assert result is None


def test_own_package_with_unparseable_toml(tmp_path):
    _write(tmp_path / "pyproject.toml", "not = = toml\n")

    result = src_projects.own_package(tmp_path)

    assert result is None


def test_own_package_without_the_manifest_table(tmp_path):
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')

    result = src_projects.own_package(tmp_path)

    assert result is None


def test_project_dirs_lists_them_sorted(checkout):
    result = src_projects.project_dirs(checkout)

    assert result == ["acme_widgets", "widgetlib"]


def test_project_dirs_without_a_src_tree(tmp_path):
    result = src_projects.project_dirs(tmp_path)

    assert result == []


def test_container_path_for_a_src_layout_checkout(checkout):
    result = src_projects.container_path(checkout, "widgetlib")

    assert result == "/app/src/widgetlib/src"


def test_container_path_for_a_flat_layout_checkout(stack):
    (stack / "src/flatrepo/flatrepo").mkdir(parents=True)

    result = src_projects.container_path(stack, "flatrepo")

    assert result == "/app/src/flatrepo"


# --------------------------------------------------------------------------
# Dependency inspection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "requirement,expected",
    [
        ("httpx", "httpx"),
        ("httpx[http2]>=0.27", "httpx"),
        ("pkg ; python_version < '3.13'", "pkg"),
        ("pkg @ https://example.com/pkg.whl", "pkg"),
    ],
)
def test_declared_dependencies_parses_requirement_forms(
    stack, requirement, expected
):
    _write(
        stack / "src/dep/pyproject.toml",
        f'[project]\nname = "dep"\ndependencies = ["{requirement}"]\n',
    )

    result = src_projects.declared_dependencies(stack, "dep")

    assert result == [expected]


def test_declared_dependencies_without_a_pyproject(stack):
    (stack / "src/bare").mkdir()

    result = src_projects.declared_dependencies(stack, "bare")

    assert result == []


def test_declared_dependencies_with_unparseable_toml(stack):
    _write(stack / "src/bad/pyproject.toml", "not = = toml\n")

    result = src_projects.declared_dependencies(stack, "bad")

    assert result == []


def test_declared_dependencies_skips_non_string_entries(stack):
    _write(
        stack / "src/odd/pyproject.toml",
        '[project]\nname = "odd"\ndependencies = ["httpx", 3]\n',
    )

    result = src_projects.declared_dependencies(stack, "odd")

    assert result == ["httpx"]


def test_backend_distributions_without_docker(stack, no_docker):
    result = src_projects.backend_distributions(stack)

    assert result is None


def test_backend_distributions_normalizes_names(stack, monkeypatch):
    _stub_dists(monkeypatch, ["Foo_Bar", "httpx"])

    result = src_projects.backend_distributions(stack)

    assert result == {"foo-bar", "httpx"}


def test_backend_distributions_when_compose_fails(stack, monkeypatch):
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/d")
    monkeypatch.setattr(
        src_projects.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", "boom"),
    )

    result = src_projects.backend_distributions(stack)

    assert result is None


def test_backend_distributions_with_no_output(stack, monkeypatch):
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/d")
    monkeypatch.setattr(
        src_projects.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, "\n", ""),
    )

    result = src_projects.backend_distributions(stack)

    assert result is None


def test_backend_distributions_with_unparseable_output(stack, monkeypatch):
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/d")
    monkeypatch.setattr(
        src_projects.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, "not json\n", ""),
    )

    result = src_projects.backend_distributions(stack)

    assert result is None


def test_unsatisfied_dependencies_none_declared(stack):
    (stack / "src/bare").mkdir()

    result = src_projects.unsatisfied_dependencies(stack, "bare")

    assert result == ([], True)


def test_unsatisfied_dependencies_all_present(checkout, monkeypatch):
    _stub_dists(monkeypatch, ["httpx"])

    result = src_projects.unsatisfied_dependencies(checkout, "widgetlib")

    assert result == ([], True)


def test_unsatisfied_dependencies_reports_the_gap(checkout, monkeypatch):
    _stub_dists(monkeypatch, ["soliplex"])

    result = src_projects.unsatisfied_dependencies(checkout, "widgetlib")

    assert result == (["httpx>=0.27".split(">")[0]], True)


def test_unsatisfied_dependencies_uncheckable(checkout, no_docker):
    result = src_projects.unsatisfied_dependencies(checkout, "widgetlib")

    assert result == (["httpx"], False)


# --------------------------------------------------------------------------
# The PYTHONPATH line
# --------------------------------------------------------------------------
def test_read_entries(tmp_path):
    result = src_projects.read_entries(_COMPOSE, tmp_path / "c.yml")

    assert result == ["/app/src/acme_widgets/src"]


def test_read_entries_requires_the_line(tmp_path):
    with pytest.raises(src_projects.PythonPathMissing, match="no 'PYTHONPATH"):
        src_projects.read_entries("services:\n", tmp_path / "c.yml")


def test_read_entries_refuses_more_than_one(tmp_path):
    text = _COMPOSE + "      PYTHONPATH: /app/src/other/src\n"

    with pytest.raises(src_projects.PythonPathAmbiguous, match="found 2"):
        src_projects.read_entries(text, tmp_path / "c.yml")


def test_write_entries_preserves_indent_and_comments():
    result = src_projects.write_entries(_COMPOSE, ["/a", "/b"])

    assert "      PYTHONPATH: /a:/b\n" in result
    assert "# Append ':/app/src/<other>/src'" in result


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
def test_list_reports_each_project(checkout, capsys):
    result = src_projects.main(["list", "--project-dir", str(checkout)])

    assert result == 0
    out = capsys.readouterr().out
    assert "*acme_widgets" in out
    assert "* this stack's own project" in out
    # The sibling exists but is not yet on the path.
    assert "widgetlib" in out
    assert out.rstrip().endswith("own project")


def test_list_without_any_project_directories(stack, capsys):
    import shutil as _shutil

    _shutil.rmtree(stack / "src")

    result = src_projects.main(["list", "--project-dir", str(stack)])

    assert result == 0
    assert "no project directories under src/" in capsys.readouterr().out


def test_list_omits_the_legend_without_an_own_project(checkout, capsys):
    # A hand-built stack with no generation manifest has no "own" project to
    # mark, so the legend would be noise.
    (checkout / "pyproject.toml").write_text('[project]\nname = "x"\n')

    result = src_projects.main(["list", "--project-dir", str(checkout)])

    assert result == 0
    out = capsys.readouterr().out
    assert "widgetlib" in out
    assert "this stack's own project" not in out


def test_list_flags_a_path_entry_with_no_directory(stack, capsys):
    (stack / "docker-compose.yml").write_text(
        _COMPOSE.replace(
            "PYTHONPATH: /app/src/acme_widgets/src",
            "PYTHONPATH: /app/src/acme_widgets/src:/app/src/ghost/src",
        )
    )

    result = src_projects.main(["list", "--project-dir", str(stack)])

    assert result == 0
    assert "(no such directory)" in capsys.readouterr().out


# --------------------------------------------------------------------------
# add
# --------------------------------------------------------------------------
def test_add_puts_the_checkout_on_the_path(checkout, capsys):
    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    assert result == 0
    compose = (checkout / "docker-compose.yml").read_text()
    assert (
        "PYTHONPATH: /app/src/acme_widgets/src:/app/src/widgetlib/src"
        in compose
    )
    assert "added: /app/src/widgetlib/src" in capsys.readouterr().out


def test_add_is_idempotent(checkout, capsys):
    src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    assert result == 0
    assert "unchanged" in capsys.readouterr().out


def test_add_dry_run_writes_nothing(checkout, capsys):
    before = (checkout / "docker-compose.yml").read_text()

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--dry-run"]
    )

    assert result == 0
    assert (checkout / "docker-compose.yml").read_text() == before
    assert "(dry run: nothing written)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:some-owner/some-repo",
        "https://github.com/some-owner/some-repo",
        "ssh://git@example.com/repo.git",
    ],
)
def test_add_redirects_a_clone_url_to_clone(stack, url):
    # soliplex-template#177 spells the request "add <url> to the backend", so
    # 'add' has to say where a URL actually goes rather than reporting a
    # nonsense path under src/.
    with pytest.raises(src_projects.UrlPassedToAdd, match="use:"):
        src_projects.main(
            ["add", url, "--project-dir", str(stack), "--no-dep-check"]
        )


def test_add_requires_the_directory(stack):
    with pytest.raises(src_projects.ProjectMissing, match="does not exist"):
        src_projects.main(
            ["add", "absent", "--project-dir", str(stack), "--no-dep-check"]
        )


def test_add_reports_missing_dependencies_and_a_rebuild(
    checkout, monkeypatch, capsys
):
    _stub_dists(monkeypatch, ["soliplex"])

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "- httpx" in out
    assert "docker compose build backend" in out


def test_add_reports_satisfied_dependencies(checkout, monkeypatch, capsys):
    _stub_dists(monkeypatch, ["httpx"])

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "already" in out
    assert "docker compose build backend" not in out


def test_add_reports_an_unverifiable_check(checkout, no_docker, capsys):
    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "could not check" in out
    assert "- httpx" in out


# --------------------------------------------------------------------------
# clone
# --------------------------------------------------------------------------
@pytest.fixture
def origin(tmp_path_factory):
    """A tiny local repo, so cloning needs no network."""
    path = tmp_path_factory.mktemp("origin")
    (path / "src" / "flatpkg").mkdir(parents=True)
    # git tracks no empty directories, so the package needs a real file or
    # the clone would arrive looking flat-layout.
    (path / "src" / "flatpkg" / "__init__.py").write_text("")
    (path / "pyproject.toml").write_text(
        '[project]\nname = "flatpkg"\ndependencies = []\n'
    )
    subprocess.run(["git", "init", "-q", "."], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=a",
            # Throwaway repo: never invoke the host's signing key, which
            # would block on a pinentry prompt.
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
    )
    return path


def test_clone_fetches_and_wires_it_up(stack, origin, capsys):
    result = src_projects.main(
        [
            "clone",
            str(origin),
            "--name",
            "flatpkg",
            "--project-dir",
            str(stack),
            "--no-dep-check",
        ]
    )

    assert result == 0
    assert (stack / "src/flatpkg/pyproject.toml").is_file()
    assert "/app/src/flatpkg/src" in (stack / "docker-compose.yml").read_text()
    assert "cloned" in capsys.readouterr().out


def test_clone_defaults_the_name_to_the_repo(stack, origin, capsys):
    result = src_projects.main(
        ["clone", str(origin), "--project-dir", str(stack), "--no-dep-check"]
    )

    assert result == 0
    assert (stack / "src" / origin.name).is_dir()
    assert origin.name in capsys.readouterr().out


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/soliplex/soliplex", "soliplex"),
        ("https://github.com/soliplex/soliplex.git", "soliplex"),
        ("git@github.com:soliplex/soliplex.git", "soliplex"),
        ("/local/path/to/repo/", "repo"),
    ],
)
def test_derive_name(url, expected):
    result = src_projects.derive_name(url)

    assert result == expected


def test_clone_can_skip_the_path_edit(stack, origin, capsys):
    before = (stack / "docker-compose.yml").read_text()

    result = src_projects.main(
        [
            "clone",
            str(origin),
            "--name",
            "flatpkg",
            "--no-path",
            "--project-dir",
            str(stack),
        ]
    )

    assert result == 0
    assert (stack / "docker-compose.yml").read_text() == before
    assert "left off the backend's PYTHONPATH" in capsys.readouterr().out


def test_clone_dry_run_writes_nothing(stack, origin, capsys):
    result = src_projects.main(
        ["clone", str(origin), "--project-dir", str(stack), "--dry-run"]
    )

    assert result == 0
    assert not (stack / "src" / origin.name).exists()
    assert "(dry run: nothing written)" in capsys.readouterr().out


def test_clone_refuses_an_existing_directory(checkout, origin):
    with pytest.raises(src_projects.ProjectExists, match="already exists"):
        src_projects.main(
            [
                "clone",
                str(origin),
                "--name",
                "widgetlib",
                "--project-dir",
                str(checkout),
            ]
        )


def test_clone_surfaces_a_git_failure(stack, tmp_path):
    with pytest.raises(src_projects.CloneFailed, match="git clone"):
        src_projects.main(
            [
                "clone",
                str(tmp_path / "nope"),
                "--name",
                "x",
                "--project-dir",
                str(stack),
            ]
        )


# --------------------------------------------------------------------------
# remove
# --------------------------------------------------------------------------
def test_remove_takes_it_off_the_path(checkout, capsys):
    src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    result = src_projects.main(
        ["remove", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    compose = (checkout / "docker-compose.yml").read_text()
    assert "PYTHONPATH: /app/src/acme_widgets/src\n" in compose
    assert "untouched" in capsys.readouterr().out


def test_remove_dry_run_writes_nothing(checkout, capsys):
    src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )
    before = (checkout / "docker-compose.yml").read_text()

    result = src_projects.main(
        ["remove", "widgetlib", "--project-dir", str(checkout), "--dry-run"]
    )

    assert result == 0
    assert (checkout / "docker-compose.yml").read_text() == before
    assert "(dry run: nothing written)" in capsys.readouterr().out


def test_remove_of_an_absent_entry_is_a_no_op(checkout, capsys):
    result = src_projects.main(
        ["remove", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    assert "unchanged" in capsys.readouterr().out


def test_remove_refuses_the_stacks_own_project(stack):
    with pytest.raises(src_projects.RefusingOwnProject, match="own project"):
        src_projects.main(
            ["remove", "acme_widgets", "--project-dir", str(stack)]
        )


def test_remove_forced_drops_the_stacks_own_project(stack, capsys):
    result = src_projects.main(
        ["remove", "acme_widgets", "--project-dir", str(stack), "--force"]
    )

    assert result == 0
    assert "PYTHONPATH: \n" in (stack / "docker-compose.yml").read_text()
    assert "removed" in capsys.readouterr().out


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------
def test_parse_args_defaults_the_project_dir():
    args = src_projects.parse_args(["list"])

    assert args.project_dir == "."
    assert args.dry_run is False
