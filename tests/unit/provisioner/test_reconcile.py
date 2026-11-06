"""Unit tests for ``soliplex_template.provisioner.reconcile``.

The clone and the uploads tree are real, under ``tmp_path``; the concierge
toolkit's calls to ``soliplex-cli``, the ingester and the backend are
replaced by :class:`_Stack`, which keeps the stored policies.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import dataclasses
import fcntl
import json

import pytest
import yaml
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import landing_clone
from soliplex_concierge.provisioner import soliplex_cli

from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import reconcile
from soliplex_template.provisioner import rendering
from soliplex_template.provisioner import wiring

ROOM = "alice--q3-plans"
OTHER = "bob--notes"


def _policy(room, rule):
    return json.loads(soliplex_cli.owner_policy(room, rule))


ALICE_RULE = access_rules.claim_rule("preferred_username", "alice")


def _as_yaml(policy):
    """A policy as 'room-authz as-yaml' shows it: every discriminator, and
    a claim rule on 'preferred_username' / 'email' as that field."""
    if policy is None:
        return None
    entries = []
    for entry in policy["acl_entries"]:
        shown = {
            "allow_deny": entry["allow_deny"],
            "permission": entry["permission"],
            "everyone": False,
            "authenticated": False,
            "preferred_username": None,
            "email": None,
            "json_path": entry["json_path"],
        }
        form = rendering.owner_form(entry["json_path"])
        if form.get("claim") in ("preferred_username", "email"):
            shown[form["claim"]] = form["value"]
            shown["json_path"] = None
        entries.append(shown)
    return {
        "room_id": policy["room_id"],
        "default_allow_deny": policy["default_allow_deny"],
        "acl_entries": entries,
    }


def _lock_held(clone):
    """Does another holder have the clone's lock?"""
    with landing_clone.lock_path(clone).open("a") as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(other, fcntl.LOCK_UN)
        return False


class _Stack:
    """The stored policies, and the toolkit calls reconcile makes.

    ``policies`` maps a room id to its stored policy; ``ingester_changes``
    and ``delta`` are what the reloads report.
    """

    def __init__(self, monkeypatch, settings):
        self.settings = settings
        self.policies = {}
        self.ingester_changes = []
        self.delta = {"added": [], "removed": [], "changed": []}
        self.calls = []
        self.lock_held = []
        cli = reconcile.soliplex_cli
        monkeypatch.setattr(cli, "run", self._run)
        monkeypatch.setattr(cli, "set_policy", self._set_policy)
        monkeypatch.setattr(reconcile.ingester, "reload", self._reload)

    def _note_lock(self):
        self.lock_held.append(_lock_held(self.settings.clone))

    def _run(self, cli, *command, **kwargs):
        assert command[:2] == ("room-authz", "as-yaml"), command
        self.calls.append(("as-yaml", command[3]))
        return yaml.safe_dump(_as_yaml(self.policies.get(command[3])))

    def _set_policy(self, cli, environment, room, policy):
        self.calls.append(("set_policy", room))
        self.policies[room] = json.loads(policy)

    def _reload(self, url, token):
        self._note_lock()
        self.calls.append(("reload", url, token))
        return {"outcome": "applied", "databases": self.ingester_changes}


@pytest.fixture
def settings(tmp_path):
    clone = tmp_path / "src" / "rooms-provisioned"
    clone.mkdir(parents=True)
    return wiring.Settings(
        ingester_token="bearer",
        environment=tmp_path / "environment",
        clone=clone,
        uploads_dir=tmp_path / "uploads" / "rooms",
    )


@pytest.fixture
def stack(monkeypatch, settings):
    fake = _Stack(monkeypatch, settings)
    monkeypatch.setattr(
        adapters.SoliplexBackend,
        "reload_rooms_delta",
        lambda backend: fake.delta,
    )
    return fake


def _room(settings, room=ROOM, *, rule=ALICE_RULE, record=True, uploads=True):
    room_dir = settings.clone / room
    room_dir.mkdir()
    (room_dir / "room_config.yaml").write_text(f'id: "{room}"\n')
    if record:
        (room_dir / rendering.AUTHZ_FILE).write_text(
            rendering.authz_document(soliplex_cli.owner_policy(room, rule))
        )
    if uploads:
        (settings.uploads_dir / room).mkdir(parents=True)


# ---- provisioned_rooms ----


def test_provisioned_rooms(settings):
    _room(settings, OTHER)
    _room(settings, ROOM)
    (settings.clone / "about_soliplex").mkdir()
    (settings.clone / "about_soliplex" / "room_config.yaml").write_text("")
    (settings.clone / "__provision__").mkdir()
    (settings.clone / "carol--unconfigured").mkdir()
    (settings.clone / rendering.FRAGMENTS_DIR).mkdir()
    (settings.clone / "dave:file").write_text("")

    result = reconcile.provisioned_rooms(settings.clone)

    assert result == [ROOM, OTHER]


def test_provisioned_rooms_with_the_configured_separator(settings):
    _room(settings, "alice:q3-plans")
    _room(settings, OTHER)

    result = reconcile.provisioned_rooms(settings.clone, ":")

    assert result == ["alice:q3-plans"]


# ---- normalized_policy ----


@pytest.mark.parametrize(
    "rule",
    [
        ALICE_RULE,
        access_rules.claim_rule("email", "alice@example.com"),
        access_rules.predicate_rule("is_foreman"),
    ],
    ids=["preferred_username", "email", "predicate"],
)
def test_normalized_policy_matches_as_yaml(rule):
    policy = _policy(ROOM, rule)

    result = reconcile.normalized_policy(_as_yaml(policy))

    assert result == reconcile.normalized_policy(policy)


@pytest.mark.parametrize(
    "policy, expected",
    [
        (None, None),
        ({}, ("DENY", ())),
        (
            {
                "default_allow_deny": "ALLOW",
                "acl_entries": [
                    {"everyone": True},
                    {"authenticated": True, "permission": "WRITE"},
                    {"allow_deny": "ALLOW", "json_path": "$"},
                ],
            },
            (
                "ALLOW",
                (
                    ("DENY", "READ", ("everyone", None)),
                    ("DENY", "WRITE", ("authenticated", None)),
                    ("ALLOW", "READ", ("json_path", "$")),
                ),
            ),
        ),
    ],
    ids=["none", "empty", "kinds"],
)
def test_normalized_policy(policy, expected):
    result = reconcile.normalized_policy(policy)

    assert result == expected


# ---- stored_policy ----


@pytest.mark.parametrize(
    "output, expected",
    [("null\n", None), ("room_id: x\n", {"room_id": "x"})],
    ids=["none", "stored"],
)
def test_stored_policy(monkeypatch, output, expected):
    runs = []

    def run(cli, *command, **kwargs):
        runs.append((cli, command))
        return output

    monkeypatch.setattr(reconcile.soliplex_cli, "run", run)

    result = reconcile.stored_policy("soliplex-cli", "/environment", ROOM)

    assert result == expected
    assert runs == [
        (
            "soliplex-cli",
            ("room-authz", "as-yaml", "/environment", ROOM, "--allow-stale"),
        )
    ]


# ---- reconcile ----


def test_reconcile_with_nothing_to_change(settings, stack):
    _room(settings)
    stack.policies[ROOM] = _policy(ROOM, ALICE_RULE)

    result = reconcile.reconcile(settings)

    assert result == reconcile.Reconciled()
    assert ("set_policy", ROOM) not in stack.calls


def test_reconcile_restores_a_lost_policy_and_uploads(settings, stack):
    _room(settings, uploads=False)

    result = reconcile.reconcile(settings)

    assert result.changes == (
        f"room {ROOM!r}: applied its authz.yaml",
        f"room {ROOM!r}: created {settings.uploads_dir / ROOM}",
    )
    assert stack.policies[ROOM] == _policy(ROOM, ALICE_RULE)
    assert (settings.uploads_dir / ROOM).is_dir()


def test_reconcile_replaces_a_changed_policy(settings, stack):
    _room(settings)
    stack.policies[ROOM] = _policy(
        ROOM, access_rules.claim_rule("preferred_username", "mallory")
    )

    result = reconcile.reconcile(settings)

    assert result.changes == (f"room {ROOM!r}: applied its authz.yaml",)
    assert stack.policies[ROOM] == _policy(ROOM, ALICE_RULE)


def test_reconcile_leaves_a_room_without_records(settings, stack):
    _room(settings, record=False)
    stack.policies[ROOM] = {"room_id": ROOM, "acl_entries": []}

    result = reconcile.reconcile(settings)

    assert result.changes == ()
    [note] = result.notes
    assert note.startswith(f"room {ROOM!r} has no authz.yaml")
    assert stack.policies[ROOM] == {"room_id": ROOM, "acl_entries": []}
    assert ("as-yaml", ROOM) not in stack.calls


def test_reconcile_reads_the_clone_with_the_configured_separator(
    settings, stack
):
    settings = dataclasses.replace(settings, room_id_separator=":")
    room = "alice:q3-plans"
    _room(settings, room, uploads=False)

    result = reconcile.reconcile(settings)

    assert result.changes == (
        f"room {room!r}: applied its authz.yaml",
        f"room {room!r}: created {settings.uploads_dir / room}",
    )
    assert stack.policies[room] == _policy(room, ALICE_RULE)
    assert (settings.uploads_dir / room).is_dir()


def test_reconcile_reports_the_reloads(settings, stack):
    stack.ingester_changes = [
        {"database": OTHER, "change": "removed", "error": None}
    ]
    stack.delta = {"added": [], "removed": [OTHER], "changed": [ROOM]}

    result = reconcile.reconcile(settings)

    assert result.changes == (
        f"ingester: database {OTHER!r} removed",
        f"backend: room {OTHER!r} removed",
        f"backend: room {ROOM!r} changed",
    )
    [(_name, url, token)] = [c for c in stack.calls if c[0] == "reload"]
    assert (url, token) == (settings.ingester_url, "bearer")


def test_lock_probe_of_a_free_lock(settings):
    result = _lock_held(settings.clone)

    assert result is False


def test_reconcile_holds_the_clone_lock(settings, stack):
    result = reconcile.reconcile(settings)

    assert result == reconcile.Reconciled()
    assert stack.lock_held == [True]


def test_reconcile_refuses_a_record_that_is_not_yaml(settings, stack):
    _room(settings)
    record = settings.clone / ROOM / rendering.AUTHZ_FILE
    record.write_text("acl_entries: [\n")

    with pytest.raises(reconcile.BadRecord) as caught:
        reconcile.reconcile(settings)

    assert caught.value.path == record


def test_reconcile_refuses_an_unreadable_record(settings, stack):
    _room(settings)
    record = settings.clone / ROOM / rendering.AUTHZ_FILE
    record.chmod(0)

    with pytest.raises(reconcile.BadRecord) as caught:
        reconcile.reconcile(settings)

    assert isinstance(caught.value.error, OSError)
