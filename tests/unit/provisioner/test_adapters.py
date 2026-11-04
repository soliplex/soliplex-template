"""Unit tests for ``soliplex_template.provisioner.adapters``.

The adapters run against a small stack laid out under ``tmp_path``: an
installation tree whose ``room_paths`` lists the provisioned-rooms clone,
the ingester config the template ships, an uploads tree and a database
directory. Git and every file edit are real; the concierge toolkit's calls
to ``soliplex-cli``, the backend, the ingester and Gitea are replaced.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import datetime
import fcntl
import json
import pathlib
import subprocess
import threading

import pytest
import yaml
from mako import template as mako_template
from soliplex import authz
from soliplex import token_mint
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import ingester
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import landing_clone
from soliplex_concierge.provisioner import room_ids
from soliplex_concierge.provisioner import soliplex_api
from soliplex_concierge.provisioner import soliplex_cli
from soliplex_concierge.tools import gitea as concierge_gitea

from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import rendering

REPO = pathlib.Path(__file__).resolve().parents[3]
NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.UTC)
CLAIMS = {
    "preferred_username": "alice",
    "name": "Alice Example",
    "email": "alice@example.com",
}
ROOM = "alice:q3-plans"
SECRET = "test" * 8
ISSUE = concierge_gitea.CreatedGiteaIssue(
    number=7,
    url="https://gitea.example.com/ops/requests/issues/7",
    title="New room request: Q3 Plans",
    profile="attached",
)


def _request(**changes):
    owner = room_ids.owner_from_claims(CLAIMS)
    fields = {
        "requester_claims": CLAIMS,
        "owner": owner,
        "room_id": room_ids.make_room_id(owner, "q3-plans"),
        "name": "Q3 Plans",
        "description": "Plans for Q3",
        "requested_at": NOW,
    }
    fields.update(changes)
    return interfaces.RoomRequest(**fields)


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
def stack(tmp_path, git_config):
    """The stack's paths: environment, clone, ingester config, db, uploads."""
    environment = tmp_path / "environment"
    environment.mkdir()
    clone = tmp_path / "src" / "rooms-provisioned"
    clone.mkdir(parents=True)  # as the store's 'prepare' leaves it
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
    return {
        "environment": environment,
        "clone": clone,
        "ingester_config": ingester_config,
        "db_dir": tmp_path / "db",
        "uploads": tmp_path / "uploads" / "rooms",
    }


@pytest.fixture
def gitea_settings(tmp_path):
    """Gitea settings whose rooms 'origin' is a local bare repository."""
    host = tmp_path / "gitea"
    origin = host / "ops" / "rooms.git"
    origin.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(origin)], check=True
    )
    return adapters.GiteaSettings(
        host=str(host),
        token="gitea-token",
        owner="ops",
        repo="requests",
        rooms_repo="rooms",
    )


def _store(stack, gitea=None):
    return adapters.GitRoomStore(
        clone=stack["clone"],
        environment=stack["environment"],
        identity_claim="preferred_username",
        gitea=gitea,
    )


def _registrar(stack):
    return adapters.HaikuRagRegistrar(
        clone=stack["clone"],
        db_dir=stack["db_dir"],
        ingester_config=stack["ingester_config"],
        ingester_config_dir="/app",
        ingester_fragments_dir="/app/rooms-provisioned/databases.d",
        ingester_url="http://haiku-ingester:8765",
        ingester_token="bearer",
    )


def _applied(*rooms):
    """An ingester reload report adding ``rooms``."""
    return {
        "outcome": "applied",
        "databases": [
            {"database": room, "change": "added", "error": None}
            for room in rooms
        ],
    }


class _Reloads:
    """Stands in for ``ingester.reload``: answers each call in turn.

    An exception among ``answers`` is raised instead; the last answer
    repeats.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, token):
        self.calls.append((url, token))
        answer = (
            self.answers[0]
            if len(self.answers) == 1
            else (self.answers.pop(0))
        )
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def reloads(monkeypatch):
    fake = _Reloads(_applied(ROOM))
    monkeypatch.setattr(adapters.ingester, "reload", fake)
    return fake


# ---- GiteaSettings ----


def test_gitea_settings_origin_url(gitea_settings):
    result = gitea_settings.origin_url

    assert result == f"{gitea_settings.host}/ops/rooms.git"


def test_gitea_settings_reference(gitea_settings):
    result = gitea_settings.reference(7)

    assert result == "ops/requests#7"


def test_gitea_settings_repr_hides_the_token(gitea_settings):
    result = repr(gitea_settings)

    assert "gitea-token" not in result


def test_gitea_settings_defaults():
    result = adapters.GiteaSettings(
        host="https://gitea.example.com/", token="t"
    )

    assert result.origin_url == (
        "https://gitea.example.com/soliplex-admin/rooms-provisioned.git"
    )
    assert result.reference(3) == "soliplex-admin/soliplex-requests#3"


# ---- _author ----


@pytest.mark.parametrize(
    "claims, expected",
    [
        (CLAIMS, ("Alice Example", "alice@example.com")),
        (
            {"preferred_username": "alice", "name": "  "},
            ("alice", "alice@users.soliplex.invalid"),
        ),
        (
            {"preferred_username": "bob@example.com"},
            ("bob@example.com", "bob_example.com@users.soliplex.invalid"),
        ),
        ({}, ("unidentified", "unidentified@users.soliplex.invalid")),
    ],
    ids=["named", "blank-name", "flattened-identity", "unidentified"],
)
def test_author(claims, expected):
    result = adapters._author(claims, "preferred_username")

    assert result == expected


# ---- GitRoomStore ----


def test_store_locked_holds_the_clone_lock(stack):
    store = _store(stack)

    with store.locked():
        lock = landing_clone.lock_path(stack["clone"])
        with lock.open("a") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("with_gitea", [False, True])
def test_store_prepare(stack, gitea_settings, with_gitea):
    store = _store(stack, gitea_settings if with_gitea else None)

    store.prepare()

    assert (stack["clone"] / ".git").is_dir()
    remotes = _git(stack["clone"], "remote", "-v")
    assert (gitea_settings.origin_url in remotes) is with_gitea


@pytest.mark.parametrize("exists", [True, False])
def test_store_has_room(stack, exists):
    if exists:
        (stack["clone"] / ROOM).mkdir()

    result = _store(stack).has_room(_request())

    assert result is exists


def test_store_clashes(stack):
    result = _store(stack).clashes(_request())

    assert result == []


@pytest.mark.parametrize("issue", [ISSUE, None], ids=["tracked", "untracked"])
def test_store_add_room(stack, issue):
    store = _store(stack)
    store.prepare()

    undo = store.add_room(_request(), issue)

    room_dir = stack["clone"] / ROOM
    config = yaml.safe_load((room_dir / "room_config.yaml").read_text())
    assert config["id"] == ROOM
    record = (room_dir / rendering.RECORD_FILE).read_text()
    assert "- **Requested by:** Alice Example (`alice`)\n" in record
    assert ("Tracking issue" in record) is (issue is not None)
    assert undo.description == "remove the room directory"


def test_store_add_room_undo(stack):
    store = _store(stack)
    store.prepare()
    undo = store.add_room(_request(), None)

    undo.run()

    assert not (stack["clone"] / ROOM).exists()


def test_store_commit(stack, gitea_settings):
    store = _store(stack, gitea_settings)
    store.prepare()
    store.add_room(_request(), ISSUE)
    fragment = stack["clone"] / rendering.FRAGMENTS_DIR / f"{ROOM}.yaml"
    fragment.parent.mkdir()
    fragment.write_text("ingester: {}\n")

    result = store.commit(_request(), ISSUE)

    assert result == _git(stack["clone"], "rev-parse", "HEAD")
    files = _git(stack["clone"], "show", "--name-only", "--format=", "HEAD")
    assert f"{rendering.FRAGMENTS_DIR}/{ROOM}.yaml" in files.splitlines()
    log = _git(stack["clone"], "log", "-1", "--format=%an <%ae>%n%B")
    assert log.startswith("Alice Example <alice@example.com>\n")
    assert "Tracking issue: ops/requests#7" in log


def test_store_commit_without_a_fragment_or_gitea(stack):
    store = _store(stack)
    store.prepare()
    store.add_room(_request(), None)

    result = store.commit(_request(), None)

    files = _git(stack["clone"], "show", "--name-only", "--format=", result)
    assert all(name.startswith(f"{ROOM}/") for name in files.splitlines())
    assert "Tracking issue" not in _git(stack["clone"], "log", "-1")


def test_store_publish_without_gitea(stack):
    result = _store(stack).publish()

    assert result == []


def test_store_publish(stack, gitea_settings, monkeypatch):
    pushes = []
    monkeypatch.setattr(
        adapters.landing_clone,
        "push",
        lambda clone, *, token: pushes.append((clone, token)),
    )
    store = _store(stack, gitea_settings)

    result = store.publish()

    assert result == []
    assert pushes == [(stack["clone"], "gitea-token")]


def test_store_publish_fails(stack, gitea_settings, monkeypatch):
    def push(clone, *, token):
        stderr = "remote: denied"
        raise landing_clone.PushFailed(stderr)

    monkeypatch.setattr(adapters.landing_clone, "push", push)
    store = _store(stack, gitea_settings)

    result = store.publish()

    [step] = result
    assert step.startswith("push the provisioned-rooms clone (")
    assert "remote: denied" in step


# ---- HaikuRagRegistrar ----


def _fragment(stack, room=ROOM):
    return stack["clone"] / rendering.FRAGMENTS_DIR / f"{room}.yaml"


def test_registrar_no_clashes(stack):
    result = _registrar(stack).clashes(_request())

    assert result == []


def _db_dir_exists(stack):
    (stack["db_dir"] / f"{ROOM}.lancedb").mkdir()
    return "database directory"


def _fragment_exists(stack):
    _fragment(stack).parent.mkdir(parents=True)
    _fragment(stack).write_text("{}\n")
    return "ingester fragment"


def _in_a_fragment(stack):
    other = _fragment(stack, "other")
    other.parent.mkdir(parents=True)
    other.write_text(
        f'ingester:\n  databases:\n    "{ROOM}": {{sources: []}}\n'
        f'lancedb:\n  databases:\n    "{ROOM}": "/data/{ROOM}.lancedb"\n'
    )
    return "ingester config entry 'ingester.databases."


def _placed_in_a_fragment(stack):
    other = _fragment(stack, "other")
    other.parent.mkdir(parents=True)
    other.write_text(
        f'lancedb:\n  databases:\n    "{ROOM}": "/data/{ROOM}.lancedb"\n'
    )
    return "ingester config entry 'lancedb.databases."


def _another_name_at_its_location(stack):
    other = _fragment(stack, "other")
    other.parent.mkdir(parents=True)
    other.write_text(
        "lancedb:\n  databases:\n"
        f'    "squatter": "/data/./{ROOM}.lancedb"\n'
        '    "elsewhere": "/data/elsewhere.lancedb"\n'
        '    "unplaced": {"uri": "s3://x"}\n'
    )
    return "'lancedb.databases.squatter' placed at"


@pytest.mark.parametrize(
    "arrange",
    [
        _db_dir_exists,
        _fragment_exists,
        _in_a_fragment,
        _placed_in_a_fragment,
        _another_name_at_its_location,
    ],
)
def test_registrar_clashes(stack, arrange):
    expected = arrange(stack)

    result = _registrar(stack).clashes(_request())

    assert any(expected in found for found in result), result


def test_registrar_refuses_an_unusable_config(stack):
    stack["ingester_config"].write_text("ingester: {}\n")

    with pytest.raises(ingester.UnusableConfig):
        _registrar(stack).clashes(_request())


def test_registrar_register(stack, reloads):
    registrar = _registrar(stack)

    undo = registrar.register(_request())

    tree = yaml.safe_load(_fragment(stack).read_text())
    assert ROOM in tree["ingester"]["databases"]
    assert reloads.calls == [("http://haiku-ingester:8765", "bearer")]
    assert undo.description == "remove the room's ingester fragment"


@pytest.mark.parametrize(
    "predates", [False, True], ids=["db-created", "db-predates"]
)
def test_registrar_undo(stack, reloads, predates):
    database = stack["db_dir"] / f"{ROOM}.lancedb"
    if predates:
        database.mkdir()
    undo = _registrar(stack).register(_request())
    database.mkdir(exist_ok=True)

    undo.run()

    assert not _fragment(stack).exists()
    assert len(reloads.calls) == 2
    assert database.exists() is predates


@pytest.mark.parametrize(
    "answer, message",
    [
        (_applied("someone:else"), "did not add database"),
        ("not a report", "did not add database"),
        (
            ingester.ReloadRefused(422, "bad fragment"),
            "refused the reload",
        ),
        (
            ingester.ReloadTimedOut("http://haiku-ingester:8765", 120),
            "the timed-out reload may still apply",
        ),
    ],
    ids=["not-added", "no-report", "refused", "timed-out"],
)
def test_registrar_register_fails(stack, monkeypatch, answer, message):
    reloads = _Reloads(answer, _applied())
    monkeypatch.setattr(adapters.ingester, "reload", reloads)

    with pytest.raises(adapters.RegistrationFailed) as caught:
        _registrar(stack).register(_request())

    assert message in str(caught.value)
    assert caught.value.undo_error is None
    assert not _fragment(stack).exists()
    assert len(reloads.calls) == 2


def test_registrar_register_fails_to_undo(stack, monkeypatch):
    down = ingester.IngesterUnreachable("http://haiku-ingester:8765", "down")
    monkeypatch.setattr(adapters.ingester, "reload", _Reloads(down))

    with pytest.raises(adapters.RegistrationFailed) as caught:
        _registrar(stack).register(_request())

    assert caught.value.undo_error is down
    assert "removing its ingester fragment failed too" in str(caught.value)


# ---- LocalUploads ----


@pytest.mark.parametrize(
    "files, expected",
    [(None, []), ([], []), (["a.pdf"], ["non-empty uploads directory"])],
    ids=["missing", "empty", "non-empty"],
)
def test_uploads_clashes(stack, files, expected):
    uploads = stack["uploads"] / ROOM
    if files is not None:
        uploads.mkdir(parents=True)
        for name in files:
            (uploads / name).write_text("x")

    result = adapters.LocalUploads(stack["uploads"]).clashes(_request())

    assert [found.split(" /")[0] for found in result] == expected


def test_uploads_create(stack):
    result = adapters.LocalUploads(stack["uploads"]).create(_request())

    assert (stack["uploads"] / ROOM).is_dir()
    assert result.description == "remove the uploads directory"


def test_uploads_create_existing(stack):
    (stack["uploads"] / ROOM).mkdir(parents=True)

    result = adapters.LocalUploads(stack["uploads"]).create(_request())

    assert result is None


@pytest.mark.parametrize("uploaded", [False, True], ids=["empty", "uploaded"])
def test_uploads_undo(stack, uploaded):
    undo = adapters.LocalUploads(stack["uploads"]).create(_request())
    if uploaded:
        (stack["uploads"] / ROOM / "a.pdf").write_text("x")

    undo.run()

    assert (stack["uploads"] / ROOM).exists() is uploaded


# ---- SoliplexCliAuthz ----


class _Cli:
    """Records the toolkit's soliplex-cli calls the authz adapter makes."""

    def __init__(self, monkeypatch, stale=()):
        self.calls = []
        self.stale = set(stale)
        for name in ("set_policy", "remove_policy", "audit_rooms"):
            monkeypatch.setattr(
                adapters.soliplex_cli, name, self._recorder(name)
            )
        monkeypatch.setattr(
            adapters.soliplex_cli, "stale_policy_ids", self._stale
        )

    def _recorder(self, name):
        def record(*args):
            self.calls.append((name, *args))

        return record

    def _stale(self, cli, environment):
        self.calls.append(("stale_policy_ids", cli, environment))
        return self.stale


def _authz(stack):
    return adapters.SoliplexCliAuthz(
        cli="soliplex-cli",
        environment=pathlib.Path("/environment"),
    )


@pytest.mark.parametrize(
    "stale, expected",
    [((), []), ({ROOM}, [f"an authz policy for {ROOM!r}"])],
    ids=["none", "stale"],
)
def test_authz_clashes(stack, monkeypatch, stale, expected):
    cli = _Cli(monkeypatch, stale)

    result = _authz(stack).clashes(_request())

    assert result == expected
    assert cli.calls == [("stale_policy_ids", "soliplex-cli", "/environment")]


def test_authz_grant_owner(stack, monkeypatch):
    cli = _Cli(monkeypatch)
    foremen = room_ids.named_owner(
        "foremen", access_rules.predicate_rule("is_foreman")
    )
    request = _request(
        owner=foremen, room_id=room_ids.RoomId("foremen", "q3-plans")
    )

    undo = _authz(stack).grant_owner(request)

    [(name, cli_path, environment, room, policy)] = cli.calls
    assert (name, cli_path, environment, room) == (
        "set_policy",
        "soliplex-cli",
        "/environment",
        "foremen:q3-plans",
    )
    assert json.loads(policy) == json.loads(
        soliplex_cli.owner_policy("foremen:q3-plans", "$[?is_foreman($)]")
    )
    assert undo.description == "remove the room's authz policy"


def test_authz_grant_owner_undo(stack, monkeypatch):
    cli = _Cli(monkeypatch)
    undo = _authz(stack).grant_owner(_request())

    undo.run()

    assert cli.calls[-1] == (
        "remove_policy",
        "soliplex-cli",
        "/environment",
        ROOM,
    )


def test_authz_audit(stack, monkeypatch):
    cli = _Cli(monkeypatch)

    result = _authz(stack).audit()

    assert result is None
    assert cli.calls == [("audit_rooms", "soliplex-cli", "/environment")]


@pytest.fixture
def jsonpath_registry(monkeypatch):
    """Soliplex's JSONPath function registry, restored after the test."""
    environment = authz.the_jsonpath_environment
    monkeypatch.setattr(
        environment,
        "function_extensions",
        dict(environment.function_extensions),
    )
    return environment.function_extensions


def test_authz_predicates(stack, jsonpath_registry):
    (stack["environment"] / "installation.yaml").write_text(
        'id: "test"\n'
        "meta:\n"
        "  jsonpath_functions:\n"
        '    - {name: "is_foreman", func: "json.dumps"}\n'
    )
    authz_adapter = adapters.SoliplexCliAuthz(
        cli="soliplex-cli", environment=stack["environment"]
    )

    result = authz_adapter.predicates()

    assert result == {"is_foreman"}
    assert "is_foreman" in jsonpath_registry


# ---- SoliplexBackend ----


class _Api:
    """Records the backend API calls; ``errors`` maps a call to its error."""

    def __init__(self, monkeypatch, *, delta=None, errors=None):
        self.calls = []
        self.delta = delta if delta is not None else {"added": [ROOM]}
        self.errors = errors or {}
        monkeypatch.setattr(adapters.soliplex_api, "check_access", self._check)
        monkeypatch.setattr(
            adapters.soliplex_api, "reload_rooms", self._reload
        )

    def _call(self, name, base_url, token):
        self.calls.append((name, base_url, token))
        if name in self.errors:
            raise self.errors[name]

    def _check(self, base_url, *, token=None):
        self._call("check_access", base_url, token)

    def _reload(self, base_url, *, token=None):
        self._call("reload_rooms", base_url, token)
        return self.delta


@pytest.fixture
def secret_file(tmp_path):
    path = tmp_path / "url_safe_token_secret"
    path.write_text(f"{SECRET}\n")
    return path


def _backend(stack, secret_file):
    return adapters.SoliplexBackend(
        base_url="http://backend:8000",
        secret_file=secret_file,
        clone=stack["clone"],
    )


def test_backend_check_access_mints_a_token(stack, secret_file, monkeypatch):
    api = _Api(monkeypatch)

    result = _backend(stack, secret_file).check_access()

    assert result is None
    [(name, base_url, token)] = api.calls
    assert (name, base_url) == ("check_access", "http://backend:8000")
    claims, reason = token_mint.load_service_token(SECRET, token)
    assert reason is None
    assert claims["sub"] == adapters.DEFAULT_SERVICE_SUBJECT


def test_backend_check_access_without_a_secret(stack, tmp_path, monkeypatch):
    api = _Api(monkeypatch)

    result = _backend(stack, tmp_path / "missing").check_access()

    assert result is None
    assert api.calls == [("check_access", "http://backend:8000", None)]


@pytest.mark.parametrize("minted", [True, False])
def test_backend_check_access_denied(
    stack, secret_file, tmp_path, monkeypatch, minted
):
    denied = soliplex_api.AccessDenied("http://backend:8000/api/x", 403)
    _Api(monkeypatch, errors={"check_access": denied})
    secret = secret_file if minted else tmp_path / "missing"

    with pytest.raises(adapters.AdminAccessDenied) as caught:
        _backend(stack, secret).check_access()

    assert isinstance(caught.value, interfaces.AccessRefused)
    assert caught.value.code == 403
    assert "stack-init" in str(caught.value)
    assert """'$[?$.sub == "soliplex-provisioner"]'""" in str(caught.value)
    assert (caught.value.mint_error is None) is minted
    assert ("minting its service token failed" in str(caught.value)) is (
        not minted
    )


def test_backend_check_access_unreachable(stack, secret_file, monkeypatch):
    failed = soliplex_api.RequestFailed("http://backend:8000/api/x", "down")
    _Api(monkeypatch, errors={"check_access": failed})

    with pytest.raises(soliplex_api.RequestFailed):
        _backend(stack, secret_file).check_access()


@pytest.mark.parametrize(
    "delta, errors, expected",
    [
        ({"added": [ROOM]}, None, []),
        ({"changed": [ROOM]}, None, []),
        ({"added": ["x:y"]}, None, ["make the backend load room"]),
        (
            {},
            {"reload_rooms": soliplex_api.RequestFailed("u", "HTTP 500: x")},
            ["reload the backend's rooms: POST /api/v1/installation/"],
        ),
    ],
    ids=["added", "changed", "not-loaded", "failed"],
)
def test_backend_reload_rooms(
    stack, secret_file, monkeypatch, delta, errors, expected
):
    _Api(monkeypatch, delta=delta, errors=errors)

    result = _backend(stack, secret_file).reload_rooms(_request())

    assert len(result) == len(expected)
    for step, start in zip(result, expected, strict=True):
        assert step.startswith(start)


# ---- ConciergeGiteaTracker ----


class _Gitea:
    """Records the concierge's Gitea calls."""

    def __init__(self, monkeypatch, *, errors=None):
        self.calls = []
        self.errors = errors or {}
        monkeypatch.setattr(
            adapters.concierge_gitea, "create_gitea_issue_sync", self._create
        )
        monkeypatch.setattr(
            adapters.gitea_admin, "comment_issue", self._recorder("comment")
        )
        monkeypatch.setattr(
            adapters.gitea_admin, "close_issue", self._recorder("close")
        )

    def _maybe_raise(self, name):
        if name in self.errors:
            raise self.errors[name]

    def _create(self, **kwargs):
        self.calls.append(("create", kwargs))
        self._maybe_raise("create")
        return ISSUE

    def _recorder(self, name):
        def record(*args):
            self.calls.append((name, *args))
            self._maybe_raise(name)

        return record


def _tracker(gitea_settings):
    return adapters.ConciergeGiteaTracker(
        settings=gitea_settings, identity_claim="preferred_username"
    )


def test_tracker_file(gitea_settings, monkeypatch):
    calls = _Gitea(monkeypatch).calls

    result = _tracker(gitea_settings).file(_request())

    assert result is ISSUE
    [(name, kwargs)] = calls
    assert name == "create"
    assert kwargs["host"] == gitea_settings.host
    assert (kwargs["owner"], kwargs["repo"]) == ("ops", "requests")
    assert kwargs["user"] == CLAIMS
    assert kwargs["title"] == "New room request: Q3 Plans"
    assert "- **Room id:** `alice:q3-plans`" in kwargs["body"]
    assert kwargs["request_type"] == "new-room"


def test_tracker_file_fails(gitea_settings, monkeypatch):
    _Gitea(monkeypatch, errors={"create": RuntimeError("Gitea is down")})

    with pytest.raises(adapters.IssueFilingFailed) as caught:
        _tracker(gitea_settings).file(_request())

    assert "Gitea is down" in str(caught.value)


def test_tracker_report_failure(gitea_settings, monkeypatch):
    calls = _Gitea(monkeypatch).calls

    _tracker(gitea_settings).report_failure(ISSUE, RuntimeError("no db"))

    assert calls == [
        (
            "comment",
            gitea_settings.host,
            "gitea-token",
            "ops",
            "requests",
            7,
            "no db",
        )
    ]


def test_tracker_resolve(gitea_settings, monkeypatch):
    calls = _Gitea(monkeypatch).calls
    provisioned = interfaces.Provisioned(request=_request(), commit="c0ffee")

    result = _tracker(gitea_settings).resolve(ISSUE, provisioned)

    assert result == []
    assert [call[0] for call in calls] == ["comment", "close"]
    assert calls[0][-1] == "Provisioned room `alice:q3-plans`."


def test_tracker_resolve_fails(gitea_settings, monkeypatch):
    _Gitea(monkeypatch, errors={"close": RuntimeError("Gitea is down")})
    provisioned = interfaces.Provisioned(request=_request(), commit="c0ffee")

    result = _tracker(gitea_settings).resolve(ISSUE, provisioned)

    [step] = result
    assert step.startswith(f"comment on and close {ISSUE.url} (")


# ---- TemplateAdmission ----


def _admission(secret_file):
    return adapters.TemplateAdmission(
        secret_file=secret_file,
        cli="soliplex-cli",
        environment=pathlib.Path("/environment"),
    )


def test_admission_claims(secret_file):
    token = token_mint.mint_service_token(SECRET, CLAIMS, expires_in=60)

    result = _admission(secret_file).claims(token)

    assert result["preferred_username"] == "alice"


def test_admission_refuses_an_invalid_token(secret_file):
    with pytest.raises(interfaces.InvalidToken):
        _admission(secret_file).claims("not-a-token")


def test_admission_claims_without_the_secret(tmp_path):
    with pytest.raises(soliplex_api.SecretUnreadable):
        _admission(tmp_path / "missing").claims("token")


class _Checks:
    """Answers the toolkit's admission checks; both must start before
    either answers, so they run concurrently."""

    def __init__(self, monkeypatch, *, admin, writer):
        self.admin, self.writer = admin, writer
        self.calls = []
        self.barrier = threading.Barrier(2, timeout=5)
        monkeypatch.setattr(adapters.soliplex_cli, "is_admin", self._is_admin)
        monkeypatch.setattr(adapters.soliplex_cli, "may", self._may)

    def _is_admin(self, cli, environment, token):
        self.calls.append(("is_admin", cli, environment, token))
        self.barrier.wait()
        return self.admin

    def _may(self, cli, environment, token, room_id, permission):
        self.calls.append(("may", room_id, permission))
        self.barrier.wait()
        return self.writer


@pytest.mark.parametrize(
    "admin, writer, expected",
    [(True, False, True), (False, True, True), (False, False, False)],
    ids=["admin", "writer", "neither"],
)
def test_admission_is_admitted(
    secret_file, monkeypatch, admin, writer, expected
):
    checks = _Checks(monkeypatch, admin=admin, writer=writer)

    result = _admission(secret_file).is_admitted("t0ken")

    assert result is expected
    assert ("may", "__provision__", "write") in checks.calls
    assert ("is_admin", "soliplex-cli", "/environment", "t0ken") in (
        checks.calls
    )


def test_admission_is_admin(secret_file, monkeypatch):
    monkeypatch.setattr(
        adapters.soliplex_cli,
        "is_admin",
        lambda cli, environment, token: token == "admin-token",
    )

    result = _admission(secret_file).is_admin("admin-token")

    assert result is True
