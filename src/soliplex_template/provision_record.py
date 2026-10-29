"""The record of a provisioned room's request.

One Markdown text serves twice: as the body of the room's tracking issue
(when a Gitea is configured), and as ``REQUEST.md`` beside the room's
``room_config.yaml`` in the landing clone (always), where it also links the
tracking issue. Either way it says who asked for which room, when, and what
the provisioner sets up for it.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import datetime
import re

from soliplex_template import room_ids

RECORD_FILE = "REQUEST.md"
TITLE_PREFIX = "New room request: "

_LONGEST_BACKTICK_RUN_RE = re.compile(r"`+")


@dataclasses.dataclass(frozen=True)
class RoomRequest:
    """One request for a provisioned room."""

    owner: room_ids.RoomOwner
    room_id: room_ids.RoomId
    name: str
    description: str
    requested_at: datetime.datetime
    requester_name: str | None = None


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _code(text: str) -> str:
    """``text`` as a Markdown code span, whatever backticks it holds."""
    runs = _LONGEST_BACKTICK_RUN_RE.findall(text)
    fence = "`" * (max((len(run) for run in runs), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def title(request: RoomRequest) -> str:
    """The tracking issue's title."""
    return f"{TITLE_PREFIX}{_one_line(request.name)}"


def render(
    request: RoomRequest,
    *,
    data_dir: str,
    uploads_root: str,
    issue_url: str | None = None,
) -> str:
    """The request record, as Markdown.

    ``data_dir`` / ``uploads_root`` are where the ingester places the
    room's database and reads its uploads; ``issue_url`` links the tracking
    issue, when one was filed.
    """
    room_id = str(request.room_id)
    owner = request.owner
    requester = _code(owner.claim_value)
    if request.requester_name:
        requester = f"{_one_line(request.requester_name)} ({requester})"
    requested_at = (
        request.requested_at.astimezone(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines = [
        f"# {_one_line(request.name)}",
        "",
        f"- **Room id:** {_code(room_id)}",
        f"- **Requested by:** {requester}",
        f"- **Requested at:** {requested_at}",
    ]
    if issue_url is not None:
        lines.append(f"- **Tracking issue:** {issue_url}")
    lines += [
        "",
        "## Description",
        "",
        request.description.strip() or "*(none given)*",
        "",
        "## Provisioned",
        "",
        f"- **Room config:** {_code(f'{room_id}/room_config.yaml')} in the "
        "provisioned-rooms clone",
        f"- **RAG database:** {_code(room_id)} at "
        f"{_code(f'{data_dir}/{room_id}.lancedb')}, ingested from the "
        f"room's uploads under {_code(f'{uploads_root}/{room_id}/')}",
        f"- **Access:** private; READ and WRITE granted where claim "
        f"{_code(owner.claim)} is {_code(owner.claim_value)}",
        "",
    ]
    return "\n".join(lines)
