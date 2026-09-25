"""Unit tests for the ``soliplex_template.soliplex_migrations`` core.

It ships as an installed package, so it is imported directly. Tests are
hermetic: stacks are built under ``tmp_path`` and the ``shutil.which`` /
``subprocess.run`` seams (or the module's own step functions) are
monkeypatched -- no Docker, no containers, no Postgres.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from soliplex_template import soliplex_migrations as sm

# What 'soliplex-cli database status' prints (at COLUMNS=1000, so unwrapped;
# the rule is shortened here) for each state it reports, as recorded from a
# soliplex 0.82 backend image.
_RULE = "──────── Database migrations ────────\n"
_STATUS_UNSTAMPED = (
    _RULE + "head: b7e2f41c9d05\n"
    "- agui: postgresql+psycopg://soliplex_agui:***@postgres/soliplex_agui\n"
    "  policy: (unset)\n"
    "  ERROR: agui: tables are present but alembic_version is empty, so this"
    " database was created by soliplex 0.81 or earlier. Apply"
    " 'scripts/bootstrap_alembic_version.py' (from a soliplex checkout)"
    " once.\n"
    "- authz: postgresql+psycopg://soliplex_authz:***@postgres/soliplex_authz\n"
    "  policy: (unset)\n"
    "  ERROR: authz: tables are present but alembic_version is empty, so this"
    " database was created by soliplex 0.81 or earlier.\n"
)
_STATUS_BEHIND = (
    _RULE + "head: b7e2f41c9d05\n"
    "- agui: postgresql+psycopg://soliplex_agui:***@postgres/soliplex_agui\n"
    "  policy: (unset)\n"
    "  applied: 4\n"
    "    d5009d4f9874  soliplex_v_0_44\n"
    "    4b63e5f3f39e  soliplex-v0.51.1\n"
    "    216d48e1e2e5  soliplex-v0.53\n"
    "    63edaa5987f6  soliplex-v0.67\n"
    "  pending: 2\n"
    "    a1c7d3e90b42  soliplex-v0.80\n"
    "    b7e2f41c9d05  soliplex-v0.82\n"
    "- authz: postgresql+psycopg://soliplex_authz:***@postgres/soliplex_authz\n"
    "  policy: (unset)\n"
    "  applied: 6\n"
    "    d5009d4f9874  soliplex_v_0_44\n"
    "    4b63e5f3f39e  soliplex-v0.51.1\n"
    "    216d48e1e2e5  soliplex-v0.53\n"
    "    63edaa5987f6  soliplex-v0.67\n"
    "    a1c7d3e90b42  soliplex-v0.80\n"
    "    b7e2f41c9d05  soliplex-v0.82\n"
    "  pending: 0\n"
)
_STATUS_UNREACHABLE = (
    _RULE + "head: b7e2f41c9d05\n"
    "- agui: postgresql+psycopg://soliplex_agui:***@postgres/soliplex_agui\n"
    "  policy: (unset)\n"
    "  ERROR: agui: did not open: OperationalError: failed to resolve host"
    " 'postgres'\n"
    "(Background on this error at: https://sqlalche.me/e/20/e3q8)\n"
)
_BOOTSTRAP_DRY_RUN = (
    "agui: postgresql+psycopg://soliplex_agui:***@postgres/soliplex_agui\n"
    "authz: postgresql+psycopg://soliplex_authz:***@postgres/soliplex_authz\n"
    "revision: 63edaa5987f6 (detected from authz.room_acl_entries.json_path)\n"
    "dry run: nothing written\n"
)

_CURRENT = sm.DatabaseStatus("agui", sm.CURRENT)
_BEHIND = sm.DatabaseStatus(
    "agui", sm.BEHIND, pending=("a1c7d3e90b42  soliplex-v0.80",)
)
_UNSTAMPED = sm.DatabaseStatus("agui", sm.UNSTAMPED, error="... empty ...")
_BROKEN = sm.DatabaseStatus("authz", sm.BROKEN, error="did not open")


def _status(*databases):
    return sm.Status("b7e2f41c9d05", databases)


def _make_stack(tmp_path, *, compose=True):
    project = tmp_path / "stack"
    project.mkdir(parents=True, exist_ok=True)
    if compose:
        (project / sm.COMPOSE_FILE).write_text("services: {}\n")
    return project


@pytest.fixture
def docker(monkeypatch):
    """Pretend the Docker CLI is on PATH (as ``/usr/bin/docker``)."""
    monkeypatch.setattr(sm.shutil, "which", lambda _name: "/usr/bin/docker")
    return "/usr/bin/docker"


@pytest.fixture
def run(monkeypatch):
    """Replace ``subprocess.run`` with a Mock returning a clean exit."""
    fake = mock.Mock(return_value=_completed())
    monkeypatch.setattr(sm.subprocess, "run", fake)
    return fake


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _backend_prefix(docker, project):
    return [
        docker,
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-deps",
        "--no-TTY",
        "--env",
        f"COLUMNS={sm.CONSOLE_COLUMNS}",
        sm.SERVICE,
    ]


# --------------------------------------------------------------------------
# docker_cli / resolve_project
# --------------------------------------------------------------------------
def test_docker_cli_returns_path(docker):
    found = sm.docker_cli()

    assert found == docker


def test_docker_cli_raises_when_absent(monkeypatch):
    monkeypatch.setattr(sm.shutil, "which", lambda _name: None)

    with pytest.raises(sm.DockerMissing):
        sm.docker_cli()


def test_resolve_project_returns_stack_root(tmp_path):
    project = _make_stack(tmp_path)

    resolved = sm.resolve_project(str(project))

    assert resolved == project.resolve()


def test_resolve_project_without_compose(tmp_path):
    project = _make_stack(tmp_path, compose=False)

    with pytest.raises(sm.ComposeNotFound) as exc_info:
        sm.resolve_project(project)

    assert exc_info.value.path == project.resolve() / sm.COMPOSE_FILE


# --------------------------------------------------------------------------
# backend_argv / backend_running
# --------------------------------------------------------------------------
def test_backend_argv(docker, tmp_path):
    argv = sm.backend_argv(tmp_path, "echo", "hi")

    assert argv == [*_backend_prefix(docker, tmp_path), "echo", "hi"]


@pytest.mark.parametrize(
    "stdout, expected", [("abc123\n", True), ("\n", False)]
)
def test_backend_running(docker, run, tmp_path, stdout, expected):
    run.return_value = _completed(stdout=stdout)

    running = sm.backend_running(tmp_path)

    assert running is expected
    run.assert_called_once_with(
        [
            docker,
            "compose",
            "--project-directory",
            str(tmp_path),
            "ps",
            "-q",
            sm.SERVICE,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


# --------------------------------------------------------------------------
# parse_version / soliplex_version
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [("0.82\n", (0, 82)), ("0.82.1", (0, 82, 1)), ("1.0", (1, 0))],
)
def test_parse_version(text, expected):
    version = sm.parse_version(text)

    assert version == expected


@pytest.mark.parametrize("text", ["", "0.82rc1", "0.82; rm -rf /", "82"])
def test_parse_version_rejects(text):
    with pytest.raises(sm.UnparseableVersion) as exc_info:
        sm.parse_version(text)

    assert exc_info.value.text == text.strip()


@pytest.mark.parametrize("version", ["0.82", "0.82.1", "0.83", "1.0"])
def test_soliplex_version_returns_it(docker, run, tmp_path, version):
    run.return_value = _completed(stdout=f"{version}\n")

    found = sm.soliplex_version(tmp_path)

    assert found == version
    argv = run.call_args.args[0]
    assert argv[: len(_backend_prefix(docker, tmp_path))] == _backend_prefix(
        docker, tmp_path
    )
    assert argv[-3:-1] == [sm.PYTHON, "-c"]


@pytest.mark.parametrize("version", ["0.79", "0.81.1"])
def test_soliplex_version_refuses_old_image(docker, run, tmp_path, version):
    run.return_value = _completed(stdout=f"{version}\n")

    with pytest.raises(sm.ImageTooOld) as exc_info:
        sm.soliplex_version(tmp_path)

    assert exc_info.value.version == version
    assert "docker compose build backend" in str(exc_info.value)


def test_soliplex_version_reports_a_failed_run(docker, run, tmp_path, capsys):
    run.return_value = _completed(returncode=1, stdout="out\n", stderr="err\n")

    with pytest.raises(sm.CommandFailed) as exc_info:
        sm.soliplex_version(tmp_path)

    assert exc_info.value.returncode == 1
    assert capsys.readouterr().out == "out\nerr\n"


# --------------------------------------------------------------------------
# parse_status
# --------------------------------------------------------------------------
def test_parse_status_unstamped():
    status = sm.parse_status(_STATUS_UNSTAMPED)

    assert status.head == "b7e2f41c9d05"
    assert [db.name for db in status.databases] == ["agui", "authz"]
    assert {db.state for db in status.databases} == {sm.UNSTAMPED}
    assert status.databases[0].error.startswith("tables are present")


def test_parse_status_behind_and_current():
    status = sm.parse_status(_STATUS_BEHIND)

    agui, authz = status.databases
    assert agui == sm.DatabaseStatus(
        "agui",
        sm.BEHIND,
        pending=(
            "a1c7d3e90b42  soliplex-v0.80",
            "b7e2f41c9d05  soliplex-v0.82",
        ),
    )
    assert authz == sm.DatabaseStatus("authz", sm.CURRENT)


def test_parse_status_unreachable():
    status = sm.parse_status(_STATUS_UNREACHABLE)

    (agui,) = status.databases
    assert agui.state == sm.BROKEN
    assert agui.error == (
        "did not open: OperationalError: failed to resolve host 'postgres'"
    )


def test_parse_status_empty():
    status = sm.parse_status("Error: no such command 'database'\n")

    assert status == sm.Status(None, ())


def test_status_in_state():
    status = _status(_BEHIND, _BROKEN)

    found = status.in_state(sm.BROKEN)

    assert found == [_BROKEN]


# --------------------------------------------------------------------------
# database_status
# --------------------------------------------------------------------------
def test_database_status_tolerates_nonzero_exit(docker, run, tmp_path):
    run.return_value = _completed(returncode=1, stdout=_STATUS_UNSTAMPED)

    status = sm.database_status(tmp_path)

    assert len(status.in_state(sm.UNSTAMPED)) == 2
    argv = run.call_args.args[0]
    assert argv[-4:] == [
        sm.SOLIPLEX_CLI,
        "database",
        "status",
        sm.INSTALLATION_PATH,
    ]


def test_database_status_without_databases(docker, run, tmp_path, capsys):
    run.return_value = _completed(returncode=2, stdout="out\n", stderr="err\n")

    with pytest.raises(sm.UnparseableStatus):
        sm.database_status(tmp_path)

    assert capsys.readouterr().out == "out\nerr\n"


# --------------------------------------------------------------------------
# bootstrap_argv / bootstrap_revision / bootstrap / upgrade
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dry_run, tail",
    [
        (True, ["--installation-path", "/environment", "--dry-run"]),
        (False, ["--installation-path", "/environment"]),
    ],
)
def test_bootstrap_argv(docker, tmp_path, dry_run, tail):
    argv = sm.bootstrap_argv(tmp_path, "0.82.1", dry_run=dry_run)

    prefix = _backend_prefix(docker, tmp_path)
    assert argv[: len(prefix)] == prefix
    sh, dash_c, script, arg0, url, *rest = argv[len(prefix) :]
    assert (sh, dash_c, arg0) == ("sh", "-c", "bootstrap")
    assert "0.82.1" not in script
    assert url == (
        "https://raw.githubusercontent.com/soliplex/soliplex/"
        "v0.82.1/scripts/bootstrap_alembic_version.py"
    )
    assert rest == tail


@pytest.mark.parametrize(
    "stdout, expected",
    [(_BOOTSTRAP_DRY_RUN, "63edaa5987f6"), ("nothing to stamp\n", None)],
)
def test_bootstrap_revision(docker, run, tmp_path, stdout, expected):
    run.return_value = _completed(stdout=stdout)

    revision = sm.bootstrap_revision(tmp_path, "0.82")

    assert revision == expected
    assert run.call_args.args[0][-1] == "--dry-run"


@pytest.mark.parametrize(
    "step, args",
    [(sm.bootstrap, ("0.82",)), (sm.upgrade, ())],
)
def test_writing_step_runs_uncaptured(docker, run, tmp_path, step, args):
    step(tmp_path, *args)

    run.assert_called_once()
    assert run.call_args.kwargs == {"check": False}


@pytest.mark.parametrize(
    "step, args, what",
    [
        (sm.bootstrap, ("0.82",), "bootstrap_alembic_version.py"),
        (sm.upgrade, (), "soliplex-cli database upgrade"),
    ],
)
def test_writing_step_failure(docker, run, tmp_path, step, args, what):
    run.return_value = _completed(returncode=1)

    with pytest.raises(sm.CommandFailed) as exc_info:
        step(tmp_path, *args)

    assert exc_info.value.what == what
    assert exc_info.value.returncode == 1


# --------------------------------------------------------------------------
# _report
# --------------------------------------------------------------------------
@pytest.fixture
def steps(monkeypatch):
    """Replace the module's step functions, recording their calls."""
    fakes = mock.Mock()
    fakes.database_status.return_value = _status(_CURRENT)
    fakes.bootstrap_revision.return_value = "63edaa5987f6"
    fakes.backend_running.return_value = False
    for name in (
        "database_status",
        "bootstrap_revision",
        "bootstrap",
        "upgrade",
        "backend_running",
    ):
        monkeypatch.setattr(sm, name, getattr(fakes, name))
    return fakes


def test_report_all_current(steps, tmp_path, capsys):
    rc = sm._report(tmp_path, "0.82")

    assert rc == 0
    steps.bootstrap_revision.assert_not_called()
    out = capsys.readouterr().out
    assert out == "soliplex 0.82; head b7e2f41c9d05\n✓ agui: at head\n"


def test_report_behind(steps, tmp_path, capsys):
    steps.database_status.return_value = _status(_BEHIND)

    rc = sm._report(tmp_path, "0.82")

    assert rc == 1
    out = capsys.readouterr().out
    assert "! agui: 1 migration(s) pending\n" in out
    assert "    a1c7d3e90b42  soliplex-v0.80\n" in out
    assert f"  uv run {sm.SHIM}\n" in out


def test_report_unstamped_names_the_stamp(steps, tmp_path, capsys):
    steps.database_status.return_value = _status(_UNSTAMPED)

    rc = sm._report(tmp_path, "0.82")

    assert rc == 1
    steps.bootstrap_revision.assert_called_once_with(tmp_path, "0.82")
    out = capsys.readouterr().out
    assert (
        "! agui: unstamped, created by soliplex 0.81 or earlier"
        " (would stamp 63edaa5987f6)\n"
    ) in out


def test_report_broken_withholds_apply_hint(steps, tmp_path, capsys):
    steps.database_status.return_value = _status(_BEHIND, _BROKEN)

    rc = sm._report(tmp_path, "0.82")

    assert rc == 1
    out = capsys.readouterr().out
    assert "✗ authz: did not open\n" in out
    assert "1 database(s) cannot be migrated" in out
    assert sm.SHIM not in out


# --------------------------------------------------------------------------
# _apply
# --------------------------------------------------------------------------
def test_apply_refuses_while_backend_runs(steps, tmp_path):
    steps.backend_running.return_value = True

    with pytest.raises(sm.BackendRunning):
        sm._apply(tmp_path, "0.82")

    steps.database_status.assert_not_called()


def test_apply_refuses_broken(steps, tmp_path):
    steps.database_status.return_value = _status(_BEHIND, _BROKEN)

    with pytest.raises(sm.DatabasesBroken) as exc_info:
        sm._apply(tmp_path, "0.82")

    assert exc_info.value.names == ["authz"]
    steps.upgrade.assert_not_called()


def test_apply_nothing_to_do(steps, tmp_path, capsys):
    rc = sm._apply(tmp_path, "0.82")

    assert rc == 0
    steps.bootstrap.assert_not_called()
    steps.upgrade.assert_not_called()
    assert "Nothing to migrate.\n" in capsys.readouterr().out


def test_apply_upgrades_behind(steps, tmp_path):
    steps.database_status.return_value = _status(_BEHIND)

    rc = sm._apply(tmp_path, "0.82")

    assert rc == 0
    steps.bootstrap.assert_not_called()
    steps.upgrade.assert_called_once_with(tmp_path)


def test_apply_stamps_then_upgrades(steps, tmp_path, capsys):
    steps.database_status.side_effect = [
        _status(_UNSTAMPED),
        _status(_BEHIND),
    ]

    rc = sm._apply(tmp_path, "0.82")

    assert rc == 0
    steps.bootstrap.assert_called_once_with(tmp_path, "0.82")
    steps.upgrade.assert_called_once_with(tmp_path)
    out = capsys.readouterr().out
    assert out.index("unstamped") < out.index("1 migration(s) pending")


# --------------------------------------------------------------------------
# migrate_soliplex_dbs
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "check, chosen, other",
    [(True, "_report", "_apply"), (False, "_apply", "_report")],
)
def test_migrate_soliplex_dbs_dispatches(
    monkeypatch, tmp_path, check, chosen, other
):
    project = _make_stack(tmp_path)
    monkeypatch.setattr(sm, "soliplex_version", mock.Mock(return_value="0.82"))
    fakes = {name: mock.Mock(return_value=7) for name in (chosen, other)}
    for name, fake in fakes.items():
        monkeypatch.setattr(sm, name, fake)

    rc = sm.migrate_soliplex_dbs(project, check=check)

    assert rc == 7
    fakes[chosen].assert_called_once_with(project.resolve(), "0.82")
    fakes[other].assert_not_called()
