"""Unit tests for ``soliplex_template.provision_record``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from soliplex_template import provision_record
from soliplex_template import room_ids

OWNER = room_ids.RoomOwner(
    claim="email",
    claim_value="tseaver@sitename.example",
    owner="tseaver_sitename.example",
)
REQUEST = provision_record.RoomRequest(
    owner=OWNER,
    room_id=room_ids.RoomId(owner=OWNER.owner, slug="q3-plans"),
    name="Q3 Plans",
    description="Plans and briefs\nfor the Q3 campaign.",
    requested_at=datetime.datetime(
        2026,
        10,
        28,
        10,
        15,
        30,
        123456,
        tzinfo=datetime.timezone(datetime.timedelta(hours=-4)),
    ),
    requester_name="Tres Seaver",
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Q3 Plans", "New room request: Q3 Plans"),
        ("  Q3\n\tPlans  ", "New room request: Q3 Plans"),
    ],
)
def test_title(name, expected):
    request = dataclasses.replace(REQUEST, name=name)

    result = provision_record.title(request)

    assert result == expected


def test_render():
    result = provision_record.render(
        REQUEST,
        data_dir="/data",
        uploads_root="/uploads/rooms",
        issue_url="https://gitea.example.com/ops/requests/issues/7",
    )

    assert result == (
        "# Q3 Plans\n"
        "\n"
        "- **Room id:** `tseaver_sitename.example:q3-plans`\n"
        "- **Requested by:** Tres Seaver (`tseaver@sitename.example`)\n"
        "- **Requested at:** 2026-10-28T14:15:30Z\n"
        "- **Tracking issue:** "
        "https://gitea.example.com/ops/requests/issues/7\n"
        "\n"
        "## Description\n"
        "\n"
        "Plans and briefs\n"
        "for the Q3 campaign.\n"
        "\n"
        "## Provisioned\n"
        "\n"
        "- **Room config:** "
        "`tseaver_sitename.example:q3-plans/room_config.yaml` in the "
        "provisioned-rooms clone\n"
        "- **RAG database:** `tseaver_sitename.example:q3-plans` at "
        "`/data/tseaver_sitename.example:q3-plans.lancedb`, ingested from "
        "the room's uploads under "
        "`/uploads/rooms/tseaver_sitename.example:q3-plans/`\n"
        "- **Access:** private; READ and WRITE granted where claim `email` "
        "is `tseaver@sitename.example`\n"
    )


def test_render_without_requester_name_or_issue():
    request = dataclasses.replace(REQUEST, requester_name=None)

    result = provision_record.render(
        request, data_dir="/data", uploads_root="/uploads/rooms"
    )

    assert "- **Requested by:** `tseaver@sitename.example`\n" in result
    assert "Tracking issue" not in result


@pytest.mark.parametrize("description", ["", "  \n "])
def test_render_blank_description(description):
    request = dataclasses.replace(REQUEST, description=description)

    result = provision_record.render(
        request, data_dir="/data", uploads_root="/uploads/rooms"
    )

    assert "## Description\n\n*(none given)*\n" in result


@pytest.mark.parametrize(
    "claim_value, expected",
    [
        ("a`b", "``a`b``"),
        ("a``b", "```a``b```"),
        ("`edge`", "`` `edge` ``"),
    ],
)
def test_render_code_spans_hold_backticks(claim_value, expected):
    owner = dataclasses.replace(OWNER, claim_value=claim_value)
    request = dataclasses.replace(REQUEST, owner=owner, requester_name=None)

    result = provision_record.render(
        request, data_dir="/data", uploads_root="/uploads/rooms"
    )

    assert f"- **Requested by:** {expected}\n" in result
