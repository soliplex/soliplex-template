"""Unit tests for the ``soliplex_template.rag_migrations`` core.

It ships as an installed package, so it is imported directly. Tests are
hermetic: stacks are built under ``tmp_path`` and the ``shutil.which`` /
``subprocess.run`` seams are monkeypatched -- no Docker, no containers, no
LanceDB.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from soliplex_template import rag_migrations as rm

# What 'haiku-rag info' prints for a store three upgrades behind: the summary
# line, one '→' entry per pending upgrade (the last wrapped by Rich), then the
# rule that closes the block and the unrelated trailer after it.
_INFO_PENDING = (
    "haiku.rag database info\n"
    "  haiku.rag version (db): 0.56.0\n"
    "────────────────────────────────\n"
    "3 migration(s) pending. Run haiku-rag migrate to upgrade.\n"
    "  → 0.58.0: Move mutable document attributes into document_meta\n"
    "  → 0.64.0: Rename the document_meta identity column\n"
    "  → 0.75.0: Index documents.id, chunks.id, chunks.document_id and \n"
    "document_items.label\n"
    "────────────────────────────────\n"
    "Versions\n"
    "  haiku.rag: 0.82.1\n"
)
_INFO_CLEAN = (
    "haiku.rag database info\n"
    "────────────────────────────────\n"
    "Database is up to date.\n"
    "────────────────────────────────\n"
)


def _make_stack(tmp_path, *, compose=True, db_dir=True, stems=("haiku.rag",)):
    project = tmp_path / "stack"
    project.mkdir(parents=True, exist_ok=True)
    if compose:
        (project / rm.COMPOSE_FILE).write_text("services: {}\n")
    if db_dir:
        databases = project / rm.DB_DIR
        databases.mkdir(parents=True, exist_ok=True)
        for stem in stems:
            (databases / f"{stem}{rm.DB_SUFFIX}").mkdir()
    return project


@pytest.fixture
def docker(monkeypatch):
    """Pretend the Docker CLI is on PATH (as ``/usr/bin/docker``)."""
    monkeypatch.setattr(rm.shutil, "which", lambda _name: "/usr/bin/docker")
    return "/usr/bin/docker"


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --------------------------------------------------------------------------
# docker_cli
# --------------------------------------------------------------------------
def test_docker_cli_returns_path(docker):
    found = rm.docker_cli()

    assert found == docker


def test_docker_cli_raises_when_absent(monkeypatch):
    monkeypatch.setattr(rm.shutil, "which", lambda _name: None)

    with pytest.raises(rm.DockerMissing):
        rm.docker_cli()


# --------------------------------------------------------------------------
# resolve_project
# --------------------------------------------------------------------------
def test_resolve_project_returns_stack_root(tmp_path):
    project = _make_stack(tmp_path)

    resolved = rm.resolve_project(str(project))

    assert resolved == project.resolve()


def test_resolve_project_without_compose(tmp_path):
    project = _make_stack(tmp_path, compose=False)

    with pytest.raises(rm.ComposeNotFound) as exc_info:
        rm.resolve_project(project)

    assert exc_info.value.path == project.resolve() / rm.COMPOSE_FILE


def test_resolve_project_without_db_dir(tmp_path):
    project = _make_stack(tmp_path, db_dir=False)

    with pytest.raises(rm.DatabaseDirectoryMissing) as exc_info:
        rm.resolve_project(project)

    assert exc_info.value.path == project.resolve() / rm.DB_DIR


# --------------------------------------------------------------------------
# discover_databases / select_databases
# --------------------------------------------------------------------------
def test_discover_databases_sorted(tmp_path):
    project = _make_stack(tmp_path, stems=("handbook", "haiku.rag"))

    found = rm.discover_databases(project)

    assert [db.name for db in found] == [
        "haiku.rag.lancedb",
        "handbook.lancedb",
    ]


def test_discover_databases_ignores_other_entries(tmp_path):
    project = _make_stack(tmp_path)
    (project / rm.DB_DIR / "README.md").write_text("not a database\n")

    found = rm.discover_databases(project)

    assert [db.name for db in found] == ["haiku.rag.lancedb"]


@pytest.mark.parametrize("names", [None, []])
def test_select_databases_takes_all_when_unnamed(tmp_path, names):
    project = _make_stack(tmp_path, stems=("handbook", "haiku.rag"))

    selected = rm.select_databases(project, names)

    assert [db.name for db in selected] == [
        "haiku.rag.lancedb",
        "handbook.lancedb",
    ]


def test_select_databases_honors_names_and_their_order(tmp_path):
    project = _make_stack(tmp_path, stems=("handbook", "haiku.rag"))

    selected = rm.select_databases(project, ["handbook", "haiku.rag"])

    assert [db.name for db in selected] == [
        "handbook.lancedb",
        "haiku.rag.lancedb",
    ]


def test_select_databases_raises_when_none_exist(tmp_path):
    project = _make_stack(tmp_path, stems=())

    with pytest.raises(rm.NoDatabases) as exc_info:
        rm.select_databases(project, None)

    assert exc_info.value.path == project / rm.DB_DIR


def test_select_databases_raises_on_unknown_name(tmp_path):
    project = _make_stack(tmp_path)

    with pytest.raises(rm.UnknownDatabase) as exc_info:
        rm.select_databases(project, ["nope"])

    assert exc_info.value.available == ["haiku.rag"]
    assert "haiku.rag" in str(exc_info.value)


def test_unknown_database_message_without_candidates():
    error = rm.UnknownDatabase("nope", [])

    assert "(none found)" in str(error)


# --------------------------------------------------------------------------
# ingester_running
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stdout, expected", [("abc123\n", True), ("\n", False)]
)
def test_ingester_running(tmp_path, docker, monkeypatch, stdout, expected):
    project = _make_stack(tmp_path)
    run = mock.Mock(return_value=_completed(stdout=stdout))
    monkeypatch.setattr(rm.subprocess, "run", run)

    running = rm.ingester_running(project)

    assert running is expected
    assert run.call_args.args[0] == [
        docker,
        "compose",
        "--project-directory",
        str(project),
        "ps",
        "-q",
        rm.INGESTER_SERVICE,
    ]


# --------------------------------------------------------------------------
# haiku_rag_argv
# --------------------------------------------------------------------------
def test_haiku_rag_argv_runs_the_cli_service(tmp_path, docker):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"

    argv = rm.haiku_rag_argv(project, db, ["info"])

    assert argv == [
        docker,
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-TTY",
        rm.SERVICE,
        "info",
        "--db",
        "/data/haiku.rag.lancedb",
    ]


# --------------------------------------------------------------------------
# parse_pending
# --------------------------------------------------------------------------
def test_parse_pending_folds_wrapped_entries():
    entries = rm.parse_pending(_INFO_PENDING)

    assert entries == [
        "0.58.0: Move mutable document attributes into document_meta",
        "0.64.0: Rename the document_meta identity column",
        "0.75.0: Index documents.id, chunks.id, chunks.document_id and "
        "document_items.label",
    ]


def test_parse_pending_empty_when_nothing_pending():
    entries = rm.parse_pending(_INFO_CLEAN)

    assert entries == []


def test_parse_pending_stops_at_a_blank_line():
    text = (
        "1 migration(s) pending\n"
        "  → 0.58.0: first\n"
        "\n"
        "  → 0.99.0: after the block\n"
    )

    entries = rm.parse_pending(text)

    assert entries == ["0.58.0: first"]


def test_parse_pending_ignores_a_stray_line_before_the_first_entry():
    text = "1 migration(s) pending\nstray\n  → 0.58.0: first\n"

    entries = rm.parse_pending(text)

    assert entries == ["0.58.0: first"]


# --------------------------------------------------------------------------
# check_database
# --------------------------------------------------------------------------
def test_check_database_reports_pending(tmp_path, docker, monkeypatch):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout=_INFO_PENDING)),
    )

    pending, entries = rm.check_database(project, db)

    assert pending is True
    assert len(entries) == 3


def test_check_database_reports_clean(tmp_path, docker, monkeypatch):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout=_INFO_CLEAN)),
    )

    pending, entries = rm.check_database(project, db)

    assert pending is False
    assert entries == []


def test_check_database_raises_on_cli_failure(
    tmp_path, docker, monkeypatch, capsys
):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(
            return_value=_completed(
                returncode=2, stdout="partial\n", stderr="boom\n"
            )
        ),
    )

    with pytest.raises(rm.CommandFailed) as exc_info:
        rm.check_database(project, db)

    assert exc_info.value.returncode == 2
    assert exc_info.value.db_name == "haiku.rag.lancedb"
    assert "boom" in capsys.readouterr().out


# --------------------------------------------------------------------------
# migrate_database
# --------------------------------------------------------------------------
def test_migrate_database_runs_the_migrate_subcommand(
    tmp_path, docker, monkeypatch
):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(rm.subprocess, "run", run)

    rm.migrate_database(project, db)

    assert "migrate" in run.call_args.args[0]


def test_migrate_database_raises_on_cli_failure(tmp_path, docker, monkeypatch):
    project = _make_stack(tmp_path)
    db = project / rm.DB_DIR / "haiku.rag.lancedb"
    monkeypatch.setattr(
        rm.subprocess, "run", mock.Mock(return_value=_completed(returncode=1))
    )

    with pytest.raises(rm.CommandFailed):
        rm.migrate_database(project, db)


# --------------------------------------------------------------------------
# migrate_rag_dbs (--check)
# --------------------------------------------------------------------------
def test_migrate_rag_dbs_check_clean_exits_zero(
    tmp_path, docker, monkeypatch, capsys
):
    project = _make_stack(tmp_path)
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout=_INFO_CLEAN)),
    )

    rc = rm.migrate_rag_dbs(project, check=True)

    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_migrate_rag_dbs_check_pending_exits_one_and_hints(
    tmp_path, docker, monkeypatch, capsys
):
    project = _make_stack(tmp_path)
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout=_INFO_PENDING)),
    )

    rc = rm.migrate_rag_dbs(project, check=True)

    assert rc == 1
    out = capsys.readouterr().out
    assert "3 migration(s) pending" in out
    assert "0.58.0: Move mutable document attributes" in out
    assert f"docker compose stop {rm.INGESTER_SERVICE}" in out
    assert rm.SHIM in out


def test_migrate_rag_dbs_check_surveys_past_an_unreadable_store(
    tmp_path, docker, monkeypatch, capsys
):
    project = _make_stack(tmp_path, stems=("broken", "haiku.rag"))
    # 'broken' comes first (sorted), and its failure must not stop the survey.
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(
            side_effect=[
                _completed(returncode=1, stderr="lance error\n"),
                _completed(stdout=_INFO_CLEAN),
            ]
        ),
    )

    rc = rm.migrate_rag_dbs(project, check=True)

    assert rc == 1
    out = capsys.readouterr().out
    assert "✗ broken.lancedb: haiku-rag exited 1" in out
    assert "✓ haiku.rag.lancedb: up to date" in out
    assert "1 database(s) could not be read" in out


# --------------------------------------------------------------------------
# migrate_rag_dbs (apply)
# --------------------------------------------------------------------------
def test_migrate_rag_dbs_applies_to_every_database(
    tmp_path, docker, monkeypatch, capsys
):
    project = _make_stack(tmp_path, stems=("handbook", "haiku.rag"))
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(rm.subprocess, "run", run)

    rc = rm.migrate_rag_dbs(project)

    assert rc == 0
    # one 'ps -q' guard, then one 'migrate' per database
    migrated = [
        call.args[0][-1]
        for call in run.call_args_list
        if "migrate" in call.args[0]
    ]
    assert migrated == [
        "/data/haiku.rag.lancedb",
        "/data/handbook.lancedb",
    ]
    assert "Migrated 2 database(s)" in capsys.readouterr().out


def test_migrate_rag_dbs_refuses_while_the_ingester_runs(
    tmp_path, docker, monkeypatch
):
    project = _make_stack(tmp_path)
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        mock.Mock(return_value=_completed(stdout="abc123\n")),
    )

    with pytest.raises(rm.IngesterRunning) as exc_info:
        rm.migrate_rag_dbs(project)

    assert f"docker compose stop {rm.INGESTER_SERVICE}" in str(exc_info.value)


def test_migrate_rag_dbs_honors_db_name_selection(
    tmp_path, docker, monkeypatch
):
    project = _make_stack(tmp_path, stems=("handbook", "haiku.rag"))
    run = mock.Mock(return_value=_completed())
    monkeypatch.setattr(rm.subprocess, "run", run)

    rc = rm.migrate_rag_dbs(project, names=["handbook"])

    assert rc == 0
    migrated = [
        call.args[0][-1]
        for call in run.call_args_list
        if "migrate" in call.args[0]
    ]
    assert migrated == ["/data/handbook.lancedb"]
