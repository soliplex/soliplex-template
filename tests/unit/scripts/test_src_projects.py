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
import sys

import pytest
import yaml

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


_FOLDED_SERVE = """\
    command: >-
      /app/.venv/bin/soliplex-cli serve
      --no-auth-mode
      --reload=config
      --reload-dirs=/app/src/acme_widgets/src
      --host=0.0.0.0
      /environment
"""

# A generated stack: the serve command folded one flag per line, watching
# the stack's own package.
_COMPOSE = f"""\
services:
  backend:
    environment: &backend_environment
      OLLAMA_BASE_URL: http://h:11434
      # Append ':/app/src/<other>/src' for each further project directory.
      PYTHONPATH: /app/src/acme_widgets/src
{_FOLDED_SERVE}
  soliplex-dev:
    profiles: ["devmode"]
    environment: *backend_environment
"""

# A stack generated before the fold keeps the serve command on one quoted
# line, with no '--reload-dirs'; build it here so the fixture stays faithful
# without a 100-column source line.
_ONE_LINE_SERVE = (
    "/app/.venv/bin/soliplex-cli serve --no-auth-mode --reload=config"
    " --host=0.0.0.0 /environment"
)
_COMPOSE_ONE_LINE = _COMPOSE.replace(
    _FOLDED_SERVE, f'    command: "{_ONE_LINE_SERVE}"\n'
)

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
def one_line_checkout(checkout):
    """The checkout beside a stack whose serve command is on one line."""
    (checkout / "docker-compose.yml").write_text(_COMPOSE_ONE_LINE)
    return checkout


def _serve(stack: pathlib.Path) -> str:
    """The backend's serve command, as compose reads it."""
    compose = yaml.safe_load((stack / "docker-compose.yml").read_text())
    return compose["services"]["backend"]["command"]


def _set_serve(stack: pathlib.Path, old: str, new: str) -> None:
    compose = stack / "docker-compose.yml"
    compose.write_text(compose.read_text().replace(old, new))


@pytest.fixture
def no_docker(monkeypatch):
    """Make the backend probe report 'cannot check'."""
    monkeypatch.setattr(src_projects.shutil, "which", lambda _name: None)


def _stub_check(monkeypatch, records):
    """Stub the one-off backend container to print ``records``."""
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/bin/docker")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(records) + "\n", stderr=""
        )

    monkeypatch.setattr(src_projects.subprocess, "run", fake_run)


def _stub_compose(monkeypatch, returncode, stdout):
    monkeypatch.setattr(src_projects.shutil, "which", lambda _n: "/d")
    monkeypatch.setattr(
        src_projects.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], returncode, stdout, ""
        ),
    )


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
# Dependency inspection: soliplex-template#210
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "requirement",
    [
        "httpx",
        "httpx[http2]>=0.27",
        "pkg ; python_version < '3.13'",
        "pkg @ https://example.com/pkg.whl",
    ],
)
def test_declared_dependencies_keeps_whole_requirements(stack, requirement):
    _write(
        stack / "src/dep/pyproject.toml",
        f'[project]\nname = "dep"\ndependencies = ["  {requirement} "]\n',
    )

    result = src_projects.declared_dependencies(stack, "dep")

    assert result == [requirement]


def test_declared_dependencies_without_a_pyproject(stack):
    (stack / "src/bare").mkdir()

    result = src_projects.declared_dependencies(stack, "bare")

    assert result == []


def test_declared_dependencies_with_unparseable_toml(stack):
    _write(stack / "src/bad/pyproject.toml", "not = = toml\n")

    result = src_projects.declared_dependencies(stack, "bad")

    assert result == []


def test_declared_dependencies_skips_non_string_and_blank_entries(stack):
    _write(
        stack / "src/odd/pyproject.toml",
        '[project]\nname = "odd"\ndependencies = ["httpx", 3, "  "]\n',
    )

    result = src_projects.declared_dependencies(stack, "odd")

    assert result == ["httpx"]


def test_dependency_status_values_are_the_wire_format():
    result = [str(status) for status in src_projects.DependencyStatus]

    assert result == ["satisfied", "missing", "conflict"]


# The container-side script, run here with this environment's own Python
# (which has 'packaging', and 'pytest' installed): the real classification
# logic, not a stub of it.
@pytest.mark.parametrize(
    "requirement, expected",
    [
        ("pytest", "satisfied"),
        ("PyTest>=1", "satisfied"),
        ("pytest<1", "conflict"),
        ("no-such-dist-for-soliplex-template-tests", "missing"),
        # Left out: a marker excluding this environment, an extra-only
        # requirement, and one that does not parse.
        ("pytest ; python_version < '3'", None),
        ("pytest ; extra == 'dev'", None),
        ("not a requirement!!", None),
    ],
)
def test_check_script_classifies_each_requirement(requirement, expected):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            src_projects._CHECK_DEPS,
            json.dumps([requirement]),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    records = json.loads(result.stdout)
    assert [status for _req, status, _installed in records] == (
        [expected] if expected else []
    )


def test_backend_check_without_docker(stack, no_docker):
    result = src_projects.backend_check(stack, ["httpx"])

    assert result is None


@pytest.mark.parametrize(
    "returncode, stdout",
    [(1, ""), (0, "\n"), (0, "not json\n")],
    ids=["compose-fails", "no-output", "unparseable"],
)
def test_backend_check_unknowable(stack, monkeypatch, returncode, stdout):
    _stub_compose(monkeypatch, returncode, stdout)

    result = src_projects.backend_check(stack, ["httpx"])

    assert result is None


def test_backend_check_reads_the_records(stack, monkeypatch):
    _stub_check(
        monkeypatch,
        [
            ["httpx>=0.27", "satisfied", "0.28.1"],
            ["pydantic<2", "conflict", "2.13.4"],
        ],
    )

    result = src_projects.backend_check(stack, ["httpx>=0.27", "pydantic<2"])

    assert result == [
        src_projects.DependencyCheck(
            "httpx>=0.27", src_projects.DependencyStatus.SATISFIED, "0.28.1"
        ),
        src_projects.DependencyCheck(
            "pydantic<2", src_projects.DependencyStatus.CONFLICT, "2.13.4"
        ),
    ]


def test_check_dependencies_none_declared(stack):
    (stack / "src/bare").mkdir()

    result = src_projects.check_dependencies(stack, "bare")

    assert result == ([], [])


def test_check_dependencies_uncheckable(checkout, no_docker):
    result = src_projects.check_dependencies(checkout, "widgetlib")

    assert result == (["httpx>=0.27"], None)


def test_check_dependencies_checked(checkout, monkeypatch):
    _stub_check(monkeypatch, [["httpx>=0.27", "missing", None]])

    result = src_projects.check_dependencies(checkout, "widgetlib")

    assert result == (
        ["httpx>=0.27"],
        [
            src_projects.DependencyCheck(
                "httpx>=0.27", src_projects.DependencyStatus.MISSING, None
            )
        ],
    )


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
    rows = {
        line.split()[0]: line.split()[2:]
        for line in capsys.readouterr().out.splitlines()
        if line.strip().lstrip("*").startswith(("acme_widgets", "widgetlib"))
    }
    # Own project: on the path and watched; the sibling: neither, yet.
    assert rows == {"*acme_widgets": ["yes", "yes"], "widgetlib": ["no", "no"]}


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


def test_list_ends_with_the_legend(checkout, capsys):
    result = src_projects.main(["list", "--project-dir", str(checkout)])

    assert result == 0
    assert capsys.readouterr().out.rstrip().endswith("own project")


def test_list_notes_that_nothing_is_watched_without_reload(stack, capsys):
    _set_serve(stack, "      --reload=config\n", "")

    result = src_projects.main(["list", "--project-dir", str(stack)])

    assert result == 0
    assert "nothing is watched" in capsys.readouterr().out


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
    out = capsys.readouterr().out
    assert "added: /app/src/widgetlib/src" in out
    assert "watching /app/src/widgetlib/src" in out


@pytest.mark.parametrize(
    "fixture, expected",
    [
        # Folded: the new flag follows the stack's own, line by line.
        (
            "checkout",
            "--reload=config --reload-dirs=/app/src/acme_widgets/src "
            "--reload-dirs=/app/src/widgetlib/src --host=0.0.0.0",
        ),
        # One line: it follows the '--reload' flag.
        (
            "one_line_checkout",
            "--reload=config --reload-dirs=/app/src/widgetlib/src "
            "--host=0.0.0.0",
        ),
    ],
)
def test_add_watches_the_checkout(request, fixture, expected):
    stack = request.getfixturevalue(fixture)

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(stack), "--no-dep-check"]
    )

    assert result == 0
    assert expected in _serve(stack)


def test_add_watches_an_entry_already_on_the_path(one_line_checkout, capsys):
    # How an older stack gets its own package watched: re-run 'add'.
    result = src_projects.main(
        [
            "add",
            "acme_widgets",
            "--project-dir",
            str(one_line_checkout),
            "--no-dep-check",
        ]
    )

    assert result == 0
    assert "--reload-dirs=/app/src/acme_widgets/src" in _serve(
        one_line_checkout
    )
    out = capsys.readouterr().out
    assert "unchanged" in out
    assert "watching /app/src/acme_widgets/src" in out
    assert "docker compose up -d backend" in out


def test_add_without_a_reload_flag_still_edits_the_path(checkout, capsys):
    _set_serve(checkout, "      --reload=config\n", "")

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    # No error, and no half-applied change: the path entry is written, and
    # no flag is added that nothing would read.
    assert result == 0
    compose = (checkout / "docker-compose.yml").read_text()
    assert "/app/src/acme_widgets/src:/app/src/widgetlib/src" in compose
    assert "--reload-dirs=/app/src/widgetlib/src" not in compose
    assert "serves without '--reload'" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mode, expected_mode, note",
    [
        ("both", "--reload=config", "'--reload=both' -> '--reload=config'"),
        ("python", "--reload=python", "keeping '--reload=python'"),
    ],
)
def test_add_settles_an_older_reload_mode(
    checkout, capsys, mode, expected_mode, note
):
    _set_serve(checkout, "--reload=config", f"--reload={mode}")

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    assert result == 0
    serve = _serve(checkout)
    assert expected_mode in serve
    assert "--reload-dirs=/app/src/widgetlib/src" in serve
    assert note in capsys.readouterr().out


def test_add_is_idempotent(checkout, capsys):
    src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )
    capsys.readouterr()  # only the second run's output is under test

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--no-dep-check"]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "unchanged" in out
    assert "watching" not in out
    assert "Next:" not in out


def test_add_dry_run_writes_nothing(checkout, capsys):
    before = (checkout / "docker-compose.yml").read_text()

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout), "--dry-run"]
    )

    assert result == 0
    assert (checkout / "docker-compose.yml").read_text() == before
    out = capsys.readouterr().out
    assert "watching /app/src/widgetlib/src" in out
    assert "(dry run: nothing written)" in out


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
    _stub_check(monkeypatch, [["httpx>=0.27", "missing", None]])

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "- httpx>=0.27" in out
    assert "docker compose build backend" in out


def test_add_reports_a_conflict_and_its_fixes(checkout, monkeypatch, capsys):
    _stub_check(monkeypatch, [["httpx>=0.27", "conflict", "0.20.0"]])

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "CONFLICT" in out
    assert "- httpx>=0.27 (the image has 0.20.0)" in out
    assert "cannot fix this" in out
    assert "1. adapt src/widgetlib" in out
    # Adding to requirements.txt and rebuilding is not the fix for this.
    assert "docker compose build backend" not in out


def test_add_reports_missing_and_conflicting_together(
    checkout, monkeypatch, capsys
):
    _stub_check(
        monkeypatch,
        [
            ["httpx>=0.27", "missing", None],
            ["pydantic<2", "conflict", "2.13.4"],
        ],
    )

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert out.index("MISSING") < out.index("CONFLICT")
    assert "docker compose build backend" in out


def test_add_reports_satisfied_dependencies(checkout, monkeypatch, capsys):
    _stub_check(monkeypatch, [["httpx>=0.27", "satisfied", "0.28.1"]])

    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "at a version it accepts" in out
    assert "docker compose build backend" not in out


def test_add_reports_an_unverifiable_check(checkout, no_docker, capsys):
    result = src_projects.main(
        ["add", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "could not check" in out
    assert "- httpx>=0.27" in out


# --------------------------------------------------------------------------
# The serve command's reload flags: soliplex-template#209
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        (_COMPOSE, "config"),
        (_COMPOSE_ONE_LINE, "config"),
        ("services:\n", None),
    ],
)
def test_read_reload(text, expected):
    result = src_projects.read_reload(text)

    assert result == expected


@pytest.mark.parametrize("text", [_COMPOSE, _COMPOSE_ONE_LINE])
def test_write_reload_keeps_the_flag_in_place(text):
    result = src_projects.write_reload(text, "both")

    assert result == text.replace("--reload=config", "--reload=both")


@pytest.mark.parametrize(
    "text, expected",
    [(_COMPOSE, ["/app/src/acme_widgets/src"]), (_COMPOSE_ONE_LINE, [])],
)
def test_read_reload_dirs(text, expected):
    result = src_projects.read_reload_dirs(text)

    assert result == expected


def test_add_reload_dir_takes_a_line_of_its_own_when_folded():
    result = src_projects.add_reload_dir(_COMPOSE, "/app/src/widgetlib/src")

    assert (
        "      --reload-dirs=/app/src/acme_widgets/src\n"
        "      --reload-dirs=/app/src/widgetlib/src\n"
        "      --host=0.0.0.0\n"
    ) in result


def test_add_reload_dir_follows_the_reload_flag_on_one_line():
    result = src_projects.add_reload_dir(
        _COMPOSE_ONE_LINE, "/app/src/widgetlib/src"
    )

    assert (
        "--reload=config --reload-dirs=/app/src/widgetlib/src --host" in result
    )


@pytest.mark.parametrize("text", [_COMPOSE, _COMPOSE_ONE_LINE])
def test_remove_reload_dir_undoes_add_reload_dir(text):
    added = src_projects.add_reload_dir(text, "/app/src/widgetlib/src")

    result = src_projects.remove_reload_dir(added, "/app/src/widgetlib/src")

    assert result == text


_LIB = "/app/src/widgetlib/src"


@pytest.mark.parametrize(
    "mode, watch, dirs_before, mode_after, dirs_after, notes",
    [
        ("config", True, [], "config", [_LIB], ["watching"]),
        ("config", True, [_LIB], "config", [_LIB], []),
        ("config", False, [_LIB], "config", [], ["no longer watching"]),
        ("config", False, [], "config", [], []),
        (
            "both",
            True,
            [],
            "config",
            [_LIB],
            ["-> '--reload=config'", "watching"],
        ),
        ("python", True, [], "python", [_LIB], ["keeping", "watching"]),
        # A mode this script does not know still turns reload on.
        ("explicit", True, [], "explicit", [_LIB], ["watching"]),
        (None, True, [], None, [], ["without '--reload'"]),
    ],
)
def test_sync_reload(mode, watch, dirs_before, mode_after, dirs_after, notes):
    flags = [f"--reload={mode}"] if mode else []
    flags += [f"--reload-dirs={d}" for d in dirs_before]
    text = "    command: serve " + " ".join([*flags, "/environment"]) + "\n"

    result, printed = src_projects.sync_reload(text, _LIB, watch=watch)

    assert src_projects.read_reload(result) == mode_after
    assert src_projects.read_reload_dirs(result) == dirs_after
    assert len(printed) == len(notes)
    for line, fragment in zip(printed, notes, strict=True):
        assert fragment in line


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
    assert "--reload-dirs=/app/src/flatpkg/src" in _serve(stack)
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
    assert compose == _COMPOSE
    out = capsys.readouterr().out
    assert "no longer watching /app/src/widgetlib/src" in out
    assert "untouched" in out


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


def test_remove_drops_a_stale_watch(checkout, capsys):
    # Watched, but not on PYTHONPATH (a hand edit): 'remove' still tidies it.
    _set_serve(
        checkout,
        "      --host=0.0.0.0\n",
        "      --reload-dirs=/app/src/widgetlib/src\n      --host=0.0.0.0\n",
    )

    result = src_projects.main(
        ["remove", "widgetlib", "--project-dir", str(checkout)]
    )

    assert result == 0
    assert (checkout / "docker-compose.yml").read_text() == _COMPOSE
    out = capsys.readouterr().out
    assert "was not on the backend's PYTHONPATH" in out
    assert "no longer watching" in out


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
    assert "--reload-dirs" not in _serve(stack)
    assert "removed" in capsys.readouterr().out


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------
def test_parse_args_defaults_the_project_dir():
    args = src_projects.parse_args(["list"])

    assert args.project_dir == "."
    assert args.dry_run is False
