"""Render the ``room_config.yaml`` of a room the provisioner creates.

A provisioned room is a conversational RAG room over its own database: the
``haiku.rag.skills.rag`` skill searches the LanceDB whose stem is the room
id, alongside evidence compaction and the citation policy. Its agent is
the installation's ``provisioned_rag`` agent config, which carries the model
and system prompt shared by every provisioned room.

Every requester-supplied string is emitted with :func:`json.dumps`: a JSON
string is a valid YAML double-quoted scalar, so no value can escape its
field. Non-ASCII text is written as-is rather than as ``\\u`` escapes, which
YAML loaders decode one UTF-16 unit at a time.

Stdlib only.
"""

from __future__ import annotations

import json

from soliplex_template import room_ids

AGENT_TEMPLATE = "provisioned_rag"

_CONFIG = """\
# Created by the soliplex-template provisioner.
id: {room_id}
name: {name}
description: {description}

agent:
  template_id: {agent_template}

tools:
  - tool_name: "soliplex.tools.get_current_datetime"
  - tool_name: "soliplex.tools.get_current_user"

skills:
  skill_configs:
    - kind: "haiku.rag.skills.rag"
      rag_databases:
        - name: {room_id}
          rag_lancedb_stem: {room_id}
    - kind: "haiku.rag.skills.evidence_compaction"
    - kind: "haiku.rag.skills.citation_policy"

allow_mcp: false
"""


class BadRoomName(room_ids.RoomIdError):
    def __init__(self, name):
        self.name = name
        super().__init__(f"room name {name!r} must not be blank")


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_room_config(
    room_id: room_ids.RoomId,
    *,
    name: str,
    description: str | None = None,
) -> str:
    """Return the ``room_config.yaml`` text for a provisioned room.

    ``name`` is the room's human-readable name; ``description`` defaults to
    it. Raises :class:`BadRoomName` when ``name`` is blank.
    """
    if not name.strip():
        raise BadRoomName(name)

    return _CONFIG.format(
        room_id=_scalar(str(room_id)),
        name=_scalar(name),
        description=_scalar(name if description is None else description),
        agent_template=_scalar(AGENT_TEMPLATE),
    )
