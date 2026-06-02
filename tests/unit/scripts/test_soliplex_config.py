"""Unit tests for the bundled ``skill/scripts/soliplex_config.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``.
Tests are hermetic: the ``docker``/``shutil.which`` and ``subprocess.run``
seams (the one-off ``soliplex-cli config`` container) are mocked and the
filesystem is routed through ``tmp_path`` -- no real Docker, no network.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skill"
    / "scripts"
    / "soliplex_config.py"
)
_spec = importlib.util.spec_from_file_location("soliplex_config", _MODULE_PATH)
soliplex_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(soliplex_config)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _make_project(tmp_path, *, compose=True) -> pathlib.Path:
    project = tmp_path / "stack"
    project.mkdir(exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text("services: {}\n")
    return project


def _make_room(
    project: pathlib.Path, dirname: str, room_id: str
) -> pathlib.Path:
    """Create backend/environment/rooms/<dirname>/room_config.yaml."""
    room = project / "backend" / "environment" / "rooms" / dirname
    room.mkdir(parents=True, exist_ok=True)
    cfg = room / "room_config.yaml"
    cfg.write_text(f'id: "{room_id}"\nname: "x"\n')
    return cfg


def _config_yaml(*container_paths: str) -> str:
    """A soliplex-cli config export (banner + room_paths) for the mock."""
    lines = [
        "#" + "-" * 78,
        "# Source: /environment",
        "#" + "-" * 78,
        "room_paths:",
    ]
    lines += [f"- {path}" for path in container_paths]
    return "\n".join(lines) + "\n"


def _expected_run(project: pathlib.Path) -> mock._Call:
    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(project.resolve()),
        "run",
        "--rm",
        "--no-TTY",
        "-e",
        "COLUMNS=10000",
        "backend",
        "/app/.venv/bin/soliplex-cli",
        "config",
        "/environment",
    ]
    return mock.call(cmd, capture_output=True, text=True, check=True)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def which(monkeypatch):
    w = mock.Mock(return_value="/usr/bin/docker")
    monkeypatch.setattr(soliplex_config.shutil, "which", w)
    return w


@pytest.fixture
def run(monkeypatch):
    r = mock.Mock()
    monkeypatch.setattr(soliplex_config.subprocess, "run", r)
    return r


# --------------------------------------------------------------------------
# resolve_project
# --------------------------------------------------------------------------
def test_resolve_project_ok(tmp_path):
    project = _make_project(tmp_path)

    resolved = soliplex_config.resolve_project(str(project))

    assert resolved == project.resolve()


def test_resolve_project_no_compose(tmp_path):
    project = _make_project(tmp_path, compose=False)

    with pytest.raises(
        soliplex_config.SoliplexConfigError, match="no docker-compose.yml"
    ):
        soliplex_config.resolve_project(str(project))


# --------------------------------------------------------------------------
# run_config
# --------------------------------------------------------------------------
def test_run_config_builds_argv(tmp_path, run):
    project = _make_project(tmp_path)
    run.return_value.stdout = "room_paths: []\n"

    out = soliplex_config.run_config(
        project, "backend", "/app/.venv/bin/soliplex-cli", "/environment"
    )

    assert out == "room_paths: []\n"
    assert run.call_args_list == [_expected_run(project)]


# --------------------------------------------------------------------------
# parse_config
# --------------------------------------------------------------------------
def test_parse_config_dict_ignores_banner():
    result = soliplex_config.parse_config(
        "# banner\nroom_paths:\n- /environment/rooms/chat\n"
    )

    assert result == {"room_paths": ["/environment/rooms/chat"]}


def test_parse_config_non_mapping_is_empty():
    result = soliplex_config.parse_config("- a\n- b\n")

    assert result == {}


# --------------------------------------------------------------------------
# map_to_host
# --------------------------------------------------------------------------
def test_map_to_host_under_installation(tmp_path):
    host_env = tmp_path / "backend" / "environment"

    mapped = soliplex_config.map_to_host(
        "/environment/rooms/chat", "/environment", host_env
    )

    assert mapped == host_env / "rooms" / "chat"


def test_map_to_host_outside_installation(tmp_path):
    host_env = tmp_path / "backend" / "environment"

    mapped = soliplex_config.map_to_host(
        "/shared/rooms/kb", "/environment", host_env
    )

    assert mapped is None


# --------------------------------------------------------------------------
# find_room_configs
# --------------------------------------------------------------------------
def test_find_room_configs_direct(tmp_path):
    room = tmp_path / "chat"
    room.mkdir()
    cfg = room / "room_config.yaml"
    cfg.write_text('id: "chat"\n')

    found = soliplex_config.find_room_configs(room)

    assert found == [cfg]


def test_find_room_configs_subdirs_skips_hidden_and_configless(tmp_path):
    root = tmp_path / "rooms"
    (root / "chat").mkdir(parents=True)
    chat = root / "chat" / "room_config.yaml"
    chat.write_text('id: "chat"\n')
    (root / ".hidden").mkdir()
    (root / ".hidden" / "room_config.yaml").write_text('id: "h"\n')
    (root / "empty").mkdir()  # no room_config.yaml

    found = soliplex_config.find_room_configs(root)

    assert found == [chat]


def test_find_room_configs_missing_dir(tmp_path):
    found = soliplex_config.find_room_configs(tmp_path / "nope")

    assert found == []


# --------------------------------------------------------------------------
# read_room_id
# --------------------------------------------------------------------------
def test_read_room_id_found():
    result = soliplex_config.read_room_id('name: "x"\nid: "search"\n')

    assert result == "search"


def test_read_room_id_absent():
    result = soliplex_config.read_room_id("name: x\ntools: []\n")

    assert result is None


# --------------------------------------------------------------------------
# resolve_rooms
# --------------------------------------------------------------------------
def test_resolve_rooms_no_room_paths_key(tmp_path, run):
    project = _make_project(tmp_path)
    run.return_value.stdout = "id: inst\n"  # no room_paths

    with pytest.raises(
        soliplex_config.SoliplexConfigError, match="no 'room_paths'"
    ):
        soliplex_config.resolve_rooms(
            project,
            "backend",
            "/app/.venv/bin/soliplex-cli",
            "/environment",
            "backend/environment",
        )


def test_resolve_rooms_maps_dedups_and_collects_unmapped(tmp_path, run):
    project = _make_project(tmp_path)
    chat = _make_room(project, "chat", "chat")
    _make_room(project, "dupe", "chat")  # same id -> first wins
    _make_room(project, "noid_dir", "")  # id "" is falsy -> skipped
    # Blank the noid room's id line so read_room_id returns None.
    (
        project
        / "backend"
        / "environment"
        / "rooms"
        / "noid_dir"
        / "room_config.yaml"
    ).write_text("name: x\n")
    run.return_value.stdout = _config_yaml(
        "/environment/rooms/chat",
        "/environment/rooms/dupe",
        "/environment/rooms/noid_dir",
        "/environment/rooms/absent",  # not on disk -> no configs
        "/shared/rooms/kb",  # outside installation -> unmapped
    )

    rooms, unmapped = soliplex_config.resolve_rooms(
        project,
        "backend",
        "/app/.venv/bin/soliplex-cli",
        "/environment",
        "backend/environment",
    )

    assert rooms == {"chat": chat}
    assert unmapped == ["/shared/rooms/kb"]


# --------------------------------------------------------------------------
# do_room_ids / main
# --------------------------------------------------------------------------
def test_room_ids_docker_missing(tmp_path, which, run):
    which.return_value = None
    project = _make_project(tmp_path)

    with pytest.raises(
        soliplex_config.SoliplexConfigError, match="docker not found"
    ):
        soliplex_config.main(["room_ids", "--project-dir", str(project)])

    assert run.call_args_list == []


def test_room_ids_prints_and_warns(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    _make_room(project, "chat", "chat")
    _make_room(project, "search", "search")
    run.return_value.stdout = _config_yaml(
        "/environment/rooms/chat",
        "/environment/rooms/search",
        "/shared/rooms/kb",
    )

    rc = soliplex_config.main(["room_ids", "--project-dir", str(project)])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == "chat\nsearch\n"
    assert "/shared/rooms/kb" in captured.err
    assert "skipped" in captured.err


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------
def test_no_command_errors():
    with pytest.raises(SystemExit):
        soliplex_config.parse_args([])
