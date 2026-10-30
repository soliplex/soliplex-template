"""End-to-end test of the room provisioner on a running auth-mode stack.

Generates an ``auth_mode: auth`` stack (no Gitea), builds and brings up what
the provisioner talks to (see ``provisioning_stack``), and authenticates
with *service tokens* (``soliplex-cli service-token mint``): the
provisioner mints its own; the test mints the room users'. No identity
provider is needed, since soliplex validates service tokens (and OIDC
tokens, against the configured PEM) locally.

The provisioner is refused until an ``admin_users`` entry admits its
tokens' subject -- added with the command the provisioning docs give -- so
the tests run in file order: the refusal first, then the admitted run.
Last, the provisioner service provisions a room for a requester admitted
by a ``__provision__`` policy, and refuses one who is not.

Needs a soliplex with service tokens in the backend image: when the image
lacks ``soliplex-cli service-token``, the tests skip with a warning. To test
an unreleased soliplex, set ``SOLIPLEX_FUNCTEST_SOLIPLEX_REPO`` and
``SOLIPLEX_FUNCTEST_SOLIPLEX_REF`` (see ``provisioning_stack``).

Live-Docker only (``needs_docker``); skips with a warning when ``docker``,
its daemon, ``git`` or ``uv`` is unavailable. Run it with:

    uv run --group dev pytest --no-cov \\
        tests/functional/test_room_provisioning_auth.py

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import json
import time
import warnings

import provisioning_stack as ps
import pytest

_PARAMS = {
    "project_name": "soliplex-provision-auth-functest",
    "ollama_base_url": "http://ollama.invalid:11434",
    "auth_mode": "auth",
}
_PROVISIONER_ENTRY = '$[?$.sub == "soliplex-provisioner"]'
_USERS = {
    "alice": {"sub": "alice", "preferred_username": "alice"},
    "bob": {"sub": "bob", "preferred_username": "bob"},
}
_ROOM_ID = "alice:q3-plans"
_SERVICE_ROOM_ID = "alice:service-plans"
_SERVICE_TIMEOUT = 180
# Admits alice alone to request rooms through the service.
_ADMISSION_POLICY = {
    "room_id": "__provision__",
    "default_allow_deny": "DENY",
    "acl_entries": [
        {
            "allow_deny": "ALLOW",
            "permission": "WRITE",
            "json_path": '$[?$.preferred_username == "alice"]',
        }
    ],
}


def _skip_without_service_tokens(out):
    if ps.soliplex_cli(out, "service-token", "--help").returncode == 0:
        return
    warnings.warn(
        "the backend image's soliplex has no service tokens; skipping the "
        f"auth-mode provisioner test (set {ps.SOLIPLEX_REPO_ENV} and "
        f"{ps.SOLIPLEX_REF_ENV} to test an unreleased soliplex)",
        stacklevel=2,
    )
    pytest.skip("soliplex lacks service tokens")


def _mint(out, claims) -> str:
    minted = ps.soliplex_cli(
        out,
        "service-token",
        "mint",
        "/environment",
        "--claims",
        json.dumps(claims),
        "--expires-in",
        "15m",
        "--quiet",
    )
    return ps.check(minted, f"minting a token for {claims}").strip()


@pytest.fixture(scope="module")
def auth_stack(tmp_path_factory, refresh_embedded_template):
    """A generated auth-mode stack, up, with no admin users yet.

    Yields ``(out, tokens)``: ``tokens`` maps each name in ``_USERS`` to a
    minted service token.
    """
    ps.require_tools()
    work = tmp_path_factory.mktemp("provision-auth")
    out = ps.generate(work, _PARAMS)
    try:
        ps.build_images(out, work)
        _skip_without_service_tokens(out)
        ps.up(out)
        tokens = {name: _mint(out, claims) for name, claims in _USERS.items()}
        yield out, tokens
    finally:
        ps.compose(out, "down", "-v")


def _provision(out, name):
    return ps.provision(out, "--username", "alice", "--name", name)


@pytest.fixture(scope="module")
def provisioned(auth_stack):
    """The provisioning run, once an admin entry admits the provisioner."""
    out, tokens = auth_stack
    ps.check(
        ps.soliplex_cli(
            out,
            "admin-users",
            "add",
            "/environment",
            "--json-path",
            _PROVISIONER_ENTRY,
        ),
        "admin-users add",
    )
    return out, tokens, _provision(out, "Q3 Plans")


def _rooms_as(out, token) -> dict:
    script = (
        "import sys, urllib.request; "
        "request = urllib.request.Request("
        "'http://localhost:8000/api/v1/rooms', "
        f"headers={{'Authorization': 'Bearer {token}'}}); "
        "sys.stdout.write(urllib.request.urlopen(request).read().decode())"
    )
    return json.loads(
        ps.check(
            ps.compose(out, "exec", "-T", "backend", "python", "-c", script),
            "GET /v1/rooms",
        )
    )


@pytest.mark.needs_docker
def test_provision_room_refused_until_admitted(auth_stack):
    out, _tokens = auth_stack

    result = _provision(out, "Refused Before Admission")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refused the provisioner's admin access (HTTP 403)" in (
        result.stderr
    )
    assert f"'{_PROVISIONER_ENTRY}'" in result.stderr
    clone = out / "src" / "rooms-provisioned"
    assert not (clone / "alice:refused-before-admission").exists()


@pytest.mark.needs_docker
def test_provision_room_once_admitted(provisioned):
    out, _tokens, result = provisioned

    stdout = result.stdout

    assert result.returncode == 0, stdout + result.stderr
    head = ps.committed_id(out / "src" / "rooms-provisioned")
    assert stdout == f"provisioned room '{_ROOM_ID}' (commit {head[:12]})\n"


@pytest.mark.needs_docker
@pytest.mark.parametrize("user, sees_room", [("alice", True), ("bob", False)])
def test_provisioned_room_is_private_to_its_owner(
    provisioned, user, sees_room
):
    out, tokens, _result = provisioned

    rooms = _rooms_as(out, tokens[user])

    assert (_ROOM_ID in rooms) is sees_room


# ---- the provisioner service ----


@pytest.fixture(scope="module")
def service(provisioned):
    """The provisioner service, up, with alice admitted by policy."""
    out, tokens, _result = provisioned
    ps.check(
        ps.soliplex_cli(
            out,
            "room-authz",
            "from-yaml",
            "/environment",
            "__provision__",
            stdin=json.dumps(_ADMISSION_POLICY),
        ),
        "room-authz from-yaml __provision__",
    )
    ps.up_provisioner(out)
    return out, tokens


@pytest.fixture(scope="module")
def service_request(service):
    """alice's request through the service, and its state once settled."""
    out, tokens = service
    accepted = ps.call_service(
        out, "POST", "/rooms", tokens["alice"], {"name": "Service Plans"}
    )
    deadline = time.monotonic() + _SERVICE_TIMEOUT
    while True:
        settled = ps.call_service(
            out, "GET", f"/rooms/{_SERVICE_ROOM_ID}", tokens["alice"]
        )
        if settled[1].get("state") != "pending":
            break
        assert time.monotonic() < deadline, "the request stayed pending"
        time.sleep(2)
    return out, tokens, accepted, settled


@pytest.mark.needs_docker
def test_service_refuses_a_requester_not_admitted(service):
    out, tokens = service

    status, body = ps.call_service(
        out, "POST", "/rooms", tokens["bob"], {"name": "Bob Plans"}
    )

    assert status == 403, body
    assert not (out / "src" / "rooms-provisioned" / "bob:bob-plans").exists()


@pytest.mark.needs_docker
def test_service_provisions_for_an_admitted_requester(service_request):
    out, tokens, accepted, settled = service_request

    rooms = _rooms_as(out, tokens["alice"])

    assert accepted == (202, {"room_id": _SERVICE_ROOM_ID, "issue_url": None})
    assert settled == (
        200,
        {
            "room_id": _SERVICE_ROOM_ID,
            "state": "live",
            "issue_url": None,
            "incomplete": [],
            "error": None,
        },
    )
    assert _SERVICE_ROOM_ID in rooms
