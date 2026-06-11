"""Unit tests for the generic ``soliplex_template.gitea`` core.

It ships as an installed package, so it is imported directly. Tests are
hermetic: the ``urllib`` / ``subprocess`` / ``time.sleep`` seams are
monkeypatched and ``.env`` writes go through ``tmp_path`` -- no Docker, no
network, no real waiting.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import io
import urllib.error
from unittest import mock

import pytest

from soliplex_template import gitea as st_gitea


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body)
    )


def _urlopen_returning(*, read=None, getcode=None):
    """A MagicMock usable as ``with urlopen(...) as resp``."""
    cm = mock.MagicMock()
    resp = cm.__enter__.return_value
    if read is not None:
        resp.read.return_value = read
    if getcode is not None:
        resp.getcode.return_value = getcode
    return cm


# --------------------------------------------------------------------------
# generate_admin_password
# --------------------------------------------------------------------------
def test_generate_admin_password_satisfies_complexity():
    password = st_gitea.generate_admin_password(24)

    assert password.endswith(st_gitea.PASSWORD_COMPLEXITY_SUFFIX)
    assert len(password) == 24 + len(st_gitea.PASSWORD_COMPLEXITY_SUFFIX)


# --------------------------------------------------------------------------
# _request
# --------------------------------------------------------------------------
def test_request_no_auth_no_body(monkeypatch):
    urlopen = mock.Mock()
    monkeypatch.setattr(st_gitea.urllib.request, "urlopen", urlopen)

    st_gitea._request("GET", "http://x/api")

    req = urlopen.call_args.args[0]
    assert req.get_method() == "GET"
    assert not req.has_header("Authorization")
    assert req.data is None


def test_request_with_auth_and_body(monkeypatch):
    urlopen = mock.Mock()
    monkeypatch.setattr(st_gitea.urllib.request, "urlopen", urlopen)

    st_gitea._request(
        "POST", "http://x/api", user="u", password="p", data={"a": 1}
    )

    req = urlopen.call_args.args[0]
    assert req.has_header("Authorization")
    assert req.data == b'{"a": 1}'


# --------------------------------------------------------------------------
# wait_for_gitea
# --------------------------------------------------------------------------
def test_wait_for_gitea_ready(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request, "urlopen", lambda url: _urlopen_returning()
    )

    ready = st_gitea.wait_for_gitea(attempts=3, sleep=mock.Mock())

    assert ready is True


def test_wait_for_gitea_never_ready(monkeypatch):
    def boom(url):
        raise urllib.error.URLError("down")

    sleep = mock.Mock()
    monkeypatch.setattr(st_gitea.urllib.request, "urlopen", boom)

    ready = st_gitea.wait_for_gitea(attempts=2, sleep=sleep)

    assert ready is False
    assert sleep.call_count == 2


# --------------------------------------------------------------------------
# parse_token
# --------------------------------------------------------------------------
def test_parse_token_returns_sha1():
    token = st_gitea.parse_token('{"sha1": "deadbeef"}')

    assert token == "deadbeef"


@pytest.mark.parametrize("body", ['{"name": "x"}', "not json"])
def test_parse_token_missing_or_invalid_raises(body):
    with pytest.raises(st_gitea.GiteaError, match="could not parse token"):
        st_gitea.parse_token(body)


# --------------------------------------------------------------------------
# _docker_compose_gitea
# --------------------------------------------------------------------------
def test_docker_compose_gitea_runs_exec_as_git(monkeypatch):
    run = mock.Mock(return_value="completed")
    monkeypatch.setattr(st_gitea.subprocess, "run", run)

    result = st_gitea._docker_compose_gitea(
        "gitea", "admin", "whoami", project_dir="/stack"
    )

    assert result == "completed"
    run.assert_called_once_with(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-u",
            "git",
            "gitea",
            "gitea",
            "admin",
            "whoami",
        ],
        cwd="/stack",
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------
# ensure_admin_user
# --------------------------------------------------------------------------
def test_ensure_admin_user_creates(monkeypatch):
    compose = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(st_gitea, "_docker_compose_gitea", compose)

    st_gitea.ensure_admin_user("pw", project_dir="/stack")

    assert compose.call_count == 1
    create_call = compose.call_args_list[0]
    assert "create" in create_call.args
    assert "--password" in create_call.args
    assert "pw" in create_call.args
    assert st_gitea.ADMIN_USER in create_call.args
    assert create_call.kwargs == {"project_dir": "/stack"}


def test_ensure_admin_user_resets_when_exists(monkeypatch, capsys):
    created = mock.Mock(returncode=1)
    changed = mock.Mock(returncode=0)
    compose = mock.Mock(side_effect=[created, changed])
    monkeypatch.setattr(st_gitea, "_docker_compose_gitea", compose)

    st_gitea.ensure_admin_user("pw", project_dir="/stack")

    assert compose.call_count == 2
    create_call, change_call = compose.call_args_list
    assert "create" in create_call.args
    assert "change-password" in change_call.args
    assert "change-password" not in create_call.args
    assert "pw" in change_call.args
    assert "user exists" in capsys.readouterr().out
    changed.check_returncode.assert_called_once_with()


# --------------------------------------------------------------------------
# mint_token
# --------------------------------------------------------------------------
def test_mint_token_success(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        lambda req: _urlopen_returning(read=b'{"sha1": "tok123"}'),
    )

    token = st_gitea.mint_token("pw", token_name="t")

    assert token == "tok123"


def test_mint_token_http_error_raises_with_status_and_body(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        mock.Mock(side_effect=_http_error(403, b'{"message": "forbidden"}')),
    )

    with pytest.raises(st_gitea.GiteaError, match="HTTP 403.*forbidden"):
        st_gitea.mint_token("pw", token_name="t")


def test_mint_token_no_token_raises(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        lambda req: _urlopen_returning(read=b"{}"),
    )

    with pytest.raises(st_gitea.GiteaError, match="could not parse token"):
        st_gitea.mint_token("pw", token_name="t")


# --------------------------------------------------------------------------
# create_repo
# --------------------------------------------------------------------------
def test_create_repo_created(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        lambda req: _urlopen_returning(getcode=201),
    )

    code = st_gitea.create_repo("pw")

    assert code == 201


def test_create_repo_already_exists(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        mock.Mock(side_effect=_http_error(409)),
    )

    code = st_gitea.create_repo("pw")

    assert code == 409


def test_create_repo_failure_raises(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        mock.Mock(side_effect=_http_error(500)),
    )

    with pytest.raises(st_gitea.GiteaError, match="HTTP 500"):
        st_gitea.create_repo("pw")


# --------------------------------------------------------------------------
# set_env_var
# --------------------------------------------------------------------------
def test_set_env_var_replaces_existing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\nGITEA_HOST=old\n")

    st_gitea.set_env_var(env, "GITEA_HOST", "new")

    assert env.read_text() == "OTHER=x\nGITEA_HOST=new\n"


def test_set_env_var_appends_new_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\n")

    st_gitea.set_env_var(env, "GITEA_HOST", "new")

    assert env.read_text() == "OTHER=x\nGITEA_HOST=new\n"


def test_set_env_var_creates_file(tmp_path):
    env = tmp_path / ".env"

    st_gitea.set_env_var(env, "GITEA_HOST", "new")

    assert env.read_text() == "GITEA_HOST=new\n"


# --------------------------------------------------------------------------
# provision_gitea
# --------------------------------------------------------------------------
def test_provision_gitea_not_ready_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: False)

    with pytest.raises(st_gitea.GiteaError, match="did not become ready"):
        st_gitea.provision_gitea(tmp_path)


def test_provision_gitea_writes_env(tmp_path, monkeypatch):
    mint = mock.Mock(return_value="tok999")
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: True)
    monkeypatch.setattr(st_gitea, "ensure_admin_user", mock.Mock())
    monkeypatch.setattr(st_gitea, "mint_token", mint)
    monkeypatch.setattr(st_gitea, "create_repo", mock.Mock())

    result = st_gitea.provision_gitea(tmp_path)

    assert result is None
    env_text = (tmp_path / ".env").read_text()
    assert f"GITEA_HOST={st_gitea.GITEA_INTERNAL_URL}" in env_text
    assert "GITEA_ACCESS_TOKEN=tok999" in env_text
    # the token name is an internal 'concierge-<epoch>' default.
    assert mint.call_args.kwargs["token_name"].startswith("concierge-")
