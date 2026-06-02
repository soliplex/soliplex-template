"""Unit tests for the bundled ``skill/scripts/rag_db.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``.
Tests are hermetic: the ``docker``/``shutil.which`` and ``subprocess.run``
seams are mocked and the filesystem is routed through ``tmp_path`` -- no real
Docker, no network.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skill"
    / "scripts"
    / "rag_db.py"
)
_spec = importlib.util.spec_from_file_location("rag_db", _MODULE_PATH)
rag_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag_db)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _make_project(tmp_path, *, compose=True, ragdb=True) -> pathlib.Path:
    """A stack directory with the bits ``resolve_project`` checks for."""
    project = tmp_path / "stack"
    project.mkdir(exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text("services: {}\n")
    if ragdb:
        (project / "rag" / "db").mkdir(parents=True, exist_ok=True)
    (project / "rag" / "docs").mkdir(parents=True, exist_ok=True)
    return project


def _make_db(project: pathlib.Path, db_name: str = "handbook") -> pathlib.Path:
    db = rag_db.db_lancedb_path(project, db_name)
    db.mkdir(parents=True, exist_ok=True)
    return db


def _expected(
    project: pathlib.Path,
    tail: list[str],
    *,
    mounts: tuple[str, ...] = (),
    db: str = "handbook",
    service: str = "haiku-ingester",
    config: str = "/app/haiku.rag.yaml",
) -> mock._Call:
    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(project.resolve()),
        "run",
        "--rm",
        "--no-TTY",
        *mounts,
        service,
        "haiku-rag",
        "--config",
        config,
        "--db",
        f"/data/{db}.lancedb",
        *tail,
    ]
    return mock.call(cmd, check=True)


def _expected_ps(project: pathlib.Path) -> mock._Call:
    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(project.resolve()),
        "ps",
        "-q",
        "haiku-ingester",
    ]
    return mock.call(cmd, capture_output=True, text=True, check=True)


def _ns(**overrides) -> argparse.Namespace:
    """A rebuild-modifier namespace (for ``rebuild_modifier``)."""
    base = dict(rechunk=False, embed_only=False, title_only=False)
    base.update(overrides)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def which(monkeypatch):
    w = mock.Mock(return_value="/usr/bin/docker")
    monkeypatch.setattr(rag_db.shutil, "which", w)
    return w


@pytest.fixture
def run(monkeypatch):
    r = mock.Mock()
    monkeypatch.setattr(rag_db.subprocess, "run", r)
    return r


# --------------------------------------------------------------------------
# validate_db_name
# --------------------------------------------------------------------------
def test_validate_db_name_ok():
    result = rag_db.validate_db_name("handbook.v2_final-1")

    assert result is None


@pytest.mark.parametrize(
    "name", ["", "bad/name", "../escape", ".hidden", "has space", "-lead"]
)
def test_validate_db_name_bad(name):
    with pytest.raises(rag_db.RagDbError, match="must match"):
        rag_db.validate_db_name(name)


def test_validate_db_name_allows_ingester_stem():
    # The reserved-stem check moved to guard_reserved_stem (it needs to know
    # whether the ingester is running); the pure name check accepts it.
    result = rag_db.validate_db_name(rag_db.INGESTER_STEM)

    assert result is None


# --------------------------------------------------------------------------
# resolve_project
# --------------------------------------------------------------------------
def test_resolve_project_ok(tmp_path):
    project = _make_project(tmp_path)

    resolved = rag_db.resolve_project(str(project))

    assert resolved == project.resolve()


def test_resolve_project_no_compose(tmp_path):
    project = _make_project(tmp_path, compose=False)

    with pytest.raises(rag_db.RagDbError, match="no docker-compose.yml"):
        rag_db.resolve_project(str(project))


def test_resolve_project_no_ragdb(tmp_path):
    project = _make_project(tmp_path, ragdb=False)

    with pytest.raises(rag_db.RagDbError, match="RAG db directory"):
        rag_db.resolve_project(str(project))


# --------------------------------------------------------------------------
# resolve_source
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source",
    ["https://example.com/a.html", "s3://bucket/key.pdf", "http://x/y"],
)
def test_resolve_source_remote(tmp_path, source):
    project = _make_project(tmp_path)

    mounts, container = rag_db.resolve_source(project, source)

    assert (mounts, container) == ([], source)


def test_resolve_source_under_docs(tmp_path):
    project = _make_project(tmp_path)
    src = project / "rag" / "docs" / "sub" / "file.md"
    src.parent.mkdir(parents=True)
    src.write_text("x")

    mounts, container = rag_db.resolve_source(project, str(src))

    assert (mounts, container) == ([], "/docs/sub/file.md")


def test_resolve_source_docs_root(tmp_path):
    project = _make_project(tmp_path)
    docs_root = project / "rag" / "docs"

    mounts, container = rag_db.resolve_source(project, str(docs_root))

    assert (mounts, container) == ([], "/docs")


def test_resolve_source_automount_dir(tmp_path):
    project = _make_project(tmp_path)
    external = tmp_path / "external"
    external.mkdir()

    mounts, container = rag_db.resolve_source(project, str(external))

    assert (mounts, container) == (
        ["-v", f"{external.resolve()}:/src:ro"],
        "/src",
    )


def test_resolve_source_automount_file(tmp_path):
    project = _make_project(tmp_path)
    external = tmp_path / "report.pdf"
    external.write_text("x")

    mounts, container = rag_db.resolve_source(project, str(external))

    assert (mounts, container) == (
        ["-v", f"{external.resolve()}:/src/report.pdf:ro"],
        "/src/report.pdf",
    )


def test_resolve_source_missing(tmp_path):
    project = _make_project(tmp_path)

    with pytest.raises(rag_db.RagDbError, match="does not exist"):
        rag_db.resolve_source(project, str(tmp_path / "nope"))


# --------------------------------------------------------------------------
# rebuild_modifier
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"rechunk": True}, "--rechunk"),
        ({"embed_only": True}, "--embed-only"),
        ({"title_only": True}, "--title-only"),
        ({}, None),
    ],
)
def test_rebuild_modifier(overrides, expected):
    result = rag_db.rebuild_modifier(_ns(**overrides))

    assert result == expected


# --------------------------------------------------------------------------
# compose_run
# --------------------------------------------------------------------------
def test_compose_run_builds_argv(tmp_path, run):
    project = _make_project(tmp_path)

    rag_db.compose_run(
        project, "svc", "/cfg.yaml", "handbook", ["-v", "a:b"], ["init"]
    )

    assert run.call_args_list == [
        _expected(
            project,
            ["init"],
            mounts=("-v", "a:b"),
            service="svc",
            config="/cfg.yaml",
        )
    ]


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------
def test_create_happy(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    src = project / "rag" / "docs" / "handbook"
    src.mkdir()

    rc = rag_db.main(
        [
            "create",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--source",
            str(src),
        ]
    )

    assert rc == 0
    assert run.call_args_list == [
        _expected(project, ["init"]),
        _expected(project, ["add-src", "/docs/handbook"]),
    ]
    assert "rag_lancedb_stem" in capsys.readouterr().out


def test_create_docker_missing(tmp_path, which, run):
    which.return_value = None
    project = _make_project(tmp_path)

    with pytest.raises(rag_db.RagDbError, match="docker not found"):
        rag_db.main(
            [
                "create",
                "--db-name",
                "handbook",
                "--project-dir",
                str(project),
                "--source",
                "https://x/y",
            ]
        )

    assert run.call_args_list == []


def test_create_db_exists_no_force(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    with pytest.raises(rag_db.RagDbError, match="already exists"):
        rag_db.main(
            [
                "create",
                "--db-name",
                "handbook",
                "--project-dir",
                str(project),
                "--source",
                "https://x/y",
            ]
        )

    assert run.call_args_list == []


def test_create_db_exists_force(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    rc = rag_db.main(
        [
            "create",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--source",
            "https://x/y",
            "--force",
        ]
    )

    assert rc == 0
    assert run.call_args_list == [
        _expected(project, ["init"]),
        _expected(project, ["add-src", "https://x/y"]),
    ]


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------
def test_update_docker_missing(tmp_path, which, run):
    which.return_value = None
    project = _make_project(tmp_path)

    with pytest.raises(rag_db.RagDbError, match="docker not found"):
        rag_db.main(
            ["update", "--db-name", "handbook", "--project-dir", str(project)]
        )

    assert run.call_args_list == []


def test_update_modifier_without_rebuild(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    with pytest.raises(rag_db.RagDbError, match="only valid with --rebuild"):
        rag_db.main(
            [
                "update",
                "--db-name",
                "handbook",
                "--project-dir",
                str(project),
                "--rechunk",
            ]
        )

    assert run.call_args_list == []


def test_update_no_op(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    with pytest.raises(rag_db.RagDbError, match="at least one of"):
        rag_db.main(
            ["update", "--db-name", "handbook", "--project-dir", str(project)]
        )

    assert run.call_args_list == []


def test_update_db_missing(tmp_path, which, run):
    project = _make_project(tmp_path)

    with pytest.raises(rag_db.RagDbError, match="does not exist"):
        rag_db.main(
            [
                "update",
                "--db-name",
                "handbook",
                "--project-dir",
                str(project),
                "--rebuild",
            ]
        )

    assert run.call_args_list == []


def test_update_add_src(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    _make_db(project)
    src = project / "rag" / "docs" / "q2"
    src.mkdir()

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--source",
            str(src),
        ]
    )

    assert rc == 0
    assert run.call_args_list == [_expected(project, ["add-src", "/docs/q2"])]
    assert "added" in capsys.readouterr().out


def test_update_rebuild_plain(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--rebuild",
        ]
    )

    assert rc == 0
    assert run.call_args_list == [_expected(project, ["rebuild"])]


@pytest.mark.parametrize(
    "flag, modifier",
    [
        ("--rechunk", "--rechunk"),
        ("--embed-only", "--embed-only"),
        ("--title-only", "--title-only"),
    ],
)
def test_update_rebuild_with_modifier(tmp_path, which, run, flag, modifier):
    project = _make_project(tmp_path)
    _make_db(project)

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--rebuild",
            flag,
        ]
    )

    assert rc == 0
    assert run.call_args_list == [_expected(project, ["rebuild", modifier])]


def test_update_delete(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_db(project)

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--delete",
            "id-1",
            "--delete",
            "id-2",
        ]
    )

    assert rc == 0
    assert run.call_args_list == [
        _expected(project, ["delete", "id-1"]),
        _expected(project, ["delete", "id-2"]),
    ]


def test_update_all_ops_in_order(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    _make_db(project)
    src = project / "rag" / "docs" / "more"
    src.mkdir()

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            "handbook",
            "--project-dir",
            str(project),
            "--delete",
            "old-id",
            "--source",
            str(src),
            "--rebuild",
        ]
    )

    assert rc == 0
    assert run.call_args_list == [
        _expected(project, ["delete", "old-id"]),
        _expected(project, ["add-src", "/docs/more"]),
        _expected(project, ["rebuild"]),
    ]
    out = capsys.readouterr().out
    assert "deleted 1 document(s)" in out
    assert "rebuilt index" in out


def test_update_ingester_stem_refused_when_running(tmp_path, which, run):
    project = _make_project(tmp_path)
    run.return_value.stdout = "container-id\n"  # ingester is up

    with pytest.raises(rag_db.RagDbError, match="ingester's database"):
        rag_db.main(
            [
                "update",
                "--db-name",
                rag_db.INGESTER_STEM,
                "--project-dir",
                str(project),
                "--rebuild",
            ]
        )

    assert run.call_args_list == [_expected_ps(project)]


def test_update_ingester_stem_allowed_when_stopped(
    tmp_path, which, run, capsys
):
    project = _make_project(tmp_path)
    _make_db(project, rag_db.INGESTER_STEM)
    run.return_value.stdout = ""  # ingester is not running

    rc = rag_db.main(
        [
            "update",
            "--db-name",
            rag_db.INGESTER_STEM,
            "--project-dir",
            str(project),
            "--rebuild",
        ]
    )

    assert rc == 0
    assert run.call_args_list == [
        _expected_ps(project),
        _expected(project, ["rebuild"], db=rag_db.INGESTER_STEM),
    ]
    assert "not running" in capsys.readouterr().out


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------
def test_no_command_errors():
    with pytest.raises(SystemExit):
        rag_db.parse_args([])


def test_mutually_exclusive_modifiers():
    with pytest.raises(SystemExit):
        rag_db.parse_args(
            ["update", "--db-name", "x", "--rechunk", "--embed-only"]
        )
