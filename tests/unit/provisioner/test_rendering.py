"""Unit tests for ``soliplex_template.provisioner.rendering``.

Requests are planned by the concierge's core, as the provisioner plans them.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.metadata

import pytest
import yaml
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import room_ids

from soliplex_template.provisioner import rendering

REQUESTED_AT = datetime.datetime(
    2026,
    10,
    28,
    10,
    15,
    30,
    123456,
    tzinfo=datetime.timezone(datetime.timedelta(hours=-4)),
)
CLAIMS = {"email": "bharney@sitename.example", "name": "Bharney Rubble"}
OWNER = room_ids.owner_from_claims(CLAIMS, claim="email", flatten_value=True)
REQUEST = interfaces.RoomRequest(
    requester_claims=CLAIMS,
    owner=OWNER,
    room_id=room_ids.RoomId(owner=OWNER.owner, slug="q3-plans"),
    name="Q3 Plans",
    description="Plans and briefs\nfor the Q3 campaign.",
    requested_at=REQUESTED_AT,
)
FOREMEN = room_ids.named_owner(
    "foremen", access_rules.predicate_rule("is_foreman")
)
ISSUE_URL = "https://gitea.example.com/ops/requests/issues/7"


def _record(request=REQUEST, **kwargs):
    kwargs.setdefault("identity_claim", "email")
    return rendering.record(request, **kwargs)


# ---- room_config ----


def test_room_config():
    result = rendering.room_config(REQUEST)

    assert result.startswith(
        "# Created by the soliplex-template provisioner.\n"
    )
    config = yaml.safe_load(result)
    assert config["id"] == "bharney_sitename.example:q3-plans"
    assert config["name"] == "Q3 Plans"
    assert config["description"] == "Plans and briefs\nfor the Q3 campaign."
    assert config["agent"] == {"template_id": rendering.AGENT_TEMPLATE}
    [rag, *_others] = config["skills"]["skill_configs"]
    assert rag["rag_databases"] == [
        {
            "name": "bharney_sitename.example:q3-plans",
            "rag_lancedb_stem": "bharney_sitename.example:q3-plans",
        }
    ]
    assert config["allow_mcp"] is False


@pytest.mark.parametrize(
    "name",
    ['Q3: "plans"\nid: hijacked', "Café ☕ 计划", "{a: b}, [c]"],
    ids=["yaml-injection", "non-ascii", "flow-syntax"],
)
def test_room_config_quotes_requester_text(name):
    request = dataclasses.replace(REQUEST, name=name, description=name)

    result = rendering.room_config(request)

    config = yaml.safe_load(result)
    assert config["id"] == "bharney_sitename.example:q3-plans"
    assert config["name"] == name
    assert config["description"] == name


# ---- issue_title ----


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Q3 Plans", "New room request: Q3 Plans"),
        ("  Q3\n\tPlans  ", "New room request: Q3 Plans"),
    ],
)
def test_issue_title(name, expected):
    request = dataclasses.replace(REQUEST, name=name)

    result = rendering.issue_title(request)

    assert result == expected


# ---- record ----


def test_record():
    result = _record(
        data_dir="/data", uploads_root="/uploads/rooms", issue_url=ISSUE_URL
    )

    assert result == (
        "# Q3 Plans\n"
        "\n"
        "- **Room id:** `bharney_sitename.example:q3-plans`\n"
        "- **Requested by:** Bharney Rubble (`bharney@sitename.example`)\n"
        "- **Requested at:** 2026-10-28T14:15:30Z\n"
        f"- **Tracking issue:** {ISSUE_URL}\n"
        "\n"
        "## Description\n"
        "\n"
        "Plans and briefs\n"
        "for the Q3 campaign.\n"
        "\n"
        "## Provisioned\n"
        "\n"
        "- **Room config:** "
        "`bharney_sitename.example:q3-plans/room_config.yaml` in the "
        "provisioned-rooms clone\n"
        "- **RAG database:** `bharney_sitename.example:q3-plans` at "
        "`/data/bharney_sitename.example:q3-plans.lancedb`, ingested from "
        "the room's uploads under "
        "`/uploads/rooms/bharney_sitename.example:q3-plans/`\n"
        "- **Access:** private; READ and WRITE granted where claim `email` "
        "is `bharney@sitename.example`\n"
    )


def test_record_for_a_named_owner():
    request = dataclasses.replace(
        REQUEST,
        owner=FOREMEN,
        room_id=room_ids.RoomId(owner="foremen", slug="q3-plans"),
    )

    result = _record(request)

    assert "- **Room id:** `foremen:q3-plans`\n" in result
    assert (
        "- **Requested by:** Bharney Rubble (`bharney@sitename.example`)\n"
    ) in result
    assert (
        "- **Access:** private; READ and WRITE granted to users the "
        "predicate `is_foreman` passes\n"
    ) in result


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({"email": "bharney@sitename.example"}, "`bharney@sitename.example`"),
        (
            {"email": "bharney@sitename.example", "name": "  "},
            "`bharney@sitename.example`",
        ),
        ({"name": "Bharney Rubble"}, "Bharney Rubble (*(unidentified)*)"),
        ({"email": ["not", "a", "string"]}, "*(unidentified)*"),
    ],
    ids=["no-name", "blank-name", "no-identity", "identity-not-a-string"],
)
def test_record_requested_by(claims, expected):
    request = dataclasses.replace(REQUEST, requester_claims=claims)

    result = _record(request)

    assert f"- **Requested by:** {expected}\n" in result
    assert "Tracking issue" not in result


@pytest.mark.parametrize("description", ["", "  \n "])
def test_record_blank_description(description):
    request = dataclasses.replace(REQUEST, description=description)

    result = _record(request)

    assert "## Description\n\n*(none given)*\n" in result


@pytest.mark.parametrize(
    "identity, expected",
    [
        ("a`b", "``a`b``"),
        ("a``b", "```a``b```"),
        ("`edge`", "`` `edge` ``"),
    ],
)
def test_record_code_spans_hold_backticks(identity, expected):
    claims = {"email": identity}
    request = dataclasses.replace(REQUEST, requester_claims=claims)

    result = _record(request)

    assert f"- **Requested by:** {expected}\n" in result


# ---- describe_rule ----


@pytest.mark.parametrize(
    "json_path, expected",
    [
        (
            access_rules.claim_rule("email", 'b"q@x.example'),
            'where claim `email` is `b"q@x.example`',
        ),
        (
            access_rules.predicate_rule("is_foreman"),
            "to users the predicate `is_foreman` passes",
        ),
        (
            "$[?'staff' in @.groups]",
            "by the access rule `$[?'staff' in @.groups]`",
        ),
        (
            '$[?$.email == "a" "b"]',
            'by the access rule `$[?$.email == "a" "b"]`',
        ),
    ],
    ids=["claim", "predicate", "other", "claim-like-but-not-json"],
)
def test_describe_rule(json_path, expected):
    result = rendering.describe_rule(json_path)

    assert result == expected


# ---- owner_form ----


@pytest.mark.parametrize(
    "json_path, expected",
    [
        (
            access_rules.claim_rule("email", 'b"q@x.example'),
            {"claim": "email", "value": 'b"q@x.example'},
        ),
        (
            access_rules.predicate_rule("is_foreman"),
            {"predicate": "is_foreman"},
        ),
        ("$[?'staff' in @.groups]", {}),
        ('$[?$.email == "a" "b"]', {}),
    ],
    ids=["claim", "predicate", "other", "claim-like-but-not-json"],
)
def test_owner_form(json_path, expected):
    result = rendering.owner_form(json_path)

    assert result == expected


# ---- provision_record ----


def _versions():
    return {
        package: importlib.metadata.version(package)
        for package in rendering.RENDERING_PACKAGES
    }


def test_provision_record():
    result = rendering.provision_record(
        REQUEST, identity_claim="email", issue_url=ISSUE_URL
    )

    assert yaml.safe_load(result) == {
        "room_id": "bharney_sitename.example:q3-plans",
        "name": "Q3 Plans",
        "description": "Plans and briefs\nfor the Q3 campaign.",
        "requested_at": "2026-10-28T14:15:30Z",
        "tracking_issue": ISSUE_URL,
        "requester": {
            "claim": "email",
            "value": "bharney@sitename.example",
            "name": "Bharney Rubble",
        },
        "owner": {
            "owner": "bharney_sitename.example",
            "claim": "email",
            "value": "bharney@sitename.example",
            "json_path": '$[?$.email == "bharney@sitename.example"]',
        },
        "versions": _versions(),
    }


def test_provision_record_keeps_its_key_order():
    result = rendering.provision_record(REQUEST, identity_claim="email")

    assert list(yaml.safe_load(result)) == [
        "room_id",
        "name",
        "description",
        "requested_at",
        "tracking_issue",
        "requester",
        "owner",
        "versions",
    ]


def test_provision_record_for_a_named_owner():
    request = dataclasses.replace(
        REQUEST,
        owner=FOREMEN,
        room_id=room_ids.RoomId(owner="foremen", slug="q3-plans"),
    )

    result = rendering.provision_record(request, identity_claim="email")

    record = yaml.safe_load(result)
    assert record["owner"] == {
        "owner": "foremen",
        "predicate": "is_foreman",
        "json_path": "$[?is_foreman($)]",
    }
    assert record["requester"]["value"] == "bharney@sitename.example"
    assert record["tracking_issue"] is None


def test_provision_record_for_an_unrecognized_rule():
    owner = room_ids.named_owner("staff", "$[?'staff' in @.groups]")
    request = dataclasses.replace(
        REQUEST,
        owner=owner,
        room_id=room_ids.RoomId(owner="staff", slug="q3-plans"),
    )

    result = rendering.provision_record(request, identity_claim="email")

    assert yaml.safe_load(result)["owner"] == {
        "owner": "staff",
        "json_path": "$[?'staff' in @.groups]",
    }


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({}, {"claim": "email", "value": None, "name": None}),
        (
            {"email": ["x"], "name": "  "},
            {"claim": "email", "value": None, "name": None},
        ),
    ],
    ids=["missing", "not-strings"],
)
def test_provision_record_requester_fallbacks(claims, expected):
    request = dataclasses.replace(REQUEST, requester_claims=claims)

    result = rendering.provision_record(request, identity_claim="email")

    assert yaml.safe_load(result)["requester"] == expected


def test_provision_record_names_an_absent_package(monkeypatch):
    def version(package):
        raise importlib.metadata.PackageNotFoundError(package)

    monkeypatch.setattr(rendering.importlib.metadata, "version", version)

    result = rendering.provision_record(REQUEST, identity_claim="email")

    assert yaml.safe_load(result)["versions"] == {
        "soliplex-template": None,
        "soliplex-concierge": None,
    }


# ---- fragment ----


def test_fragment():
    result = rendering.fragment(REQUEST)

    assert result.startswith("# Written by the soliplex-template provisioner.")
    room = "bharney_sitename.example:q3-plans"
    assert yaml.safe_load(result) == {
        "ingester": {
            "databases": {
                room: {
                    "sources": [
                        {
                            "type": "fs",
                            "id": room,
                            "root": f"/uploads/rooms/{room}",
                            "delete_orphans": True,
                        }
                    ]
                }
            }
        },
        "lancedb": {"databases": {room: f"/data/{room}.lancedb"}},
    }


def test_fragment_honors_data_dir_and_uploads_root():
    result = rendering.fragment(
        REQUEST, data_dir="/srv/db", uploads_root="/up"
    )

    tree = yaml.safe_load(result)
    room = "bharney_sitename.example:q3-plans"
    [source] = tree["ingester"]["databases"][room]["sources"]
    assert source["root"] == f"/up/{room}"
    assert tree["lancedb"]["databases"] == {room: f"/srv/db/{room}.lancedb"}


def test_fragment_name():
    result = rendering.fragment_name("alice:q3.notes")

    assert result == "alice:q3.notes.yaml"


# ---- commit_message / provisioned_comment ----


@pytest.mark.parametrize(
    "reference, expected",
    [
        (
            None,
            "Provision room bharney_sitename.example:q3-plans\n\nQ3 Plans\n",
        ),
        (
            "ops/requests#7",
            "Provision room bharney_sitename.example:q3-plans\n\nQ3 Plans\n"
            "\nTracking issue: ops/requests#7\n",
        ),
    ],
    ids=["untracked", "tracked"],
)
def test_commit_message(reference, expected):
    result = rendering.commit_message(REQUEST, tracking_reference=reference)

    assert result == expected


def test_provisioned_comment():
    provisioned = interfaces.Provisioned(request=REQUEST, commit="c0ffee")

    result = rendering.provisioned_comment(provisioned)

    assert result == "Provisioned room `bharney_sitename.example:q3-plans`."
