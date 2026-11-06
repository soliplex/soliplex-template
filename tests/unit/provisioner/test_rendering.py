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
import json

import pytest
import yaml
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import room_ids
from soliplex_concierge.provisioner import soliplex_cli

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
BR_EMAIL = "bharney@sitename.example"
BR_NAME = "Bharney Rubble"
CLAIMS = {"email": BR_EMAIL, "name": BR_NAME}
OWNER = room_ids.owner_from_claims(CLAIMS, claim="email", flatten_value=True)
ROOM_NAME = "Q3 Plans"
ROOM_DESC = "Plans and briefs\nfor the Q3 campaign."
SLUG = "q3-plans"
REQUEST = interfaces.RoomRequest(
    requester_claims=CLAIMS,
    owner=OWNER,
    room_id=room_ids.RoomId(owner=OWNER.owner, slug=SLUG),
    name=ROOM_NAME,
    description=ROOM_DESC,
    requested_at=REQUESTED_AT,
)
ROOM_ID = room_ids.RoomId(OWNER.owner, SLUG)
ROOM_ID_STR = str(ROOM_ID)
FOREMEN = room_ids.named_owner(
    "foremen", access_rules.predicate_rule("is_foreman")
)
ISSUE_URL = "https://gitea.example.com/ops/requests/issues/7"


# ---- room_config ----


def test_room_config():
    result = rendering.room_config(REQUEST)

    assert result.startswith(
        "# Created by the soliplex-template provisioner.\n"
    )
    config = yaml.safe_load(result)
    assert config["id"] == ROOM_ID_STR
    assert config["name"] == ROOM_NAME
    assert config["description"] == ROOM_DESC
    assert config["agent"] == {"template_id": rendering.AGENT_TEMPLATE}
    [rag, *_others] = config["skills"]["skill_configs"]
    assert rag["rag_databases"] == [
        {
            "name": ROOM_ID_STR,
            "rag_lancedb_stem": ROOM_ID_STR,
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
    assert config["id"] == ROOM_ID_STR
    assert config["name"] == name
    assert config["description"] == name


# ---- issue_title ----


@pytest.mark.parametrize(
    "name, expected",
    [
        (ROOM_NAME, f"New room request: {ROOM_NAME}"),
        ("  Q3\n\tPlans  ", f"New room request: {ROOM_NAME}"),
    ],
)
def test_issue_title(name, expected):
    request = dataclasses.replace(REQUEST, name=name)

    result = rendering.issue_title(request)

    assert result == expected


# ---- record ----


def test_record():
    result = rendering.record(
        request=REQUEST,
        identity_claim="email",
        data_dir="/data",
        uploads_root="/uploads/rooms",
        issue_url=ISSUE_URL,
    )

    assert result == (
        f"# {ROOM_NAME}\n"
        "\n"
        f"- **Room id:** `{ROOM_ID_STR}`\n"
        f"- **Requested by:** {BR_NAME} (`{BR_EMAIL}`)\n"
        "- **Requested at:** 2026-10-28T14:15:30Z\n"
        f"- **Tracking issue:** {ISSUE_URL}\n"
        "\n"
        "## Description\n"
        "\n"
        f"{ROOM_DESC}\n"
        "\n"
        "## Provisioned\n"
        "\n"
        "- **Room config:** "
        f"`{ROOM_ID_STR}/room_config.yaml` in the "
        "provisioned-rooms clone\n"
        f"- **RAG database:** `{ROOM_ID_STR}` at "
        f"`/data/{ROOM_ID_STR}.lancedb`, ingested from "
        "the room's uploads under "
        f"`/uploads/rooms/{ROOM_ID_STR}/`\n"
        "- **Access:** private; READ and WRITE granted where claim `email` "
        f"is `{BR_EMAIL}`\n"
    )


def test_record_for_a_named_owner():
    request = dataclasses.replace(
        REQUEST,
        owner=FOREMEN,
        room_id=room_ids.RoomId(owner="foremen", slug="q3-plans"),
    )

    result = rendering.record(request=request, identity_claim="email")

    assert "- **Room id:** `foremen--q3-plans`\n" in result
    assert (f"- **Requested by:** {BR_NAME} (`{BR_EMAIL}`)\n") in result
    assert (
        "- **Access:** private; READ and WRITE granted to users the "
        "predicate `is_foreman` passes\n"
    ) in result


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({"email": BR_EMAIL}, f"`{BR_EMAIL}`"),
        (
            {"email": BR_EMAIL, "name": "  "},
            f"`{BR_EMAIL}`",
        ),
        ({"name": BR_NAME}, f"{BR_NAME} (*(unidentified)*)"),
        ({"email": ["not", "a", "string"]}, "*(unidentified)*"),
    ],
    ids=["no-name", "blank-name", "no-identity", "identity-not-a-string"],
)
def test_record_requested_by(claims, expected):
    request = dataclasses.replace(REQUEST, requester_claims=claims)

    result = rendering.record(request=request, identity_claim="email")

    assert f"- **Requested by:** {expected}\n" in result
    assert "Tracking issue" not in result


@pytest.mark.parametrize("description", ["", "  \n "])
def test_record_blank_description(description):
    request = dataclasses.replace(REQUEST, description=description)

    result = rendering.record(request=request, identity_claim="email")

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

    result = rendering.record(request=request, identity_claim="email")

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
        "room_id": ROOM_ID_STR,
        "name": ROOM_NAME,
        "description": ROOM_DESC,
        "requested_at": "2026-10-28T14:15:30Z",
        "tracking_issue": ISSUE_URL,
        "requester": {
            "claim": "email",
            "value": BR_EMAIL,
            "name": BR_NAME,
        },
        "owner": {
            "owner": OWNER.owner,
            "claim": "email",
            "value": BR_EMAIL,
            "json_path": f'$[?$.email == "{BR_EMAIL}"]',
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
    assert record["requester"]["value"] == BR_EMAIL
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


# ---- authz_document ----


def test_authz_document():
    policy = soliplex_cli.owner_policy(
        "foremen--q3-plans", access_rules.predicate_rule("is_foreman")
    )

    result = rendering.authz_document(policy)

    assert yaml.safe_load(result) == json.loads(policy)
    assert result.startswith("room_id: foremen--q3-plans\n")


# ---- fragment ----


def test_fragment():
    result = rendering.fragment(REQUEST)

    assert result.startswith("# Written by the soliplex-template provisioner.")
    assert yaml.safe_load(result) == {
        "ingester": {
            "databases": {
                ROOM_ID_STR: {
                    "sources": [
                        {
                            "type": "fs",
                            "id": ROOM_ID_STR,
                            "root": f"/uploads/rooms/{ROOM_ID_STR}",
                            "delete_orphans": True,
                        }
                    ]
                }
            }
        },
        "lancedb": {
            "databases": {ROOM_ID_STR: f"/data/{ROOM_ID_STR}.lancedb"},
        },
    }


def test_fragment_honors_data_dir_and_uploads_root():
    result = rendering.fragment(
        REQUEST, data_dir="/srv/db", uploads_root="/up"
    )

    tree = yaml.safe_load(result)
    [source] = tree["ingester"]["databases"][ROOM_ID_STR]["sources"]
    assert source["root"] == f"/up/{ROOM_ID_STR}"
    assert tree["lancedb"]["databases"] == {
        ROOM_ID_STR: f"/srv/db/{ROOM_ID_STR}.lancedb",
    }


def test_fragment_name():
    result = rendering.fragment_name("alice--q3.notes")

    assert result == "alice--q3.notes.yaml"


# ---- commit_message / provisioned_comment ----


@pytest.mark.parametrize(
    "reference, expected",
    [
        (
            None,
            f"Provision room {ROOM_ID_STR}\n\n{ROOM_NAME}\n",
        ),
        (
            "ops/requests#7",
            f"Provision room {ROOM_ID_STR}\n\n{ROOM_NAME}\n"
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

    assert result == f"Provisioned room `{ROOM_ID_STR}`."
