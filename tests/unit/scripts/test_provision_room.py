"""Unit tests for the skill's ``provision_room.py`` shim.

The script is not part of an importable package, so it is loaded here by
file path via ``importlib.util``; ``docker`` is never run (``subprocess.run``
is replaced).

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import runpy
import subprocess
import sys
import types

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "provision_room.py"
)
_spec = importlib.util.spec_from_file_location("provision_room", _MODULE_PATH)
provision_room = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provision_room)


@pytest.fixture
def stack(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    return tmp_path


@pytest.fixture
def fake_run(monkeypatch):
    """Replaces ``subprocess.run``; records argv, exits ``returncode``."""
    calls = []

    def run(argv):
        calls.append(argv)
        return types.SimpleNamespace(returncode=fake_run.returncode)

    fake_run.returncode = 0
    fake_run.calls = calls
    monkeypatch.setattr(subprocess, "run", run)
    return fake_run


def _compose_prefix(stack):
    return [
        "docker",
        "compose",
        "--project-directory",
        str(stack),
        "run",
        "--rm",
        "--no-TTY",
        "--quiet-build",
    ]


@pytest.mark.parametrize(
    "argv, claims",
    [
        (["--username", "alice"], {"preferred_username": "alice"}),
        (
            [
                "--username",
                "alice",
                "--email",
                "alice@example.com",
                "--display-name",
                "Alice Example",
            ],
            {
                "preferred_username": "alice",
                "email": "alice@example.com",
                "name": "Alice Example",
            },
        ),
        (
            ["--claims", '{"sub": "1234", "preferred_username": "alice"}'],
            {"sub": "1234", "preferred_username": "alice"},
        ),
    ],
)
def test_main(stack, fake_run, argv, claims):
    fake_run.returncode = 1

    result = provision_room.main(
        ["--project-dir", str(stack), *argv, "--name", "Q3 Plans"], {}
    )

    assert result == 1
    [command] = fake_run.calls
    assert command == [
        *_compose_prefix(stack),
        "soliplex-dev",
        "/app/.venv/bin/soliplex-provision-room",
        "--claims",
        json.dumps(claims),
        "--name",
        "Q3 Plans",
    ]


def test_main_room_options_and_admin_token(stack, fake_run):
    environ = {"SOLIPLEX_ADMIN_TOKEN": "jwt"}

    provision_room.main(
        [
            "--project-dir",
            str(stack),
            "--username",
            "alice",
            "--name",
            "Q3 Plans",
            "--description",
            "Plans",
            "--slug",
            "plans",
        ],
        environ,
    )

    [command] = fake_run.calls
    assert command[8:11] == ["-e", "SOLIPLEX_ADMIN_TOKEN", "soliplex-dev"]
    assert "jwt" not in command
    assert command[-4:] == ["--description", "Plans", "--slug", "plans"]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--name", "Q3 Plans"], "pass --claims, or at least --username"),
        (
            ["--claims", "{nope", "--name", "x"],
            "--claims must be a JSON object: Expecting property name",
        ),
        (
            ["--claims", "[1]", "--name", "x"],
            "--claims must be a JSON object: got list",
        ),
        (
            [
                "--project-dir",
                "/nonexistent",
                "--username",
                "a",
                "--name",
                "x",
            ],
            "/nonexistent has no docker-compose.yml",
        ),
    ],
)
def test_main_errors(stack, fake_run, monkeypatch, capsys, argv, message):
    monkeypatch.chdir(stack)

    result = provision_room.main(argv, {})

    assert result == 2
    assert message in capsys.readouterr().err
    assert fake_run.calls == []


def test_run_as_script(stack, fake_run, monkeypatch):
    fake_run.returncode = 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision_room.py",
            "--project-dir",
            str(stack),
            "--username",
            "alice",
            "--name",
            "Q3 Plans",
        ],
    )

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(_MODULE_PATH), run_name="__main__")

    assert caught.value.code == 0
    assert len(fake_run.calls) == 1
