"""End-to-end test of the room provisioner on a running auth-mode stack.

Generates an ``auth_mode: auth`` stack (no Gitea), builds and brings up what
the provisioner talks to (see ``provisioning_stack``), and authenticates
with *service tokens* minted by ``soliplex-cli service-token mint``: no
identity provider is needed, since soliplex validates service tokens (and
OIDC tokens, against the configured PEM) locally. An ``admin_users`` entry
admits the provisioner's token as an admin.

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
import warnings

import provisioning_stack as ps
import pytest

_PARAMS = {
    "project_name": "soliplex-provision-auth-functest",
    "ollama_base_url": "http://ollama.invalid:11434",
    "auth_mode": "auth",
}
_PROVISIONER = {"sub": "provisioner"}
_ADMIN_USERS = {"admin_users": [{"json_path": '$[?$.sub == "provisioner"]'}]}
_USERS = {
    "alice": {"sub": "alice", "preferred_username": "alice"},
    "bob": {"sub": "bob", "preferred_username": "bob"},
}
_ROOM_ID = "alice:q3-plans"


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
    """A generated auth-mode stack, up, admitting the provisioner's token.

    Yields ``(out, tokens)``: ``tokens`` maps 'provisioner' and each name in
    ``_USERS`` to a minted service token.
    """
    ps.require_tools()
    work = tmp_path_factory.mktemp("provision-auth")
    out = ps.generate(work, _PARAMS)
    try:
        ps.build_images(out, work)
        _skip_without_service_tokens(out)
        ps.up(out)
        ps.check(
            ps.soliplex_cli(
                out,
                "admin-users",
                "from-yaml",
                "/environment",
                stdin=json.dumps(_ADMIN_USERS),
            ),
            "admin-users from-yaml",
        )
        tokens = {"provisioner": _mint(out, _PROVISIONER)}
        tokens |= {name: _mint(out, claims) for name, claims in _USERS.items()}
        yield out, tokens
    finally:
        ps.compose(out, "down", "-v")


def _provision(out, name, admin_token):
    return ps.provision(
        out, "--username", "alice", "--name", name, admin_token=admin_token
    )


@pytest.fixture(scope="module")
def provisioned(auth_stack):
    """The provisioning run with the provisioner's token."""
    out, tokens = auth_stack
    return out, tokens, _provision(out, "Q3 Plans", tokens["provisioner"])


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
@pytest.mark.parametrize(
    "token_name, code, name, slug",
    [
        # no token: unauthenticated
        (None, 401, "Refused Without Token", "refused-without-token"),
        # a valid token, but not an admin's
        ("bob", 403, "Refused For Bob", "refused-for-bob"),
    ],
)
def test_provision_room_needs_an_admin_token(
    auth_stack, token_name, code, name, slug
):
    out, tokens = auth_stack
    admin_token = None if token_name is None else tokens[token_name]

    result = _provision(out, name, admin_token)

    assert result.returncode == 2, result.stdout + result.stderr
    assert f"refused the provisioner's admin access (HTTP {code})" in (
        result.stderr
    )
    assert "SOLIPLEX_ADMIN_TOKEN" in result.stderr
    assert not (out / "src" / "rooms-provisioned" / f"alice:{slug}").exists()


@pytest.mark.needs_docker
def test_provision_room_with_the_provisioner_token(provisioned):
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
