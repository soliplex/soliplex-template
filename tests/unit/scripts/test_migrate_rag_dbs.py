"""Unit tests for the bundled ``scripts/migrate_rag_dbs.py`` shim.

The script ships into generated stacks and is a thin PEP 723 front end over
``soliplex_template.rag_migrations``; it is loaded here by file path via
``importlib.util``. The library itself is tested in ``test_rag_migrations``.

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
    / "migrate_rag_dbs.py"
)
_spec = importlib.util.spec_from_file_location("migrate_rag_dbs", _MODULE_PATH)
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)


def test_default_project_is_stack_root():
    project = shim.default_project()

    assert project == _MODULE_PATH.resolve().parent.parent


def test_parse_args_defaults():
    args = shim.parse_args([])

    assert args.project_dir is None
    assert args.db_name is None
    assert args.check is False


def test_parse_args_collects_repeated_db_names():
    args = shim.parse_args(["--db-name", "handbook", "--db-name", "haiku.rag"])

    assert args.db_name == ["handbook", "haiku.rag"]


def test_main_uses_default_project(monkeypatch):
    migrate = mock.Mock(return_value=0)
    monkeypatch.setattr(shim, "migrate_rag_dbs", migrate)

    rc = shim.main([])

    assert rc == 0
    migrate.assert_called_once_with(
        shim.default_project(), names=None, check=False
    )


def test_main_passes_project_dir_names_and_check(monkeypatch):
    migrate = mock.Mock(return_value=1)
    monkeypatch.setattr(shim, "migrate_rag_dbs", migrate)

    rc = shim.main(
        ["--project-dir", "/some/stack", "--db-name", "handbook", "--check"]
    )

    assert rc == 1
    migrate.assert_called_once_with(
        "/some/stack", names=["handbook"], check=True
    )
