"""Unit tests for the bundled ``scripts/generate_secrets.py`` shim.

The script ships into generated stacks and is a thin PEP 723 front end over
``soliplex_template.secrets``; it is loaded here by file path via
``importlib.util``. The library itself is tested in ``test_secrets``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once.
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts"
    / "generate_secrets.py"
)
_spec = importlib.util.spec_from_file_location(
    "generate_secrets", _MODULE_PATH
)
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)


def test_default_project_is_stack_root():
    project = shim.default_project()

    assert project == _MODULE_PATH.resolve().parent.parent


def test_parse_args_defaults_are_missing_only_mode():
    args = shim.parse_args([])

    assert args.project_dir is None
    assert args.rotate is None
    assert args.rotate_all is False
    assert args.dry_run is False


def test_parse_args_bare_rotate_is_empty_list():
    args = shim.parse_args(["--rotate"])

    assert args.rotate == []


def test_parse_args_rotate_collects_names():
    args = shim.parse_args(["--rotate", "a", "b.gen"])

    assert args.rotate == ["a", "b.gen"]


def test_parse_args_rotate_all_is_flags_only():
    args = shim.parse_args(["--rotate-all"])

    assert args.rotate is None
    assert args.rotate_all is True


def test_parse_args_rejects_rotate_and_rotate_all_together():
    with pytest.raises(SystemExit):
        shim.parse_args(["--rotate", "--rotate-all"])


def test_main_uses_default_project(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main([])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=None,
        dry_run=False,
    )


def test_main_uses_project_dir_arg(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--project-dir", "/some/stack"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        "/some/stack",
        rotate=None,
        dry_run=False,
    )


def test_main_bare_rotate_previews_full_rotation(monkeypatch, capsys):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--rotate"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=shim.ROTATE_ALL,
        dry_run=True,
    )
    assert "previewing the run" in capsys.readouterr().out


def test_main_rotate_passes_names(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--rotate", "a", "b.gen"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=["a", "b.gen"],
        dry_run=False,
    )


def test_main_rotate_all(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--rotate-all"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=shim.ROTATE_ALL,
        dry_run=False,
    )


def test_main_dry_run_flag(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--dry-run"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=None,
        dry_run=True,
    )


def test_main_rotate_all_with_dry_run(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--rotate-all", "--dry-run"])

    assert rc == 0
    generate_secrets.assert_called_once_with(
        shim.default_project(),
        rotate=shim.ROTATE_ALL,
        dry_run=True,
    )
