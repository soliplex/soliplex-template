"""Unit tests for ``soliplex_template.provision``.

``provision_room`` runs against a small stack laid out under ``tmp_path``:
an installation tree whose ``room_paths`` lists the provisioned-rooms
clone, the ingester config the template ships, an uploads tree and a
database directory. Git and every file edit are real; the backend's API,
``soliplex-cli``, the ingester's reload and the concierge are replaced by
recording fakes.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import dataclasses
import datetime
import fcntl
import io
import json
import pathlib
import subprocess
import sys
import types
import urllib.error

import pytest
import yaml
from mako import template as mako_template

from soliplex_template import ingester
from soliplex_template import landing_clone
from soliplex_template import provision
from soliplex_template import room_ids

REPO = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.UTC)
CLAIMS = {
    "preferred_username": "alice",
    "name": "Alice Example",
    "email": "alice@example.com",
}
ROOM = "alice:q3-plans"


def _git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def git_config(tmp_path, monkeypatch):
    """An empty global git config file, in effect for the test."""
    path = tmp_path / "gitconfig"
    path.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    return path


@pytest.fixture
def settings(tmp_path, git_config):
    environment = tmp_path / "environment"
    environment.mkdir()
    clone = tmp_path / "src" / "rooms-provisioned"
    (environment / "installation.yaml").write_text(
        f'id: "test"\nroom_paths:\n  - "./rooms"\n  - "{clone}"\n'
    )
    ingester_config = tmp_path / "haiku.rag.yaml"
    ingester_config.write_text(
        mako_template.Template(
            filename=str(REPO / "haiku.rag" / "haiku.rag.yaml.mako"),
            strict_undefined=True,
        ).render(chunk_size=256, rag_embed_model="m", rag_embed_dim=8)
    )
    (tmp_path / "db").mkdir()
    return provision.Settings(
        ingester_token="bearer",
        environment=environment,
        clone=clone,
        uploads_dir=tmp_path / "uploads" / "rooms",
        db_dir=tmp_path / "db",
        ingester_config=ingester_config,
        soliplex_cli=str(tmp_path / "soliplex-cli"),
    )


@pytest.fixture
def gitea_settings(tmp_path, settings):
    """Gitea settings whose rooms 'origin' is a local bare repository."""
    host = tmp_path / "gitea"
    origin = host / "ops" / "rooms.git"
    origin.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(origin)], check=True
    )
    return dataclasses.replace(
        settings,
        gitea=provision.GiteaSettings(
            host=str(host),
            token="gitea-token",
            owner="ops",
            repo="requests",
            rooms_repo="rooms",
        ),
    )


class _Recorder:
    """Stands in for the provisioner's collaborators, recording calls.

    ``fail`` maps a call name to the exception that call raises.
    """

    def __init__(self, monkeypatch, *, fail=None, check_lock_of=None):
        self.calls = []
        self.fail = fail or {}
        self.check_lock_of = check_lock_of
        self.lock_held = []
        # The room the backend's rooms reload reports adding.
        self.loaded = ROOM
        monkeypatch.setattr(provision, "_backend", self._backend)
        monkeypatch.setattr(provision, "_cli", self._cli)
        monkeypatch.setattr(provision.ingester, "reload", self._reload)
        monkeypatch.setattr(provision, "concierge_issues", self._concierge)

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise self.fail.pop(name)

    def _backend(self, settings, method, path):
        self._record("backend", method, path)
        if method == "POST":
            return {"added": [self.loaded], "removed": [], "changed": []}
        return {}

    def _cli(self, settings, *command, stdin=None):
        if self.check_lock_of is not None:
            lock = landing_clone.lock_path(self.check_lock_of)
            with lock.open("a") as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.lock_held.append(command[0])
        self._record("cli", command, stdin)
        return ""

    def _reload(self, url, token):
        self._record("reload", url, token)

    def _concierge(self):
        return self._create, self._comment, self._close

    def _create(self, **kwargs):
        self._record("create_issue", kwargs)
        return types.SimpleNamespace(
            number=7, url="https://gitea.example.com/ops/requests/issues/7"
        )

    def _comment(self, host, token, owner, repo, number, body):
        self._record("comment_issue", number, body)

    def _close(self, host, token, owner, repo, number):
        self._record("close_issue", number)

    def named(self, name):
        return [call[1:] for call in self.calls if call[0] == name]


def _provision(settings, **kwargs):
    kwargs.setdefault("name", "Q3 Plans")
    return provision.provision_room(
        CLAIMS, settings=settings, now=NOW, **kwargs
    )


# ---- provision_room: success ----


def test_provision_room(settings, monkeypatch):
    recorder = _Recorder(monkeypatch)

    result = _provision(settings, description="Plans for Q3")

    room_dir = settings.clone / ROOM
    assert result == provision.Provisioned(
        room_id=ROOM,
        room_dir=room_dir,
        commit=_git(settings.clone, "rev-parse", "HEAD"),
        issue_url=None,
    )
    config = yaml.safe_load((room_dir / "room_config.yaml").read_text())
    assert config["id"] == ROOM
    assert config["name"] == "Q3 Plans"
    assert config["description"] == "Plans for Q3"
    record = (room_dir / "REQUEST.md").read_text()
    assert record.startswith("# Q3 Plans\n")
    assert "Tracking issue" not in record
    assert (settings.uploads_dir / ROOM).is_dir()
    registered = yaml.safe_load(settings.ingester_config.read_text())
    assert ROOM in registered["ingester"]["databases"]
    assert ROOM in registered["lancedb"]["databases"]
    assert recorder.named("reload") == [
        ("http://haiku-ingester:8765", "bearer")
    ]
    [(policy_cmd, policy), (audit_cmd, _stdin)] = recorder.named("cli")
    assert policy_cmd == (
        "room-authz",
        "from-yaml",
        str(settings.environment),
        ROOM,
    )
    assert json.loads(policy) == json.loads(
        provision.owner_policy(
            ROOM,
            room_ids.owner_from_claims(CLAIMS),
        )
    )
    assert audit_cmd == (
        "audit",
        "--quiet",
        "rooms",
        str(settings.environment),
    )
    assert recorder.named("backend") == [
        ("GET", "/v1/installation"),
        ("POST", "/v1/installation/reload/rooms"),
    ]
    log = _git(settings.clone, "log", "-1", "--format=%an|%ae|%s")
    assert log == "Alice Example|alice@example.com|Provision room " + ROOM
    assert _git(settings.clone, "status", "--porcelain") == ""


def test_provision_room_holds_the_lock(settings, monkeypatch):
    recorder = _Recorder(monkeypatch, check_lock_of=settings.clone)

    _provision(settings)

    assert recorder.lock_held == ["room-authz", "audit"]


def test_provision_room_with_explicit_slug(settings, monkeypatch):
    _Recorder(monkeypatch)

    result = _provision(settings, slug="plans")

    assert result.room_id == "alice:plans"


def test_provision_room_with_gitea_and_concierge(gitea_settings, monkeypatch):
    recorder = _Recorder(monkeypatch)

    result = _provision(gitea_settings)

    issue_url = "https://gitea.example.com/ops/requests/issues/7"
    assert result.issue_url == issue_url
    assert result.incomplete == ()
    [(created,)] = recorder.named("create_issue")
    assert created["host"] == gitea_settings.gitea.host
    assert created["token"] == "gitea-token"
    assert (created["owner"], created["repo"]) == ("ops", "requests")
    assert created["user"] == CLAIMS
    assert created["title"] == "New room request: Q3 Plans"
    assert created["request_type"] == "new-room"
    assert "Tracking issue" not in created["body"]
    record = (result.room_dir / "REQUEST.md").read_text()
    assert f"- **Tracking issue:** {issue_url}\n" in record
    message = _git(gitea_settings.clone, "log", "-1", "--format=%B")
    assert "Tracking issue: ops/requests#7" in message
    origin = pathlib.Path(gitea_settings.gitea.origin_url)
    assert _git(origin, "rev-parse", "main") == result.commit
    assert recorder.named("comment_issue") == [
        (7, f"Provisioned room `{ROOM}`.")
    ]
    assert recorder.named("close_issue") == [(7,)]


def test_provision_room_with_gitea_without_concierge(
    gitea_settings, monkeypatch
):
    recorder = _Recorder(monkeypatch)
    monkeypatch.setattr(provision, "concierge_issues", lambda: None)

    result = _provision(gitea_settings)

    assert result.issue_url is None
    assert recorder.named("create_issue") == []
    origin = pathlib.Path(gitea_settings.gitea.origin_url)
    assert _git(origin, "rev-parse", "main") == result.commit


# ---- provision_room: refusals before any change ----


def test_provision_room_refused_admin_access(settings, monkeypatch):
    _Recorder(monkeypatch, fail={"backend": provision.AdminAccessDenied(403)})

    with pytest.raises(provision.AdminAccessDenied):
        _provision(settings)

    assert not settings.clone.exists()


def test_provision_room_existing_room(settings, monkeypatch):
    _Recorder(monkeypatch)
    (settings.clone / ROOM).mkdir(parents=True)
    original = settings.ingester_config.read_text()

    with pytest.raises(provision.ProvisionError) as caught:
        _provision(settings)

    assert type(caught.value) is provision.RoomExists
    assert caught.value.room_id == ROOM
    assert settings.ingester_config.read_text() == original


def test_provision_room_bad_name(settings, monkeypatch):
    recorder = _Recorder(monkeypatch)

    with pytest.raises(room_ids.SlugEmpty):
        _provision(settings, name="!!!")

    assert recorder.calls == []


# ---- provision_room: rollback before the commit ----


def test_provision_room_rolls_back(gitea_settings, monkeypatch):
    original = gitea_settings.ingester_config.read_text()
    db = gitea_settings.db_dir / f"{ROOM}.lancedb"
    db.mkdir()  # as the ingester's reload creates it
    fail = provision.CliFailed(("audit",), "room broken")
    calls_before_audit = []

    def fail_audit(settings, *command, stdin=None):
        calls_before_audit.append(command[0])
        if command[0] == "audit":
            raise fail

    recorder = _Recorder(monkeypatch)
    monkeypatch.setattr(provision, "_cli", fail_audit)

    with pytest.raises(provision.ProvisionError) as caught:
        _provision(gitea_settings)

    assert type(caught.value) is provision.ProvisioningFailed
    assert caught.value.error is fail
    assert caught.value.rollback_failures == ()
    assert "rolled back" in str(caught.value)
    assert not (gitea_settings.clone / ROOM).exists()
    assert not (gitea_settings.uploads_dir / ROOM).exists()
    assert gitea_settings.ingester_config.read_text() == original
    assert not db.exists()
    assert len(recorder.named("reload")) == 2
    assert calls_before_audit == ["room-authz", "audit", "room-authz"]
    assert _git(gitea_settings.clone, "log", "--all", "--oneline") == ""
    [(number, comment)] = recorder.named("comment_issue")
    assert number == 7
    assert "room broken" in comment
    assert recorder.named("close_issue") == []


def test_provision_room_reports_failed_rollback(settings, monkeypatch):
    reloads = []

    def reload(url, token):
        reloads.append(url)
        raise ingester.IngesterUnreachable(url, "down")

    _Recorder(monkeypatch)
    monkeypatch.setattr(provision.ingester, "reload", reload)

    with pytest.raises(provision.ProvisioningFailed) as caught:
        _provision(settings)

    assert len(reloads) == 2
    [failure] = caught.value.rollback_failures
    assert failure.startswith("restore the ingester config")
    assert "these steps failed" in str(caught.value)
    assert not (settings.clone / ROOM).exists()


def test_provision_room_rollback_comment_failure_is_ignored(
    gitea_settings, monkeypatch
):
    recorder = _Recorder(
        monkeypatch,
        fail={
            "reload": ingester.ReloadRefused(422, "bad"),
            "comment_issue": RuntimeError("gitea down"),
        },
    )

    with pytest.raises(provision.ProvisioningFailed) as caught:
        _provision(gitea_settings)

    assert isinstance(caught.value.error, ingester.ReloadRefused)
    assert len(recorder.named("comment_issue")) == 1


# ---- provision_room: failures after the commit ----


def test_provision_room_reports_incomplete_steps(gitea_settings, monkeypatch):
    origin = pathlib.Path(gitea_settings.gitea.origin_url)
    subprocess.run(["rm", "-rf", str(origin)], check=True)
    recorder = _Recorder(monkeypatch)
    calls = {"backend": 0}

    def backend(settings, method, path):
        calls["backend"] += 1
        if method == "POST":
            raise provision.BackendRequestFailed(path, "HTTP 409: workers")
        return {}

    monkeypatch.setattr(provision, "_backend", backend)
    recorder.fail["close_issue"] = RuntimeError("gitea down")

    result = _provision(gitea_settings)

    push, backend_reload, issue = result.incomplete
    assert push.startswith("push the provisioned-rooms clone")
    assert backend_reload.startswith("reload the backend's rooms")
    assert "HTTP 409: workers" in backend_reload
    assert issue.startswith(f"comment on and close {result.issue_url}")
    assert (result.room_dir / "room_config.yaml").is_file()
    assert result.commit == _git(gitea_settings.clone, "rev-parse", "HEAD")


def test_provision_room_backend_did_not_load_the_room(settings, monkeypatch):
    recorder = _Recorder(monkeypatch)
    recorder.loaded = "someone:else"

    result = _provision(settings)

    [step] = result.incomplete
    assert step.startswith(f"make the backend load room '{ROOM}'")
    assert repr(str(settings.clone)) in step


# ---- pieces ----


def test_owner_policy():
    owner = room_ids.RoomOwner(
        claim="email",
        claim_value='al"ice@example.com',
        owner="al_ice_example.com",
    )

    result = provision.owner_policy("al_ice_example.com:x", owner)

    json_path = '$[?$.email == "al\\"ice@example.com"]'
    assert json.loads(result) == {
        "room_id": "al_ice_example.com:x",
        "default_allow_deny": "DENY",
        "acl_entries": [
            {
                "allow_deny": "ALLOW",
                "permission": "READ",
                "json_path": json_path,
            },
            {
                "allow_deny": "ALLOW",
                "permission": "WRITE",
                "json_path": json_path,
            },
        ],
    }


@pytest.mark.parametrize("claim", ["", "1st", "preferred-username", "a.b"])
def test_settings_rejects_non_identifier_owner_claim(claim):
    with pytest.raises(provision.ProvisionError) as caught:
        provision.Settings(ingester_token="t", owner_claim=claim)

    assert type(caught.value) is provision.BadOwnerClaim
    assert caught.value.claim == claim


def test_settings_repr_hides_tokens():
    settings = provision.Settings(
        ingester_token="ingester-secret",
        admin_token="admin-secret",
        gitea=provision.GiteaSettings(host="h", token="gitea-secret"),
    )

    result = repr(settings)

    assert "secret" not in result


def test_gitea_settings_origin_url():
    gitea_settings = provision.GiteaSettings(
        host="https://gitea.example.com/", token="t"
    )

    result = gitea_settings.origin_url

    assert result == (
        "https://gitea.example.com/soliplex-admin/rooms-provisioned.git"
    )


@pytest.mark.parametrize(
    "claims, expected",
    [
        (CLAIMS, ("Alice Example", "alice@example.com")),
        (
            {"preferred_username": "alice"},
            ("alice", "alice@users.soliplex.invalid"),
        ),
    ],
)
def test_author(claims, expected):
    owner = room_ids.owner_from_claims(claims)

    result = provision._author(claims, owner)

    assert result == expected


# ---- concierge_issues ----


def test_concierge_issues_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "soliplex_concierge", None)

    result = provision.concierge_issues()

    assert result is None


def _fake_concierge(monkeypatch, *, with_sync):
    package = types.ModuleType("soliplex_concierge")
    tools = types.ModuleType("soliplex_concierge.tools")
    tools_gitea = types.ModuleType("soliplex_concierge.tools.gitea")
    gitea_admin = types.ModuleType("soliplex_concierge.gitea_admin")
    if with_sync:
        tools_gitea.create_gitea_issue_sync = object()
    gitea_admin.comment_issue = object()
    gitea_admin.close_issue = object()
    package.tools, package.gitea_admin, tools.gitea = (
        tools,
        gitea_admin,
        tools_gitea,
    )
    for module in (package, tools, tools_gitea, gitea_admin):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return tools_gitea, gitea_admin


def test_concierge_issues_present(monkeypatch):
    tools_gitea, gitea_admin = _fake_concierge(monkeypatch, with_sync=True)

    result = provision.concierge_issues()

    assert result == (
        tools_gitea.create_gitea_issue_sync,
        gitea_admin.comment_issue,
        gitea_admin.close_issue,
    )


def test_concierge_issues_too_old(monkeypatch):
    _fake_concierge(monkeypatch, with_sync=False)

    result = provision.concierge_issues()

    assert result is None


# ---- the backend's API ----


class _FakeUrlopen:
    def __init__(self, *, response=None, error=None):
        self.response, self.error = response, error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode())


def _http_error(code, body=""):
    return urllib.error.HTTPError(
        "http://backend:8000", code, "error", {}, io.BytesIO(body.encode())
    )


@pytest.mark.parametrize("admin_token", [None, "jwt"])
def test_check_admin_access(monkeypatch, admin_token):
    fake = _FakeUrlopen(response={"id": "test"})
    monkeypatch.setattr(provision.urllib.request, "urlopen", fake)
    settings = provision.Settings(ingester_token="t", admin_token=admin_token)

    result = provision.check_admin_access(settings)

    assert result is None
    [request] = fake.requests
    assert request.full_url == "http://backend:8000/api/v1/installation"
    assert request.get_method() == "GET"
    expected = None if admin_token is None else f"Bearer {admin_token}"
    assert request.get_header("Authorization") == expected


def test_reload_backend_rooms(monkeypatch):
    delta = {"added": [ROOM], "removed": [], "changed": []}
    fake = _FakeUrlopen(response=delta)
    monkeypatch.setattr(provision.urllib.request, "urlopen", fake)
    settings = provision.Settings(ingester_token="t")

    result = provision.reload_backend_rooms(settings)

    assert result == delta
    [request] = fake.requests
    assert request.full_url == (
        "http://backend:8000/api/v1/installation/reload/rooms"
    )
    assert request.get_method() == "POST"


@pytest.mark.parametrize("code", [401, 403])
def test_backend_denied(monkeypatch, code):
    fake = _FakeUrlopen(error=_http_error(code))
    monkeypatch.setattr(provision.urllib.request, "urlopen", fake)
    settings = provision.Settings(ingester_token="t")

    with pytest.raises(provision.ProvisionError) as caught:
        provision.check_admin_access(settings)

    assert type(caught.value) is provision.AdminAccessDenied
    assert caught.value.code == code
    assert "SOLIPLEX_ADMIN_TOKEN" in str(caught.value)


@pytest.mark.parametrize(
    "error, detail",
    [
        (
            _http_error(409, '{"detail": "workers"}'),
            'HTTP 409: {"detail": "workers"}',
        ),
        (urllib.error.URLError("refused"), "refused"),
    ],
)
def test_backend_request_failed(monkeypatch, error, detail):
    fake = _FakeUrlopen(error=error)
    monkeypatch.setattr(provision.urllib.request, "urlopen", fake)
    settings = provision.Settings(ingester_token="t")

    with pytest.raises(provision.ProvisionError) as caught:
        provision.reload_backend_rooms(settings)

    assert type(caught.value) is provision.BackendRequestFailed
    assert caught.value.detail == detail


# ---- soliplex-cli ----


@pytest.fixture
def fake_cli(tmp_path):
    """A stand-in 'soliplex-cli': echoes its args and stdin, exits $FAKE_RC."""
    script = tmp_path / "soliplex-cli"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "print(' '.join(sys.argv[1:]), sys.stdin.read())\n"
        "sys.exit(int(os.environ.get('FAKE_RC', '0')))\n"
    )
    script.chmod(0o755)
    return script


def test_cli(fake_cli):
    settings = provision.Settings(
        ingester_token="t", soliplex_cli=str(fake_cli)
    )

    result = provision._cli(settings, "audit", "rooms", stdin="null")

    assert result == "audit rooms null\n"


def test_cli_failed(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_RC", "1")
    settings = provision.Settings(
        ingester_token="t", soliplex_cli=str(fake_cli)
    )

    with pytest.raises(provision.ProvisionError) as caught:
        provision._cli(settings, "audit", "rooms")

    assert type(caught.value) is provision.CliFailed
    assert caught.value.command == ("audit", "rooms")
    assert caught.value.output == "audit rooms"


def test_provision_room_issue_filing_failed(gitea_settings, monkeypatch):
    recorder = _Recorder(
        monkeypatch, fail={"create_issue": RuntimeError("HTTP 403")}
    )

    with pytest.raises(provision.ProvisionError) as caught:
        _provision(gitea_settings)

    assert type(caught.value) is provision.IssueFilingFailed
    assert str(caught.value.error) == "HTTP 403"
    assert not (gitea_settings.clone / ROOM).exists()
    assert recorder.named("reload") == []


# ---- Settings.from_env ----


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "ingester_bearer"
    path.write_text("bearer\n")
    return path


def test_settings_from_env_defaults(token_file):
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file)}

    result = provision.Settings.from_env(environ)

    assert result == provision.Settings(ingester_token="bearer")


def test_settings_from_env(token_file):
    environ = {
        "PROVISIONER_INGESTER_TOKEN_FILE": str(token_file),
        "SOLIPLEX_ADMIN_TOKEN": "jwt",
        "PROVISIONER_OWNER_CLAIM": "email",
        "PROVISIONER_FLATTEN_OWNER": "True",
        "GITEA_HOST": "https://gitea.example.com",
        "GITEA_ACCESS_TOKEN": "gt",
        "GITEA_OWNER": "ops",
        "GITEA_REPO": "requests",
        "GITEA_ROOMS_REPO": "rooms",
    }

    result = provision.Settings.from_env(environ)

    assert result == provision.Settings(
        ingester_token="bearer",
        admin_token="jwt",
        owner_claim="email",
        flatten_owner=True,
        gitea=provision.GiteaSettings(
            host="https://gitea.example.com",
            token="gt",
            owner="ops",
            repo="requests",
            rooms_repo="rooms",
        ),
    )


def test_settings_from_env_gitea_defaults(token_file):
    environ = {
        "PROVISIONER_INGESTER_TOKEN_FILE": str(token_file),
        "GITEA_HOST": "http://gitea:3000",
        "GITEA_ACCESS_TOKEN": "gt",
    }

    result = provision.Settings.from_env(environ)

    assert result.gitea == provision.GiteaSettings(
        host="http://gitea:3000",
        token="gt",
        owner="soliplex-admin",
        repo="soliplex-requests",
        rooms_repo="rooms-provisioned",
    )


def test_settings_from_env_reads_the_secret_by_default(monkeypatch):
    monkeypatch.setattr(provision, "INGESTER_TOKEN_FILE", "/nonexistent/x")

    with pytest.raises(provision.ProvisionError) as caught:
        provision.Settings.from_env({})

    assert type(caught.value) is provision.IngesterTokenMissing
    assert caught.value.path == pathlib.Path("/nonexistent/x")


@pytest.mark.parametrize(
    "extra, exc_type",
    [
        ({"GITEA_HOST": "http://gitea:3000"}, provision.GiteaTokenMissing),
        ({"PROVISIONER_FLATTEN_OWNER": "maybe"}, provision.BadFlag),
        ({"PROVISIONER_OWNER_CLAIM": "a-b"}, provision.BadOwnerClaim),
    ],
)
def test_settings_from_env_rejects(token_file, extra, exc_type):
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file), **extra}

    with pytest.raises(provision.ProvisionError) as caught:
        provision.Settings.from_env(environ)

    assert type(caught.value) is exc_type


# ---- main ----


def _fake_provision(monkeypatch, *, result=None, error=None):
    calls = []

    def provision_room(claims, **kwargs):
        calls.append((claims, kwargs))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(provision, "provision_room", provision_room)
    return calls


def test_main(token_file, monkeypatch, capsys):
    calls = _fake_provision(
        monkeypatch,
        result=provision.Provisioned(
            room_id=ROOM,
            room_dir=pathlib.Path("/app/src/rooms-provisioned") / ROOM,
            commit="0123456789abcdef",
            issue_url=None,
        ),
    )
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file)}

    result = provision.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"], environ
    )

    assert result == 0
    [(claims, kwargs)] = calls
    assert claims == CLAIMS
    assert kwargs == {
        "name": "Q3 Plans",
        "settings": provision.Settings(ingester_token="bearer"),
        "description": None,
        "slug": None,
    }
    assert capsys.readouterr().out == (
        f"provisioned room '{ROOM}' (commit 0123456789ab)\n"
    )


def test_main_incomplete(token_file, monkeypatch, capsys):
    _fake_provision(
        monkeypatch,
        result=provision.Provisioned(
            room_id=ROOM,
            room_dir=pathlib.Path("/x"),
            commit="0123456789abcdef",
            issue_url="https://gitea.example.com/ops/requests/issues/7",
            incomplete=("push it", "reload it"),
        ),
    )
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file)}

    result = provision.main(
        [
            "--claims",
            json.dumps(CLAIMS),
            "--name",
            "Q3 Plans",
            "--description",
            "D",
            "--slug",
            "plans",
        ],
        environ,
    )

    assert result == 1
    assert capsys.readouterr().out == (
        f"provisioned room '{ROOM}' (commit 0123456789ab)\n"
        "tracking issue: https://gitea.example.com/ops/requests/issues/7\n"
        "still to do:\n"
        "  - push it\n"
        "  - reload it\n"
    )


def test_main_error(token_file, monkeypatch, capsys):
    _fake_provision(monkeypatch, error=provision.RoomExists(ROOM))
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file)}

    result = provision.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"], environ
    )

    assert result == 2
    assert capsys.readouterr().err == (
        f"error: room '{ROOM}' already exists; choose another name\n"
    )


def test_main_settings_error(tmp_path, capsys):
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(tmp_path / "missing")}

    result = provision.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"], environ
    )

    assert result == 2
    assert "cannot read the ingester's bearer token" in capsys.readouterr().err


def test_main_claims_not_an_object(capsys):
    result = provision.main(["--claims", "[1, 2]", "--name", "Q3 Plans"], {})

    assert result == 2
    assert capsys.readouterr().err == (
        "error: --claims must be a JSON object\n"
    )


def test_main_uses_the_process_environment(token_file, monkeypatch):
    monkeypatch.setenv("PROVISIONER_INGESTER_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("SOLIPLEX_ADMIN_TOKEN", "from-env")
    calls = _fake_provision(monkeypatch, error=provision.RoomExists(ROOM))

    provision.main(["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"])

    [(_claims, kwargs)] = calls
    assert kwargs["settings"].admin_token == "from-env"
