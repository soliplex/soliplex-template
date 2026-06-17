"""Unit tests for the bundled ``scripts/init_gitea.py`` shim.

The script ships into gitea-enabled stacks and is a thin PEP 723 front end over
``soliplex_template.gitea``; it is loaded here by file path via
``importlib.util``. The library itself is tested in ``test_gitea``.

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
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "init_gitea.py"
)
_spec = importlib.util.spec_from_file_location("init_gitea", _MODULE_PATH)
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)


def test_default_project_is_stack_root():
    project = shim.default_project()

    assert project == _MODULE_PATH.resolve().parent.parent


def test_parse_args_defaults_to_none():
    args = shim.parse_args([])

    assert args.project_dir is None
    assert args.admin_user is None
    assert args.push_to_gitea is False
    assert args.stack_repo is None
    assert args.ssh_key is None


def test_main_uses_default_project(monkeypatch):
    gitea = mock.Mock()
    monkeypatch.setattr(shim, "gitea", gitea)

    rc = shim.main([])

    assert rc == 0
    gitea.provision_gitea.assert_called_once_with(
        shim.default_project(),
        webui_user=None,
        webui_password=None,
        push_to_gitea=False,
        stack_repo=None,
        ssh_key=None,
    )


def test_main_uses_project_dir_arg(monkeypatch):
    gitea = mock.Mock()
    monkeypatch.setattr(shim, "gitea", gitea)

    rc = shim.main(["--project-dir", "/some/stack"])

    assert rc == 0
    gitea.provision_gitea.assert_called_once_with(
        "/some/stack",
        webui_user=None,
        webui_password=None,
        push_to_gitea=False,
        stack_repo=None,
        ssh_key=None,
    )


def test_main_admin_user_prompts_for_password(monkeypatch):
    gitea = mock.Mock()
    monkeypatch.setattr(shim, "gitea", gitea)
    monkeypatch.setattr(
        shim.getpass, "getpass", mock.Mock(side_effect=["s3kr3t", "s3kr3t"])
    )

    rc = shim.main(["--admin-user", "alice"])

    assert rc == 0
    gitea.provision_gitea.assert_called_once_with(
        shim.default_project(),
        webui_user="alice",
        webui_password="s3kr3t",
        push_to_gitea=False,
        stack_repo=None,
        ssh_key=None,
    )


def test_main_push_to_gitea_threads_options_through(monkeypatch):
    gitea = mock.Mock()
    monkeypatch.setattr(shim, "gitea", gitea)

    rc = shim.main(
        ["--push-to-gitea", "--stack-repo", "acme", "--ssh-key", "/k.pub"]
    )

    assert rc == 0
    gitea.provision_gitea.assert_called_once_with(
        shim.default_project(),
        webui_user=None,
        webui_password=None,
        push_to_gitea=True,
        stack_repo="acme",
        ssh_key="/k.pub",
    )


def test_main_admin_user_rejects_service_account(monkeypatch):
    from soliplex_template import gitea as gitea_module

    gitea = mock.Mock()
    gitea.ADMIN_USER = gitea_module.ADMIN_USER
    gitea.ReservedUser = gitea_module.ReservedUser
    getpass = mock.Mock()
    monkeypatch.setattr(shim, "gitea", gitea)
    monkeypatch.setattr(shim.getpass, "getpass", getpass)

    with pytest.raises(gitea_module.ReservedUser):
        shim.main(["--admin-user", gitea_module.ADMIN_USER])

    getpass.assert_not_called()
    gitea.provision_gitea.assert_not_called()


def test_prompt_admin_password_mismatch_raises(monkeypatch):
    from soliplex_template import gitea as gitea_module

    monkeypatch.setattr(
        shim.getpass, "getpass", mock.Mock(side_effect=["a", "b"])
    )

    with pytest.raises(gitea_module.PasswordMismatch):
        shim._prompt_admin_password("alice")
