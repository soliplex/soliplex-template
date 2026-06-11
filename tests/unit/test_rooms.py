"""Unit tests for the generic ``soliplex_template.rooms`` core.

It ships as an installed package, so it is imported directly (no
importlib-by-path). Tests are hermetic: everything is routed through
``tmp_path`` -- pure filesystem, no Docker/network.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import pytest

from soliplex_template import rooms

_INSTALLATION = (
    'name: "demo"\n'
    "\n"
    "# rooms loaded by this install\n"
    "room_paths:\n"
    '  - "./rooms/chat"\n'
    "\n"
    "secrets:\n"
    "  - foo\n"
)


def _make_stack(
    tmp_path, *, compose=True, installation=True, inst_text=_INSTALLATION
):
    """A stack directory with the bits ``resolve_project`` checks for."""
    project = tmp_path / "stack"
    (project / "backend" / "environment").mkdir(parents=True, exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text("services: {}\n")
    if installation:
        (project / "backend" / "environment" / "installation.yaml").write_text(
            inst_text
        )
    return project


def _room_dir(project, room_id="handbook"):
    return project / "backend" / "environment" / "rooms" / room_id


def _installation(project):
    return project / "backend" / "environment" / "installation.yaml"


# --------------------------------------------------------------------------
# validate_room_id
# --------------------------------------------------------------------------
@pytest.mark.parametrize("room_id", ["chat", "a", "a.b_c-1", "Z9"])
def test_validate_room_id_accepts(room_id):
    rooms.validate_room_id(room_id)


@pytest.mark.parametrize("room_id", ["", ".hidden", "a/b", "a b", "../x"])
def test_validate_room_id_rejects(room_id):
    with pytest.raises(rooms.AddRoomError, match="must match"):
        rooms.validate_room_id(room_id)


# --------------------------------------------------------------------------
# add_room_path
# --------------------------------------------------------------------------
def test_add_room_path_added_after_anchor():
    new, action = rooms.add_room_path(_INSTALLATION, "handbook")

    assert action == rooms.ADDED
    assert "# rooms loaded by this install" in new
    lines = new.splitlines()
    anchor = lines.index("room_paths:")
    assert lines[anchor + 1] == '  - "./rooms/handbook"'


def test_add_room_path_covered_by_rooms_parent():
    text = 'room_paths:\n  - "./rooms"\n'

    new, action = rooms.add_room_path(text, "handbook")

    assert action == rooms.COVERED
    assert new == text


def test_add_room_path_unchanged_when_present():
    text = _INSTALLATION.replace('"./rooms/chat"', '"./rooms/handbook"')

    new, action = rooms.add_room_path(text, "handbook")

    assert action == rooms.UNCHANGED
    assert new == text


def test_add_room_path_no_anchor():
    with pytest.raises(rooms.AddRoomError, match="room_paths"):
        rooms.add_room_path("name: x\n", "handbook")


# --------------------------------------------------------------------------
# resolve_project
# --------------------------------------------------------------------------
def test_resolve_project_ok(tmp_path):
    project = _make_stack(tmp_path)

    result = rooms.resolve_project(str(project))

    assert result == project.resolve()


def test_resolve_project_no_compose(tmp_path):
    project = _make_stack(tmp_path, compose=False)

    with pytest.raises(rooms.AddRoomError, match="docker-compose.yml"):
        rooms.resolve_project(str(project))


def test_resolve_project_not_a_stack(tmp_path):
    project = _make_stack(tmp_path, installation=False)

    with pytest.raises(rooms.AddRoomError, match="not a generated"):
        rooms.resolve_project(str(project))


# --------------------------------------------------------------------------
# resolve_package_name
# --------------------------------------------------------------------------
def test_resolve_package_name_override(tmp_path):
    project = _make_stack(tmp_path)

    result = rooms.resolve_package_name(project, "acme_pkg")

    assert result == "acme_pkg"


def test_resolve_package_name_from_src(tmp_path):
    project = _make_stack(tmp_path)
    (project / "src" / "mypkg").mkdir(parents=True)
    (project / "src" / "mypkg" / "tools.py").write_text(
        "def greeting(): ...\n"
    )
    (project / "src" / "notpkg").mkdir()  # excluded: no tools.py
    (project / "src" / "stray.txt").write_text("x")  # excluded: not a dir

    result = rooms.resolve_package_name(project, None)

    assert result == "mypkg"


def test_resolve_package_name_src_without_package(tmp_path):
    project = _make_stack(tmp_path)
    (project / "src").mkdir()

    result = rooms.resolve_package_name(project, None)

    assert result == rooms.DEFAULT_PACKAGE_NAME


def test_resolve_package_name_no_src(tmp_path):
    project = _make_stack(tmp_path)

    result = rooms.resolve_package_name(project, None)

    assert result == rooms.DEFAULT_PACKAGE_NAME


# --------------------------------------------------------------------------
# install_room
# --------------------------------------------------------------------------
def test_install_room_writes_and_adds_path(tmp_path):
    project = _make_stack(tmp_path)

    result = rooms.install_room(
        project, "handbook", config_text='id: "handbook"\n'
    )

    assert result.path_action == rooms.ADDED
    assert result.config_path.read_text() == 'id: "handbook"\n'
    assert not (result.config_path.parent / "prompt.txt").exists()
    assert '"./rooms/handbook"' in _installation(project).read_text()


def test_install_room_writes_prompt_file(tmp_path):
    project = _make_stack(tmp_path)

    result = rooms.install_room(
        project, "qa", config_text="id: qa\n", prompt_text="Be helpful.\n"
    )

    assert (result.config_path.parent / "prompt.txt").read_text() == (
        "Be helpful.\n"
    )


def test_install_room_dry_run_writes_nothing(tmp_path):
    project = _make_stack(tmp_path)
    before = _installation(project).read_text()

    result = rooms.install_room(
        project, "handbook", config_text="id: x\n", dry_run=True
    )

    assert result.path_action == rooms.ADDED
    assert not _room_dir(project).exists()
    assert _installation(project).read_text() == before


def test_install_room_exists_without_force(tmp_path):
    project = _make_stack(tmp_path)
    _room_dir(project).mkdir(parents=True)

    with pytest.raises(rooms.AddRoomError, match="already exists"):
        rooms.install_room(project, "handbook", config_text="id: x\n")


def test_install_room_covered_leaves_installation_untouched(tmp_path):
    project = _make_stack(tmp_path, inst_text='room_paths:\n  - "./rooms"\n')
    inst = _installation(project)
    before = inst.read_text()

    result = rooms.install_room(project, "handbook", config_text="id: x\n")

    assert result.path_action == rooms.COVERED
    assert result.config_path.is_file()
    assert inst.read_text() == before


def test_install_room_force_overwrites_path_unchanged(tmp_path):
    inst_text = _INSTALLATION.replace('"./rooms/chat"', '"./rooms/handbook"')
    project = _make_stack(tmp_path, inst_text=inst_text)
    room = _room_dir(project)
    room.mkdir(parents=True)
    (room / "room_config.yaml").write_text("id: stale\n")
    inst = _installation(project)
    before = inst.read_text()

    result = rooms.install_room(
        project, "handbook", config_text='id: "handbook"\n', force=True
    )

    assert result.path_action == rooms.UNCHANGED
    assert inst.read_text() == before
    assert (room / "room_config.yaml").read_text() == 'id: "handbook"\n'
