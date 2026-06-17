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
    / "skills"
    / "soliplex-template"
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
    project: pathlib.Path,
    dirname: str,
    room_id: str,
    name: str = "X",
    description: str = "d",
) -> pathlib.Path:
    """Create backend/environment/rooms/<dirname>/room_config.yaml."""
    room = project / "backend" / "environment" / "rooms" / dirname
    room.mkdir(parents=True, exist_ok=True)
    cfg = room / "room_config.yaml"
    cfg.write_text(
        f'id: "{room_id}"\nname: "{name}"\ndescription: "{description}"\n'
    )
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

    with pytest.raises(soliplex_config.ComposeNotFound):
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
# navigate
# --------------------------------------------------------------------------
_NAV_CONFIG = {
    "room_paths": ["/environment/rooms/chat", "/environment/rooms/search"],
    "installation": {"name": "demo"},
}


@pytest.mark.parametrize(
    "key, expected",
    [
        (
            "room_paths",
            ["/environment/rooms/chat", "/environment/rooms/search"],
        ),
        ("room_paths.0", "/environment/rooms/chat"),
        ("installation", {"name": "demo"}),
        ("installation.name", "demo"),
    ],
)
def test_navigate_resolves(key, expected):
    result = soliplex_config.navigate(_NAV_CONFIG, key)

    assert result == expected


@pytest.mark.parametrize(
    "key",
    [
        "missing",  # unknown mapping key
        "room_paths.9",  # sequence index out of range
        "room_paths.x",  # non-integer sequence index
        "installation.name.deep",  # descend past a scalar
    ],
)
def test_navigate_not_found(key):
    with pytest.raises(soliplex_config.KeyNotFound):
        soliplex_config.navigate(_NAV_CONFIG, key)


# --------------------------------------------------------------------------
# render_value
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        ("hello", "hello"),
        (42, "42"),
        (True, "true"),
        (False, "false"),
        (None, "null"),
    ],
)
def test_render_plain_scalar(value, expected):
    result = soliplex_config.render_value(value, "plain")

    assert result == expected


def test_render_plain_list_of_scalars_one_per_line():
    result = soliplex_config.render_value(["a", "b", 1], "plain")

    assert result == "a\nb\n1"


def test_render_plain_nested_falls_back_to_yaml():
    result = soliplex_config.render_value([{"k": "v"}], "plain")

    assert result == "- k: v"


def test_render_plain_dict_is_yaml():
    result = soliplex_config.render_value({"a": 1, "b": 2}, "plain")

    assert result == "a: 1\nb: 2"


def test_render_yaml_format_dumps_any_value():
    result = soliplex_config.render_value(["a", "b"], "yaml")

    assert result == "- a\n- b"


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
# read_room_meta
# --------------------------------------------------------------------------
def test_read_room_meta_found():
    result = soliplex_config.read_room_meta(
        'id: "search"\nname: "Search"\ndescription: "find things"\n'
    )

    assert result == {
        "room_id": "search",
        "name": "Search",
        "description": "find things",
    }


def test_read_room_meta_missing_fields_default_to_none():
    result = soliplex_config.read_room_meta('id: "search"\n')

    assert result == {
        "room_id": "search",
        "name": None,
        "description": None,
    }


def test_read_room_meta_no_id():
    result = soliplex_config.read_room_meta("name: x\ntools: []\n")

    assert result is None


def test_read_room_meta_non_mapping():
    result = soliplex_config.read_room_meta("- a\n- b\n")

    assert result is None


# --------------------------------------------------------------------------
# resolve_rooms
# --------------------------------------------------------------------------
def test_resolve_rooms_no_room_paths_key(tmp_path, run):
    project = _make_project(tmp_path)
    run.return_value.stdout = "id: inst\n"  # no room_paths

    with pytest.raises(soliplex_config.NoRoomPaths):
        soliplex_config.resolve_rooms(
            project,
            "backend",
            "/app/.venv/bin/soliplex-cli",
            "/environment",
            "backend/environment",
        )


def test_resolve_rooms_maps_dedups_and_collects_unmapped(tmp_path, run):
    project = _make_project(tmp_path)
    _make_room(project, "chat", "chat", "First", "first wins")
    _make_room(project, "dupe", "chat", "Second", "second loses")  # same id
    _make_room(project, "noid_dir", "x")
    # Blank the noid room's id so read_room_meta returns None.
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

    assert rooms == [
        {"room_id": "chat", "name": "First", "description": "first wins"}
    ]
    assert unmapped == ["/shared/rooms/kb"]


# --------------------------------------------------------------------------
# do_show / main
# --------------------------------------------------------------------------
def test_show_prints_raw_config(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    stdout = _config_yaml("/environment/rooms/chat")
    run.return_value.stdout = stdout

    rc = soliplex_config.main(["show", "--project-dir", str(project)])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == stdout
    assert run.call_args_list == [_expected_run(project)]


# --------------------------------------------------------------------------
# do_get / main
# --------------------------------------------------------------------------
def test_get_list_plain_one_per_line(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    run.return_value.stdout = _config_yaml(
        "/environment/rooms/chat", "/environment/rooms/search"
    )

    rc = soliplex_config.main(
        ["get", "room_paths", "--project-dir", str(project)]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert out == "/environment/rooms/chat\n/environment/rooms/search\n"


def test_get_index_plain_scalar(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    run.return_value.stdout = _config_yaml("/environment/rooms/chat")

    rc = soliplex_config.main(
        ["get", "room_paths.0", "--project-dir", str(project)]
    )

    assert rc == 0
    assert capsys.readouterr().out == "/environment/rooms/chat\n"


def test_get_format_yaml(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    run.return_value.stdout = _config_yaml("/environment/rooms/chat")

    rc = soliplex_config.main(
        [
            "get",
            "room_paths",
            "--format",
            "yaml",
            "--project-dir",
            str(project),
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out == "- /environment/rooms/chat\n"


def test_get_missing_key_errors(tmp_path, which, run):
    project = _make_project(tmp_path)
    run.return_value.stdout = _config_yaml("/environment/rooms/chat")

    with pytest.raises(soliplex_config.KeyNotFound):
        soliplex_config.main(["get", "nope", "--project-dir", str(project)])


# --------------------------------------------------------------------------
# do_rooms / main
# --------------------------------------------------------------------------
def test_rooms_docker_missing(tmp_path, which, run):
    which.return_value = None
    project = _make_project(tmp_path)

    with pytest.raises(soliplex_config.DockerMissing):
        soliplex_config.main(["rooms", "--project-dir", str(project)])

    assert run.call_args_list == []


def test_rooms_prints_mappings_and_warns(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    _make_room(project, "chat", "chat", "Chat", "Conversational RAG")
    _make_room(project, "search", "search", "Search", "Search the KB")
    run.return_value.stdout = _config_yaml(
        "/environment/rooms/chat",
        "/environment/rooms/search",
        "/shared/rooms/kb",
    )

    rc = soliplex_config.main(["rooms", "--project-dir", str(project)])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "- room_id: chat\n"
        "  name: Chat\n"
        "  description: Conversational RAG\n"
        "- room_id: search\n"
        "  name: Search\n"
        "  description: Search the KB\n"
    )
    assert "/shared/rooms/kb" in captured.err
    assert "skipped" in captured.err


# --------------------------------------------------------------------------
# do_room / main
# --------------------------------------------------------------------------
def test_room_prints_full_config(tmp_path, which, run, capsys):
    project = _make_project(tmp_path)
    cfg = _make_room(project, "chat", "chat", "Chat", "Conversational RAG")
    _make_room(project, "search", "search")
    run.return_value.stdout = _config_yaml(
        "/environment/rooms/chat", "/environment/rooms/search"
    )

    rc = soliplex_config.main(["room", "chat", "--project-dir", str(project)])

    assert rc == 0
    assert capsys.readouterr().out == cfg.read_text()


def test_room_not_found_errors(tmp_path, which, run):
    project = _make_project(tmp_path)
    _make_room(project, "chat", "chat")
    run.return_value.stdout = _config_yaml("/environment/rooms/chat")

    with pytest.raises(soliplex_config.RoomNotFound):
        soliplex_config.main(["room", "ghost", "--project-dir", str(project)])


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------
def test_no_command_errors():
    with pytest.raises(SystemExit):
        soliplex_config.parse_args([])
