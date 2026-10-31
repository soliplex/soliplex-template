"""Unit tests for ``soliplex_template.provision_service``.

``soliplex-cli`` is replaced by :class:`_FakeCli`, and the provisioner's
admission and creation by fakes recording their calls; the room planning
is the real one. ``TestClient`` runs a response's background task before
returning it.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import types

import pytest
from fastapi import testclient

from soliplex_template import provision
from soliplex_template import provision_service

ENVIRONMENT = "/environment"
ALICE = {"preferred_username": "alice", "sub": "alice-sub"}
BOB = {"preferred_username": "bob", "sub": "bob-sub"}
ROOM = "alice:q3-plans"
ISSUE_URL = "https://gitea.example.com/ops/requests/issues/7"


class _FakeCli:
    """Answers ``soliplex-cli`` checks.

    ``tokens`` maps the valid tokens to their user's claims (the fake
    ``token_mint`` validates with it); ``admins`` and ``writers`` hold the
    tokens that are admins and that hold WRITE on ``__provision__``;
    ``verdicts`` overrides a check's output by its command's first word.
    """

    def __init__(self, monkeypatch, token_mint):
        self.tokens = token_mint.tokens
        self.tokens.update({"alice-token": ALICE, "bob-token": BOB})
        self.admins = set()
        self.writers = set()
        self.verdicts = {}
        self.calls = []
        # When set, each admission check waits here for the other.
        self.barrier = None
        monkeypatch.setattr(provision, "run_cli", self)

    def __call__(self, settings, *command, stdin=None, ok_codes=(0,)):
        self.calls.append((command, stdin, ok_codes))
        if self.barrier is not None:
            self.barrier.wait()
        if command[0] in self.verdicts:
            return self.verdicts[command[0]]
        if command[0] == "admin-users":
            return json.dumps({"is_admin": stdin in self.admins})
        return json.dumps({"allowed": stdin in self.writers})

    def commands(self):
        return [command for command, _stdin, _codes in self.calls]


class _FakeProvisioner:
    """Stands in for ``admit_room`` / ``create_room``, recording calls."""

    def __init__(self, monkeypatch):
        self.admitted = []
        self.created = []
        self.admit_error = None
        self.create_error = None
        self.incomplete = ()
        self.issue = types.SimpleNamespace(number=7, url=ISSUE_URL)
        monkeypatch.setattr(provision, "admit_room", self.admit)
        monkeypatch.setattr(provision, "create_room", self.create)

    def admit(self, plan, settings):
        self.admitted.append(plan)
        if self.admit_error is not None:
            raise self.admit_error
        return provision.Admission(plan=plan, issue=self.issue)

    def create(self, admission, settings):
        self.created.append(admission)
        if self.create_error is not None:
            raise self.create_error
        return provision.Provisioned(
            room_id=admission.plan.room,
            room_dir=pathlib.Path("/app/src/rooms-provisioned", ROOM),
            commit="c0ffee",
            issue_url=ISSUE_URL,
            incomplete=self.incomplete,
        )


@pytest.fixture
def settings(secret_file):
    return provision.Settings(
        ingester_token="bearer",
        environment=pathlib.Path(ENVIRONMENT),
        service_token_secret_file=secret_file,
    )


@pytest.fixture
def app(settings):
    return provision_service.create_app(settings)


@pytest.fixture
def client(app):
    return testclient.TestClient(app)


@pytest.fixture
def cli(monkeypatch, token_mint):
    return _FakeCli(monkeypatch, token_mint)


@pytest.fixture
def provisioner(monkeypatch):
    return _FakeProvisioner(monkeypatch)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _post(client, token="alice-token", **body):
    body.setdefault("name", "Q3 Plans")
    return client.post("/rooms", json=body, headers=_auth(token))


# ---- POST /rooms ----


def test_post_room_as_admin(app, client, cli, provisioner, token_mint):
    cli.admins.add("alice-token")

    response = _post(client, description="Plans for Q3")

    assert response.status_code == 202
    assert response.json() == {"room_id": ROOM, "issue_url": ISSUE_URL}
    [plan] = provisioner.admitted
    assert plan.claims == ALICE
    assert plan.request.description == "Plans for Q3"
    [admission] = provisioner.created
    assert admission.plan is plan
    assert token_mint.loaded == [("s3cret", "alice-token")]
    assert {command[0] for command in cli.commands()} == {
        "admin-users",
        "room-authz",
    }
    assert all(stdin == "alice-token" for _c, stdin, _o in cli.calls)
    request = app.state.requests.get(ROOM)
    assert request.state == provision_service.State.LIVE
    assert request.issue_url == ISSUE_URL


def test_post_room_with_write_on_the_admission_room(client, cli, provisioner):
    cli.writers.add("alice-token")

    response = _post(client, slug="plans")

    assert response.status_code == 202
    assert response.json()["room_id"] == "alice:plans"
    calls = {command[0]: (command, codes) for command, _s, codes in cli.calls}
    admin_check, admin_codes = calls["admin-users"]
    room_check, ok_codes = calls["room-authz"]
    assert admin_check == ("admin-users", "--quiet", "check", ENVIRONMENT)
    assert admin_codes == (0, 1)
    assert room_check == (
        "room-authz",
        "--quiet",
        "check",
        ENVIRONMENT,
        "__provision__",
        "--permission",
        "write",
    )
    assert ok_codes == (0, 1)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic YWxpY2U6cHc="},
        {"Authorization": "Bearer  "},
    ],
)
def test_post_room_without_a_bearer_token(client, cli, provisioner, headers):
    response = client.post("/rooms", json={"name": "Q3"}, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert cli.calls == []


def test_post_room_with_an_invalid_token(client, cli, provisioner):
    response = _post(client, token="forged")

    assert response.status_code == 401
    assert response.json() == {"detail": "the service token is not valid"}
    assert provisioner.admitted == []


def test_post_room_not_admitted(client, cli, provisioner):
    response = _post(client)

    assert response.status_code == 403
    assert provisioner.admitted == []


def test_post_room_admission_check_without_a_verdict(client, cli, provisioner):
    cli.verdicts["room-authz"] = "Stale ACL entry\n"

    response = _post(client)

    assert response.status_code == 500
    assert "gave no verdict" in response.json()["detail"]
    assert provisioner.admitted == []


def test_post_room_bad_slug(client, cli, provisioner):
    cli.admins.add("alice-token")

    response = _post(client, slug="no/slashes")

    assert response.status_code == 400
    assert provisioner.admitted == []


def test_post_room_already_pending(app, client, cli, provisioner):
    cli.admins.add("alice-token")
    app.state.requests.reserve(ROOM, "alice")

    response = _post(client)

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"room {ROOM!r} is already being provisioned",
        "room_id": ROOM,
    }
    assert provisioner.admitted == []


@pytest.mark.parametrize(
    "error, status, named",
    [
        (provision.RoomExists(ROOM), 409, True),
        (provision.ResourcesExist(ROOM, ["an authz policy"]), 409, True),
        (provision.IssueFilingFailed(RuntimeError("gitea down")), 500, False),
    ],
)
def test_post_room_not_admitted_by_the_provisioner(
    app, client, cli, provisioner, error, status, named
):
    cli.admins.add("alice-token")
    provisioner.admit_error = error

    response = _post(client)

    assert response.status_code == status
    expected = {"detail": str(error)}
    if named:
        expected["room_id"] = ROOM
    assert response.json() == expected
    assert app.state.requests.get(ROOM) is None
    assert provisioner.created == []


def test_post_room_creation_fails(app, client, cli, provisioner):
    cli.admins.add("alice-token")
    provisioner.create_error = provision.ProvisioningFailed(
        ROOM, RuntimeError("audit failed")
    )

    response = _post(client)

    assert response.status_code == 202
    request = app.state.requests.get(ROOM)
    assert request.state == provision_service.State.FAILED
    assert request.error == str(provisioner.create_error)


def test_post_room_creation_incomplete(app, client, cli, provisioner):
    cli.admins.add("alice-token")
    provisioner.incomplete = ("push the provisioned-rooms clone (down)",)

    response = _post(client)

    assert response.status_code == 202
    request = app.state.requests.get(ROOM)
    assert request.state == provision_service.State.INCOMPLETE
    assert request.incomplete == provisioner.incomplete


# ---- GET /rooms/{room_id} ----


def _pending(app):
    app.state.requests.reserve(ROOM, "alice")
    app.state.requests.update(ROOM, issue_url=ISSUE_URL)


def test_get_room_by_its_requester(app, client, cli):
    _pending(app)

    response = client.get(f"/rooms/{ROOM}", headers=_auth("alice-token"))

    assert response.status_code == 200
    assert response.json() == {
        "room_id": ROOM,
        "state": "pending",
        "issue_url": ISSUE_URL,
        "incomplete": [],
        "error": None,
    }
    assert cli.commands() == []


def test_get_room_by_an_admin(app, client, cli):
    _pending(app)
    cli.admins.add("bob-token")

    response = client.get(f"/rooms/{ROOM}", headers=_auth("bob-token"))

    assert response.status_code == 200
    assert response.json()["state"] == "pending"


@pytest.mark.parametrize("claims", [BOB, {"sub": "no-username"}])
def test_get_room_by_someone_else(app, client, cli, claims):
    _pending(app)
    cli.tokens["other-token"] = claims

    response = client.get(f"/rooms/{ROOM}", headers=_auth("other-token"))

    assert response.status_code == 404


def test_get_room_admin_check_without_a_verdict(app, client, cli):
    _pending(app)
    cli.verdicts["admin-users"] = "Stale admin entry\n"

    response = client.get(f"/rooms/{ROOM}", headers=_auth("bob-token"))

    assert response.status_code == 500


def test_get_room_unknown(client, cli):
    response = client.get(f"/rooms/{ROOM}", headers=_auth("alice-token"))

    assert response.status_code == 404


def test_get_room_with_an_invalid_token(app, client, cli):
    _pending(app)

    response = client.get(f"/rooms/{ROOM}", headers=_auth("forged"))

    assert response.status_code == 401


# ---- GET /ok ----


def test_health_check(app, client):
    app.state.access.update(True)

    response = client.get("/ok")

    assert response.status_code == 200
    assert response.text == "OK"


def test_health_check_until_the_backend_admits_the_provisioner(app, client):
    app.state.access.update(False, "the backend refused the provisioner")

    response = client.get("/ok")

    assert response.status_code == 503
    assert response.text == "the backend refused the provisioner"


def test_health_check_before_the_first_check(client):
    response = client.get("/ok")

    assert response.status_code == 503
    assert response.text == "the backend has not been checked yet"


# ---- admission ----


def test_is_admitted_runs_its_checks_concurrently(settings, cli):
    # Each check waits for the other: run one after the other, the first
    # would time out at the barrier.
    cli.barrier = threading.Barrier(2, timeout=5)
    cli.writers.add("alice-token")

    result = provision_service.is_admitted(settings, "alice-token")

    assert result is True
    assert len(cli.calls) == 2


def test_is_admitted_admits_an_admin_whatever_the_policy_check_says(
    settings, cli
):
    cli.admins.add("alice-token")
    cli.verdicts["room-authz"] = "Stale ACL entry\n"

    result = provision_service.is_admitted(settings, "alice-token")

    assert result is True


# ---- watch_access ----


def _checks(monkeypatch, *outcomes):
    """Make 'check_admin_access' raise each of ``outcomes`` in turn (None
    passes)."""
    outcomes = list(outcomes)

    def check(settings):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(provision, "check_admin_access", check)


def test_watch_access_admitted_at_once(settings, monkeypatch):
    _checks(monkeypatch, None)
    access = provision_service.Access()
    sleeps = []

    provision_service.watch_access(settings, access, sleep=sleeps.append)

    assert access.state() == (True, None)
    assert sleeps == []


def test_watch_access_until_admitted(settings, monkeypatch, caplog):
    denied = provision.AdminAccessDenied(403, "soliplex-provisioner")
    unreachable = provision.BackendRequestFailed("http://backend", "refused")
    _checks(monkeypatch, unreachable, denied, denied, None)
    access = provision_service.Access()
    sleeps = []
    states = []

    def sleep(seconds):
        sleeps.append(seconds)
        states.append(access.state())

    with caplog.at_level(logging.INFO, logger=provision_service.logger.name):
        provision_service.watch_access(settings, access, sleep=sleep)

    assert access.state() == (True, None)
    assert sleeps == [
        provision_service.BACKEND_RETRY,
        provision_service.ACCESS_RETRY,
        provision_service.ACCESS_RETRY,
    ]
    assert states == [
        (False, str(unreachable)),
        (False, str(denied)),
        (False, str(denied)),
    ]
    logged = [
        (record.levelno, record.getMessage()) for record in caplog.records
    ]
    assert logged == [
        (logging.WARNING, str(unreachable)),
        (logging.ERROR, str(denied)),
        (logging.INFO, "the backend admits the provisioner"),
    ]
    assert """'$[?$.sub == "soliplex-provisioner"]'""" in str(denied)


def test_lifespan_watches_access(settings, monkeypatch):
    watched = threading.Event()
    calls = []

    def watch(settings, access):
        calls.append((settings, access))
        watched.set()

    monkeypatch.setattr(provision_service, "watch_access", watch)
    app = provision_service.create_app(settings)

    with testclient.TestClient(app):
        assert watched.wait(timeout=5)

    assert calls == [(settings, app.state.access)]


# ---- pieces ----


def test_validate_token_refuses_an_unknown_token(settings, token_mint):
    with pytest.raises(provision_service.TokenInvalid) as caught:
        provision_service.validate_token(settings, "forged")

    assert caught.value.output == token_mint.BAD_SIGNATURE


def test_post_room_without_the_signing_secret(
    client, cli, provisioner, settings
):
    settings.service_token_secret_file.unlink()

    response = _post(client)

    assert response.status_code == 500
    assert "'url_safe_token_secret'" in response.json()["detail"]
    assert cli.calls == []


@pytest.mark.parametrize(
    "output", ["", "[]", '{"sub": "alice-sub"}', "Stale admin entry"]
)
def test_is_admin_without_a_verdict(settings, cli, output):
    cli.verdicts["admin-users"] = output

    with pytest.raises(provision_service.CheckFailed) as caught:
        provision_service.is_admin(settings, "alice-token")

    assert caught.value.command[0] == "admin-users"
    assert caught.value.output == output


def test_requests_reserve_replaces_a_finished_request():
    requests = provision_service.Requests()
    requests.reserve(ROOM, "alice")
    requests.update(ROOM, state=provision_service.State.FAILED, error="boom")

    reserved = requests.reserve(ROOM, "alice")

    assert reserved is True
    assert requests.get(ROOM) == provision_service.Request(
        room_id=ROOM, owner="alice"
    )


def test_create_app_reads_settings_from_the_environment(settings, monkeypatch):
    environ = []

    def from_env(env):
        environ.append(env)
        return settings

    monkeypatch.setattr(provision.Settings, "from_env", from_env)

    app = provision_service.create_app()

    assert app.state.settings is settings
    assert environ == [provision_service.os.environ]
