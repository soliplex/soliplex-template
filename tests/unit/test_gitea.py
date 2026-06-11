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
import json
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

    st_gitea.ensure_admin_user("svc", "svc@x", "pw", project_dir="/stack")

    assert compose.call_count == 1
    create_call = compose.call_args_list[0]
    assert "create" in create_call.args
    assert "svc" in create_call.args
    assert "svc@x" in create_call.args
    assert "pw" in create_call.args
    assert create_call.kwargs == {"project_dir": "/stack"}


def test_ensure_admin_user_resets_when_exists(monkeypatch, capsys):
    created = mock.Mock(returncode=1)
    changed = mock.Mock(returncode=0)
    compose = mock.Mock(side_effect=[created, changed])
    monkeypatch.setattr(st_gitea, "_docker_compose_gitea", compose)

    st_gitea.ensure_admin_user("svc", "svc@x", "pw", project_dir="/stack")

    assert compose.call_count == 2
    create_call, change_call = compose.call_args_list
    assert "create" in create_call.args
    assert "change-password" in change_call.args
    assert "change-password" not in create_call.args
    assert "pw" in change_call.args
    assert "resetting password" in capsys.readouterr().out
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
    ensure = mock.Mock()
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: True)
    monkeypatch.setattr(st_gitea, "ensure_admin_user", ensure)
    monkeypatch.setattr(st_gitea, "mint_token", mint)
    monkeypatch.setattr(st_gitea, "create_repo", mock.Mock())

    result = st_gitea.provision_gitea(tmp_path)

    assert result is None
    env_text = (tmp_path / ".env").read_text()
    assert f"GITEA_HOST={st_gitea.GITEA_INTERNAL_URL}" in env_text
    assert "GITEA_ACCESS_TOKEN=tok999" in env_text
    # the token name is an internal 'concierge-<epoch>' default.
    assert mint.call_args.kwargs["token_name"].startswith("concierge-")
    # with no web-UI user, only the rotating service account is ensured.
    assert ensure.call_count == 1
    assert ensure.call_args_list[0].args[0] == st_gitea.ADMIN_USER


def test_provision_gitea_creates_distinct_webui_admin(tmp_path, monkeypatch):
    ensure = mock.Mock()
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: True)
    monkeypatch.setattr(st_gitea, "ensure_admin_user", ensure)
    monkeypatch.setattr(st_gitea, "mint_token", mock.Mock(return_value="t"))
    monkeypatch.setattr(st_gitea, "create_repo", mock.Mock())

    st_gitea.provision_gitea(tmp_path, webui_user="alice", webui_password="pw")

    assert ensure.call_count == 2
    service_call, webui_call = ensure.call_args_list
    assert service_call.args[0] == st_gitea.ADMIN_USER
    assert webui_call.args[0] == "alice"
    assert webui_call.args[2] == "pw"


def test_provision_gitea_rejects_service_account_as_webui(tmp_path):
    with pytest.raises(st_gitea.GiteaError, match="rotating service account"):
        st_gitea.provision_gitea(
            tmp_path, webui_user=st_gitea.ADMIN_USER, webui_password="pw"
        )


def test_provision_gitea_push_to_gitea_uses_dir_name(tmp_path, monkeypatch):
    push = mock.Mock()
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: True)
    monkeypatch.setattr(st_gitea, "ensure_admin_user", mock.Mock())
    monkeypatch.setattr(st_gitea, "mint_token", mock.Mock(return_value="t"))
    monkeypatch.setattr(st_gitea, "create_repo", mock.Mock())
    monkeypatch.setattr(st_gitea, "push_stack_to_gitea", push)

    st_gitea.provision_gitea(tmp_path, push_to_gitea=True)

    push.assert_called_once()
    assert push.call_args.kwargs["repo_name"] == tmp_path.name
    assert push.call_args.kwargs["ssh_key"] is None


def test_provision_gitea_push_to_gitea_honors_overrides(tmp_path, monkeypatch):
    push = mock.Mock()
    monkeypatch.setattr(st_gitea, "wait_for_gitea", lambda **kw: True)
    monkeypatch.setattr(st_gitea, "ensure_admin_user", mock.Mock())
    monkeypatch.setattr(st_gitea, "mint_token", mock.Mock(return_value="t"))
    monkeypatch.setattr(st_gitea, "create_repo", mock.Mock())
    monkeypatch.setattr(st_gitea, "push_stack_to_gitea", push)

    st_gitea.provision_gitea(
        tmp_path, push_to_gitea=True, stack_repo="custom", ssh_key="/k.pub"
    )

    assert push.call_args.kwargs["repo_name"] == "custom"
    assert push.call_args.kwargs["ssh_key"] == "/k.pub"


# --------------------------------------------------------------------------
# stack_ssh_url
# --------------------------------------------------------------------------
def test_stack_ssh_url():
    url = st_gitea.stack_ssh_url("myrepo")

    assert url == f"ssh://git@localhost:2222/{st_gitea.ADMIN_USER}/myrepo.git"


# --------------------------------------------------------------------------
# create_repo (named, non-auto-init: the stack-backing variant)
# --------------------------------------------------------------------------
def test_create_repo_posts_name_and_auto_init(monkeypatch):
    urlopen = mock.Mock(return_value=_urlopen_returning(getcode=201))
    monkeypatch.setattr(st_gitea.urllib.request, "urlopen", urlopen)

    code = st_gitea.create_repo("pw", name="stackrepo", auto_init=False)

    assert code == 201
    body = json.loads(urlopen.call_args.args[0].data)
    assert body == {"name": "stackrepo", "auto_init": False, "private": False}


# --------------------------------------------------------------------------
# discover_ssh_keys
# --------------------------------------------------------------------------
def test_discover_ssh_keys_explicit(tmp_path):
    keyfile = tmp_path / "id.pub"
    keyfile.write_text("ssh-ed25519 AAAA explicit\n")

    keys = st_gitea.discover_ssh_keys(ssh_key=str(keyfile))

    assert keys == ["ssh-ed25519 AAAA explicit"]


def test_discover_ssh_keys_from_agent(monkeypatch):
    run = mock.Mock(
        return_value=mock.Mock(returncode=0, stdout="ssh-ed25519 AAAA agent\n")
    )
    monkeypatch.setattr(st_gitea.subprocess, "run", run)

    keys = st_gitea.discover_ssh_keys()

    assert keys == ["ssh-ed25519 AAAA agent"]
    run.assert_called_once_with(
        ["ssh-add", "-L"], capture_output=True, text=True
    )


def test_discover_ssh_keys_falls_back_to_files_when_agent_empty(
    tmp_path, monkeypatch
):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA file\n")
    no_ids = mock.Mock(
        return_value=mock.Mock(returncode=1, stdout="no identities.\n")
    )
    monkeypatch.setattr(st_gitea.subprocess, "run", no_ids)
    monkeypatch.setattr(st_gitea.pathlib.Path, "home", lambda: tmp_path)

    keys = st_gitea.discover_ssh_keys()

    assert keys == ["ssh-ed25519 AAAA file"]


def test_discover_ssh_keys_falls_back_when_no_agent(tmp_path, monkeypatch):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id.pub").write_text("ssh-rsa BBBB noagent\n")
    monkeypatch.setattr(
        st_gitea.subprocess, "run", mock.Mock(side_effect=OSError("absent"))
    )
    monkeypatch.setattr(st_gitea.pathlib.Path, "home", lambda: tmp_path)

    keys = st_gitea.discover_ssh_keys()

    assert keys == ["ssh-rsa BBBB noagent"]


# --------------------------------------------------------------------------
# _key_title
# --------------------------------------------------------------------------
def test_key_title_is_prefixed():
    title = st_gitea._key_title("ssh-ed25519 AAAA me")

    assert title.startswith("soliplex-template-")
    assert len(title) == len("soliplex-template-") + 12


# --------------------------------------------------------------------------
# upload_ssh_key
# --------------------------------------------------------------------------
def test_upload_ssh_key_success(monkeypatch):
    urlopen = mock.Mock(return_value=_urlopen_returning())
    monkeypatch.setattr(st_gitea.urllib.request, "urlopen", urlopen)

    st_gitea.upload_ssh_key("ssh-ed25519 AAAA", password="pw", title="t")

    req = urlopen.call_args.args[0]
    assert req.full_url.endswith("/api/v1/user/keys")
    assert json.loads(req.data) == {"title": "t", "key": "ssh-ed25519 AAAA"}


def test_upload_ssh_key_already_present_is_ignored(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        mock.Mock(side_effect=_http_error(422)),
    )

    st_gitea.upload_ssh_key("k", password="pw", title="t")


def test_upload_ssh_key_other_error_raises(monkeypatch):
    monkeypatch.setattr(
        st_gitea.urllib.request,
        "urlopen",
        mock.Mock(side_effect=_http_error(500, b'{"message": "boom"}')),
    )

    with pytest.raises(st_gitea.GiteaError, match="HTTP 500.*boom"):
        st_gitea.upload_ssh_key("k", password="pw", title="t")


# --------------------------------------------------------------------------
# _git / current_branch / set_origin / push_initial
# --------------------------------------------------------------------------
def test_git_runs_in_project_dir(monkeypatch):
    run = mock.Mock(return_value="ok")
    monkeypatch.setattr(st_gitea.subprocess, "run", run)

    result = st_gitea._git("status", project_dir="/stack")

    assert result == "ok"
    run.assert_called_once_with(
        ["git", "-C", "/stack", "status"],
        capture_output=True,
        text=True,
        env=None,
    )


def test_current_branch_returns_name(monkeypatch):
    git = mock.Mock(return_value=mock.Mock(stdout="feature-x\n"))
    monkeypatch.setattr(st_gitea, "_git", git)

    branch = st_gitea.current_branch("/stack")

    assert branch == "feature-x"
    git.assert_called_once_with(
        "rev-parse", "--abbrev-ref", "HEAD", project_dir="/stack"
    )


def test_set_origin_adds_when_absent(monkeypatch):
    git = mock.Mock(side_effect=[mock.Mock(stdout="upstream\n"), mock.Mock()])
    monkeypatch.setattr(st_gitea, "_git", git)

    st_gitea.set_origin("/stack", "ssh://x/r.git")

    second = git.call_args_list[1]
    assert second.args == ("remote", "add", "origin", "ssh://x/r.git")
    assert second.kwargs == {"project_dir": "/stack"}


def test_set_origin_updates_when_present(monkeypatch):
    git = mock.Mock(side_effect=[mock.Mock(stdout="origin\n"), mock.Mock()])
    monkeypatch.setattr(st_gitea, "_git", git)

    st_gitea.set_origin("/stack", "ssh://x/r.git")

    second = git.call_args_list[1]
    assert second.args == ("remote", "set-url", "origin", "ssh://x/r.git")


def test_push_initial_pushes_with_ssh_env(monkeypatch):
    git = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(st_gitea, "_git", git)

    st_gitea.push_initial("/stack", "main")

    call = git.call_args
    assert call.args == ("push", "-u", "origin", "main")
    assert call.kwargs["project_dir"] == "/stack"
    assert "accept-new" in call.kwargs["env"]["GIT_SSH_COMMAND"]
    assert call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_push_initial_failure_raises(monkeypatch):
    git = mock.Mock(return_value=mock.Mock(returncode=1, stderr="denied\n"))
    monkeypatch.setattr(st_gitea, "_git", git)

    with pytest.raises(st_gitea.GiteaError, match="denied"):
        st_gitea.push_initial("/stack", "main")


# --------------------------------------------------------------------------
# push_stack_to_gitea
# --------------------------------------------------------------------------
def test_push_stack_to_gitea_rejects_non_git_dir(tmp_path):
    with pytest.raises(st_gitea.GiteaError, match="not a git repository"):
        st_gitea.push_stack_to_gitea(tmp_path, "pw", repo_name="r")


def test_push_stack_to_gitea_requires_an_ssh_key(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(st_gitea, "discover_ssh_keys", lambda **kw: [])

    with pytest.raises(st_gitea.GiteaError, match="no SSH public key"):
        st_gitea.push_stack_to_gitea(tmp_path, "pw", repo_name="r")


def test_push_stack_to_gitea_uploads_creates_sets_origin_pushes(
    tmp_path, monkeypatch
):
    (tmp_path / ".git").mkdir()
    upload = mock.Mock()
    create = mock.Mock()
    set_origin = mock.Mock()
    push = mock.Mock()
    monkeypatch.setattr(
        st_gitea, "discover_ssh_keys", lambda **kw: ["k1", "k2"]
    )
    monkeypatch.setattr(st_gitea, "upload_ssh_key", upload)
    monkeypatch.setattr(st_gitea, "create_repo", create)
    monkeypatch.setattr(st_gitea, "set_origin", set_origin)
    monkeypatch.setattr(st_gitea, "current_branch", lambda p: "main")
    monkeypatch.setattr(st_gitea, "push_initial", push)

    st_gitea.push_stack_to_gitea(tmp_path, "pw", repo_name="myrepo")

    assert upload.call_count == 2
    create.assert_called_once_with("pw", name="myrepo", auto_init=False)
    set_origin.assert_called_once_with(
        tmp_path, st_gitea.stack_ssh_url("myrepo")
    )
    push.assert_called_once_with(tmp_path, "main")
