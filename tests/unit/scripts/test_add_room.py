"""Unit tests for the bundled ``skill/scripts/add_room.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``.
Tests are hermetic: everything is routed through ``tmp_path`` and the bundled
templates -- no Docker, no network, no running backend (adding a room is pure
filesystem + Mako render).

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib

import pytest
import yaml

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "add_room.py"
)
_spec = importlib.util.spec_from_file_location("add_room", _MODULE_PATH)
add_room = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(add_room)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
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


def _make_stack(tmp_path, *, compose=True, installation=True):
    """A stack directory with the bits ``resolve_project`` checks for."""
    project = tmp_path / "stack"
    (project / "backend" / "environment").mkdir(parents=True, exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text("services: {}\n")
    if installation:
        (project / "backend" / "environment" / "installation.yaml").write_text(
            _INSTALLATION
        )
    return project


def _add_args(**over):
    """An ``add`` Namespace with defaults, overridable per test."""
    base = {
        "project_dir": ".",
        "template": "chat",
        "room_id": "handbook",
        "name": None,
        "description": None,
        "agent_template": add_room.DEFAULT_AGENT_TEMPLATE,
        "rag_stem": add_room.DEFAULT_RAG_STEM,
        "package_name": None,
        "system_prompt": None,
        "prompt_file": None,
        "force": False,
        "dry_run": False,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _room_dir(project, room_id="handbook"):
    return project / "backend" / "environment" / "rooms" / room_id


def _installation(project):
    return project / "backend" / "environment" / "installation.yaml"


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
def test_available_templates_has_bundled_set():
    templates = add_room.available_templates()

    assert set(templates) >= {"chat", "search", "minimal", "sandbox"}
    assert all(summary for summary in templates.values())


def test_template_summary_missing():
    summary = add_room._template_summary("# just a comment\nid: x\n")

    assert summary == ""


def test_resolve_template_ok():
    path = add_room.resolve_template("chat")

    assert path.is_file()


def test_resolve_template_unknown():
    with pytest.raises(add_room.AddRoomError, match="unknown --template"):
        add_room.resolve_template("does-not-exist")


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("room_id", ["chat", "a", "a.b_c-1", "Z9"])
def test_validate_room_id_accepts(room_id):
    add_room.validate_room_id(room_id)


@pytest.mark.parametrize("room_id", ["", ".hidden", "a/b", "a b", "../x"])
def test_validate_room_id_rejects(room_id):
    with pytest.raises(add_room.AddRoomError, match="must match"):
        add_room.validate_room_id(room_id)


def test_format_system_prompt_file():
    result = add_room.format_system_prompt(None, "prompt.txt")

    assert result == '"./prompt.txt"'


def test_format_system_prompt_inline_keeps_blank_lines():
    result = add_room.format_system_prompt("line one\n\nline two", None)

    assert result == "|\n    line one\n\n    line two"


@pytest.mark.parametrize(
    "name, description, exp_name, exp_desc",
    [
        (None, None, "handbook", "handbook"),
        ("Book", None, "Book", "Book"),
        ("Book", "A desc", "Book", "A desc"),
    ],
)
def test_build_context_name_description(name, description, exp_name, exp_desc):
    args = _add_args(name=name, description=description)

    ctx = add_room.build_context(args, "minimal")

    assert ctx["name"] == exp_name
    assert ctx["description"] == exp_desc


@pytest.mark.parametrize(
    "template, system_prompt, snippet",
    [
        ("chat", None, "helpful agent"),
        ("search", None, "search_documents"),
        ("sandbox", None, "sandbox"),
        ("minimal", None, "helpful assistant"),
        ("chat", "Custom prompt!", "Custom prompt!"),
    ],
)
def test_build_context_system_prompt(template, system_prompt, snippet):
    args = _add_args(system_prompt=system_prompt)

    ctx = add_room.build_context(args, template)

    assert snippet in ctx["system_prompt_yaml"]


def test_build_context_prompt_file_reference():
    args = _add_args(prompt_file="anything.txt")

    ctx = add_room.build_context(args, "chat")

    assert ctx["system_prompt_yaml"] == '"./prompt.txt"'


def test_add_room_path_added_after_anchor():
    new, action = add_room.add_room_path(_INSTALLATION, "handbook")

    assert action == add_room.ADDED
    assert "# rooms loaded by this install" in new
    lines = new.splitlines()
    anchor = lines.index("room_paths:")
    assert lines[anchor + 1] == '  - "./rooms/handbook"'


def test_add_room_path_unchanged_when_present():
    text = _INSTALLATION.replace('"./rooms/chat"', '"./rooms/handbook"')

    new, action = add_room.add_room_path(text, "handbook")

    assert action == add_room.UNCHANGED
    assert new == text


def test_add_room_path_no_anchor():
    with pytest.raises(add_room.AddRoomError, match="room_paths"):
        add_room.add_room_path("name: x\n", "handbook")


# --------------------------------------------------------------------------
# Rendering each bundled template
# --------------------------------------------------------------------------
@pytest.mark.parametrize("template", ["chat", "search", "minimal", "sandbox"])
def test_render_template_is_valid_yaml(template):
    args = _add_args(name="My Room", description="Desc", system_prompt="Hi")
    ctx = add_room.build_context(args, template)

    text = add_room.render_room_config(
        add_room.resolve_template(template), ctx
    )

    data = yaml.safe_load(text)
    assert data["id"] == "handbook"
    assert data["name"] == "My Room"
    assert data["agent"]["template_id"] == "default_chat"


@pytest.mark.parametrize("template", ["chat", "search", "sandbox"])
def test_greeting_tool_uses_package_name(template):
    args = _add_args(system_prompt="Hi")
    ctx = add_room.build_context(args, template, "acme_pkg")

    text = add_room.render_room_config(
        add_room.resolve_template(template), ctx
    )

    tools = [t["tool_name"] for t in yaml.safe_load(text)["tools"]]
    assert "acme_pkg.tools.greeting" in tools


def test_minimal_greeting_tool_is_commented():
    args = _add_args(system_prompt="Hi")
    ctx = add_room.build_context(args, "minimal", "acme_pkg")

    text = add_room.render_room_config(
        add_room.resolve_template("minimal"), ctx
    )

    assert yaml.safe_load(text).get("tools") is None
    assert "acme_pkg.tools.greeting" in text


def test_render_escapes_quotes_in_name():
    args = _add_args(name='He said "hi"', system_prompt="Hi")
    ctx = add_room.build_context(args, "minimal")

    text = add_room.render_room_config(
        add_room.resolve_template("minimal"), ctx
    )

    assert yaml.safe_load(text)["name"] == 'He said "hi"'


# --------------------------------------------------------------------------
# Stack discovery
# --------------------------------------------------------------------------
def test_resolve_project_ok(tmp_path):
    project = _make_stack(tmp_path)

    result = add_room.resolve_project(str(project))

    assert result == project.resolve()


def test_resolve_project_no_compose(tmp_path):
    project = _make_stack(tmp_path, compose=False)

    with pytest.raises(add_room.AddRoomError, match="docker-compose.yml"):
        add_room.resolve_project(str(project))


def test_resolve_project_not_a_stack(tmp_path):
    project = _make_stack(tmp_path, installation=False)

    with pytest.raises(add_room.AddRoomError, match="not a generated"):
        add_room.resolve_project(str(project))


def test_resolve_package_name_override(tmp_path):
    project = _make_stack(tmp_path)

    result = add_room.resolve_package_name(project, "acme_pkg")

    assert result == "acme_pkg"


def test_resolve_package_name_from_src(tmp_path):
    project = _make_stack(tmp_path)
    (project / "src" / "mypkg").mkdir(parents=True)
    (project / "src" / "mypkg" / "tools.py").write_text(
        "def greeting(): ...\n"
    )
    (project / "src" / "notpkg").mkdir()  # excluded: no tools.py
    (project / "src" / "stray.txt").write_text("x")  # excluded: not a dir

    result = add_room.resolve_package_name(project, None)

    assert result == "mypkg"


def test_resolve_package_name_src_without_package(tmp_path):
    project = _make_stack(tmp_path)
    (project / "src").mkdir()

    result = add_room.resolve_package_name(project, None)

    assert result == add_room.DEFAULT_PACKAGE_NAME


def test_resolve_package_name_no_src(tmp_path):
    project = _make_stack(tmp_path)

    result = add_room.resolve_package_name(project, None)

    assert result == add_room.DEFAULT_PACKAGE_NAME


# --------------------------------------------------------------------------
# CLI: list
# --------------------------------------------------------------------------
def test_list(capsys):
    rc = add_room.main(["list"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "chat" in out
    assert "minimal" in out


# --------------------------------------------------------------------------
# CLI: add
# --------------------------------------------------------------------------
def test_add_writes_room_and_updates_paths(tmp_path):
    project = _make_stack(tmp_path)

    rc = add_room.main(
        [
            "add",
            "--project-dir",
            str(project),
            "--template",
            "chat",
            "--room-id",
            "handbook",
        ]
    )

    assert rc == 0
    config = _room_dir(project) / "room_config.yaml"
    assert config.is_file()
    assert not (config.parent / "prompt.txt").exists()
    assert '"./rooms/handbook"' in _installation(project).read_text()


def test_add_with_prompt_file(tmp_path):
    project = _make_stack(tmp_path)
    src = tmp_path / "p.txt"
    src.write_text("Be helpful.\n")

    rc = add_room.main(
        [
            "add",
            "--project-dir",
            str(project),
            "--template",
            "minimal",
            "--room-id",
            "qa",
            "--prompt-file",
            str(src),
        ]
    )

    assert rc == 0
    room = _room_dir(project, "qa")
    assert (room / "prompt.txt").read_text() == "Be helpful.\n"
    data = yaml.safe_load((room / "room_config.yaml").read_text())
    assert data["agent"]["system_prompt"] == "./prompt.txt"


def test_add_prompt_file_missing(tmp_path):
    project = _make_stack(tmp_path)

    with pytest.raises(add_room.AddRoomError, match="prompt-file"):
        add_room.main(
            [
                "add",
                "--project-dir",
                str(project),
                "--template",
                "chat",
                "--room-id",
                "x",
                "--prompt-file",
                str(tmp_path / "nope.txt"),
            ]
        )


def test_add_dry_run_writes_nothing(tmp_path, capsys):
    project = _make_stack(tmp_path)
    before = _installation(project).read_text()

    rc = add_room.main(
        [
            "add",
            "--project-dir",
            str(project),
            "--template",
            "chat",
            "--room-id",
            "handbook",
            "--dry-run",
        ]
    )

    assert rc == 0
    assert "would write" in capsys.readouterr().out
    assert not _room_dir(project).exists()
    assert _installation(project).read_text() == before


def test_add_room_exists_without_force(tmp_path):
    project = _make_stack(tmp_path)
    _room_dir(project).mkdir(parents=True)

    with pytest.raises(add_room.AddRoomError, match="already exists"):
        add_room.main(
            [
                "add",
                "--project-dir",
                str(project),
                "--template",
                "chat",
                "--room-id",
                "handbook",
            ]
        )


def test_add_force_overwrites_with_paths_unchanged(tmp_path):
    project = _make_stack(tmp_path)
    room = _room_dir(project)
    room.mkdir(parents=True)
    (room / "room_config.yaml").write_text("id: stale\n")
    inst = _installation(project)
    inst.write_text(
        inst.read_text().replace('"./rooms/chat"', '"./rooms/handbook"')
    )

    rc = add_room.main(
        [
            "add",
            "--project-dir",
            str(project),
            "--template",
            "minimal",
            "--room-id",
            "handbook",
            "--force",
        ]
    )

    assert rc == 0
    assert inst.read_text().count('"./rooms/handbook"') == 1
    data = yaml.safe_load((room / "room_config.yaml").read_text())
    assert data["id"] == "handbook"
