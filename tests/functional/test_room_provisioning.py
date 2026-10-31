"""End-to-end test of the room provisioner on a running no-auth stack.

Generates a stack (no-auth, no Gitea), builds and brings up what the
provisioner talks to (see ``provisioning_stack``), and provisions a room
through the skill's ``provision_room.py``.

Live-Docker only (``needs_docker``); skips with a warning when ``docker``,
its daemon, ``git`` or ``uv`` is unavailable. Run it with:

    uv run --group dev pytest --no-cov \\
        tests/functional/test_room_provisioning.py

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import json

import provisioning_stack as ps
import pytest
import yaml

_PARAMS = {
    "project_name": "soliplex-provision-functest",
    "ollama_base_url": "http://ollama.invalid:11434",
}
_ROOM_ARGS = ("--username", "alice", "--email", "alice@example.com")
_ROOM_ID = "alice:q3-plans"


@pytest.fixture(scope="module")
def stack(tmp_path_factory, refresh_embedded_template):
    """A generated no-auth stack with the provisioner's services up."""
    ps.require_tools()
    work = tmp_path_factory.mktemp("provision")
    out = ps.generate(work, _PARAMS)
    try:
        ps.build_images(out, work)
        ps.up(out)
        yield out
    finally:
        ps.compose(out, "down", "-v")


def _provision(out):
    return ps.provision(out, *_ROOM_ARGS, "--name", "Q3 Plans")


@pytest.fixture(scope="module")
def provisioned(stack):
    """The first provisioning run's result."""
    return stack, _provision(stack)


def _ingester_get(out, path):
    script = (
        "import sys, urllib.request; "
        "token = open('/run/secrets/ingester_bearer').read().strip(); "
        "request = urllib.request.Request("
        f"'http://localhost:8765{path}', "
        "headers={'Authorization': 'Bearer ' + token}); "
        "sys.stdout.write(urllib.request.urlopen(request).read().decode())"
    )
    return json.loads(
        ps.check(
            ps.compose(
                out, "exec", "-T", "haiku-ingester", "python", "-c", script
            ),
            f"ingester GET {path}",
        )
    )


@pytest.mark.needs_docker
def test_provision_room_live(provisioned):
    # Exit 0 with nothing left to do means every step ran, including the
    # backend's rooms reload reporting the room loaded. (The rooms listing
    # cannot show it: the room is private to 'alice', and no-auth mode's
    # user is another one.)
    out, result = provisioned
    clone = out / "src" / "rooms-provisioned"

    committed = ps.check(
        ps.run("git", "-C", clone, "show", "--name-only", "--format=%an|%s"),
        "git show",
    ).split()

    assert result.returncode == 0, result.stdout + result.stderr
    expected = (
        f"provisioned room '{_ROOM_ID}' "
        f"(commit {ps.committed_id(clone)[:12]})\n"
    )
    assert result.stdout == expected, result.stdout
    assert committed[0] == "alice|Provision"
    assert f"{_ROOM_ID}/REQUEST.md" in committed
    assert f"{_ROOM_ID}/room_config.yaml" in committed
    assert (out / "rag" / "db" / f"{_ROOM_ID}.lancedb").is_dir()
    assert (out / "uploads" / "rooms" / _ROOM_ID).is_dir()


@pytest.mark.needs_docker
def test_provision_room_policy(provisioned):
    out, _result = provisioned

    shown = ps.soliplex_cli(
        out, "room-authz", "as-yaml", "/environment", _ROOM_ID
    )

    policy = yaml.safe_load(ps.check(shown, "room-authz as-yaml"))
    assert policy["default_allow_deny"] == "DENY"
    # 'as-yaml' shows a stored '$[?$.preferred_username == ...]' query as
    # the 'preferred_username' shortcut.
    granted = {
        (
            entry["permission"],
            entry.get("preferred_username") or entry.get("json_path"),
        )
        for entry in policy["acl_entries"]
        if entry["allow_deny"] == "ALLOW"
    }
    assert granted == {("READ", "alice"), ("WRITE", "alice")}


@pytest.mark.needs_docker
def test_provision_room_ingester_source(provisioned):
    out, _result = provisioned

    sources = _ingester_get(out, "/sources")

    assert _ROOM_ID in json.dumps(sources)
    clone = out / "src" / "rooms-provisioned"
    fragment = clone / "databases.d" / f"{_ROOM_ID}.yaml"
    config = yaml.safe_load(fragment.read_text())
    assert config["lancedb"]["databases"][_ROOM_ID] == (
        f"/data/{_ROOM_ID}.lancedb"
    )
    tracked = ps.run("git", "-C", clone, "ls-files", "--", "databases.d")
    assert tracked.stdout.split() == [f"databases.d/{_ROOM_ID}.yaml"]


@pytest.mark.needs_docker
def test_provision_room_again_is_refused(provisioned):
    out, _first = provisioned
    clone = out / "src" / "rooms-provisioned"
    head = ps.committed_id(clone)

    result = _provision(out)

    assert result.returncode == 2
    assert f"room '{_ROOM_ID}' already exists" in result.stderr
    assert ps.committed_id(clone) == head


@pytest.mark.needs_docker
def test_provision_room_after_its_directory_was_removed(provisioned):
    # A room whose directory was removed by hand leaves its database, its
    # ingester fragment and its authz policy behind; provisioning the same
    # id again refuses, naming them, rather than adopting them. (Runs last:
    # it removes the room the earlier tests check.)
    out, _first = provisioned
    clone = out / "src" / "rooms-provisioned"
    ps.check(ps.run("git", "-C", clone, "rm", "-r", "-q", _ROOM_ID), "git rm")
    config_before = (out / "haiku.rag" / "haiku.rag.yaml").read_text()

    result = _provision(out)

    assert result.returncode == 2, result.stdout + result.stderr
    assert f"room '{_ROOM_ID}' was not provisioned" in result.stderr
    assert f"/db/{_ROOM_ID}.lancedb" in result.stderr
    assert f"databases.d/{_ROOM_ID}.yaml" in result.stderr
    assert f"'ingester.databases.{_ROOM_ID}'" in result.stderr
    assert f"'lancedb.databases.{_ROOM_ID}'" in result.stderr
    assert f"an authz policy for '{_ROOM_ID}'" in result.stderr
    assert not (clone / _ROOM_ID).exists()
    assert (out / "haiku.rag" / "haiku.rag.yaml").read_text() == config_before
