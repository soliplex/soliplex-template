"""Unit tests for ``soliplex_template.provisioned_room``.

The rendered text is parsed back with PyYAML (a dev dependency), the loader
soliplex reads room configs with.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import pytest
import yaml

from soliplex_template import provisioned_room
from soliplex_template import room_ids

ROOM_ID = room_ids.RoomId(owner="alice", slug="q3-marketing-plans")


def test_render_room_config():
    result = provisioned_room.render_room_config(
        ROOM_ID,
        name="Q3 Marketing Plans",
        description="Plans and briefs for the Q3 campaign",
    )

    assert yaml.safe_load(result) == {
        "id": "alice:q3-marketing-plans",
        "name": "Q3 Marketing Plans",
        "description": "Plans and briefs for the Q3 campaign",
        "agent": {"template_id": "provisioned_rag"},
        "tools": [
            {"tool_name": "soliplex.tools.get_current_datetime"},
            {"tool_name": "soliplex.tools.get_current_user"},
        ],
        "skills": {
            "skill_configs": [
                {
                    "kind": "haiku.rag.skills.rag",
                    "rag_databases": [
                        {
                            "name": "alice:q3-marketing-plans",
                            "rag_lancedb_stem": "alice:q3-marketing-plans",
                        },
                    ],
                },
                {"kind": "haiku.rag.skills.evidence_compaction"},
                {"kind": "haiku.rag.skills.citation_policy"},
            ],
        },
        "allow_mcp": False,
    }


def test_render_room_config_description_defaults_to_name():
    result = provisioned_room.render_room_config(ROOM_ID, name="Q3 Plans")

    assert yaml.safe_load(result)["description"] == "Q3 Plans"


@pytest.mark.parametrize(
    "name",
    [
        'Quotes "and" back\\slashes',
        "Ünïcode Café — notes",
        "Emoji 🙂 room",
        "会议记录",
        # Looks like YAML structure; stays one scalar.
        'x"\nallow_mcp: true\n#',
        "multi\nline\ttabbed",
        "bell\x07char",
    ],
)
def test_render_room_config_quotes_requester_text(name):
    result = provisioned_room.render_room_config(
        ROOM_ID, name=name, description=name
    )

    config = yaml.safe_load(result)
    assert config["name"] == name
    assert config["description"] == name
    assert config["allow_mcp"] is False


@pytest.mark.parametrize("name", ["", "   ", "\n\t"])
def test_render_room_config_rejects_blank_name(name):
    with pytest.raises(room_ids.RoomIdError) as caught:
        provisioned_room.render_room_config(ROOM_ID, name=name)

    assert type(caught.value) is provisioned_room.BadRoomName
    assert caught.value.name == name
    assert "must not be blank" in str(caught.value)
