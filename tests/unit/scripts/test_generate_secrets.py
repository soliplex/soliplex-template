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


def test_parse_args_defaults_project_dir_to_none():
    args = shim.parse_args([])

    assert args.project_dir is None


def test_main_uses_default_project(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main([])

    assert rc == 0
    generate_secrets.assert_called_once_with(shim.default_project())


def test_main_uses_project_dir_arg(monkeypatch):
    generate_secrets = mock.Mock()
    monkeypatch.setattr(shim, "generate_secrets", generate_secrets)

    rc = shim.main(["--project-dir", "/some/stack"])

    assert rc == 0
    generate_secrets.assert_called_once_with("/some/stack")
