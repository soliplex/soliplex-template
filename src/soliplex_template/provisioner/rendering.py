"""The texts and files the template's room provisioner writes.

The concierge's provisioner core renders nothing; the template's adapters
(:mod:`soliplex_template.provisioner.adapters`) write what is here:

- a provisioned room's ``room_config.yaml`` (:func:`room_config`): a
  conversational RAG room over its own database, its agent the
  installation's ``provisioned_rag`` agent config
- the request's record (:func:`record`), which serves twice: as the body of
  the tracking issue (:func:`issue_title` its title), and as ``REQUEST.md``
  (:data:`RECORD_FILE`) beside the room's config
- the room's haiku.rag fragment (:func:`fragment`), registering its database
  with the ingester (:data:`FRAGMENTS_DIR`, :func:`fragment_name`)
- the commit recording the room (:func:`commit_message`), and the comment
  resolving its tracking issue (:func:`provisioned_comment`)

Every requester-supplied string in YAML is emitted with :func:`json.dumps`:
a JSON string is a valid YAML double-quoted scalar, so no value can escape
its field. Non-ASCII text is written as-is rather than as ``\\u`` escapes,
which YAML loaders decode one UTF-16 unit at a time.
"""

from __future__ import annotations

import datetime
import json
import re

from soliplex_concierge.provisioner import interfaces

AGENT_TEMPLATE = "provisioned_rag"
RECORD_FILE = "REQUEST.md"
TITLE_PREFIX = "New room request: "

# The fragments' directory in the provisioned-rooms clone, and where the
# ingester places a room's database and reads its uploads.
FRAGMENTS_DIR = "databases.d"
DEFAULT_DATA_DIR = "/data"
DEFAULT_UPLOADS_ROOT = "/uploads/rooms"

_ROOM_CONFIG = """\
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

_FRAGMENT = """\
# Written by the soliplex-template provisioner.
ingester:
  databases:
    {name}:
      sources:
        - type: fs
          id: {name}
          root: {root}
          delete_orphans: true
lancedb:
  databases:
    {name}: {location}
"""

_LONGEST_BACKTICK_RUN_RE = re.compile(r"`+")
# The rules soliplex_concierge.provisioner.access_rules builds.
_CLAIM_RULE_RE = re.compile(r'\$\[\?\$\.([A-Za-z_][A-Za-z0-9_]*) == (".*")\]')
_PREDICATE_RULE_RE = re.compile(r"\$\[\?([a-z][a-z0-9_]*)\(\$\)\]")


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def code(text: str) -> str:
    """``text`` as a Markdown code span, whatever backticks it holds."""
    runs = _LONGEST_BACKTICK_RUN_RE.findall(text)
    fence = "`" * (max((len(run) for run in runs), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


# ---------------------------------------------------------------------------
# The room's config
# ---------------------------------------------------------------------------
def room_config(request: interfaces.RoomRequest) -> str:
    """The ``room_config.yaml`` text of the requested room."""
    return _ROOM_CONFIG.format(
        room_id=_scalar(request.room),
        name=_scalar(request.name),
        description=_scalar(request.description),
        agent_template=_scalar(AGENT_TEMPLATE),
    )


# ---------------------------------------------------------------------------
# The request's record
# ---------------------------------------------------------------------------
def issue_title(request: interfaces.RoomRequest) -> str:
    """The tracking issue's title."""
    return f"{TITLE_PREFIX}{_one_line(request.name)}"


def describe_rule(json_path: str) -> str:
    """Whom an owner's access rule grants the room, in words.

    The rules the concierge's ``access_rules`` builds read as such; any
    other is quoted.
    """
    claim = _CLAIM_RULE_RE.fullmatch(json_path)
    if claim is not None:
        try:
            value = json.loads(claim.group(2))
        except json.JSONDecodeError:
            pass  # not a single JSON string: quote the rule
        else:
            return f"where claim {code(claim.group(1))} is {code(value)}"
    predicate = _PREDICATE_RULE_RE.fullmatch(json_path)
    if predicate is not None:
        return f"to users the predicate {code(predicate.group(1))} passes"
    return f"by the access rule {code(json_path)}"


def _requester(request: interfaces.RoomRequest, identity_claim: str) -> str:
    claims = request.requester_claims
    identity = claims.get(identity_claim)
    who = code(identity) if isinstance(identity, str) else "*(unidentified)*"
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        who = f"{_one_line(name)} ({who})"
    return who


def record(
    request: interfaces.RoomRequest,
    *,
    identity_claim: str,
    data_dir: str = DEFAULT_DATA_DIR,
    uploads_root: str = DEFAULT_UPLOADS_ROOT,
    issue_url: str | None = None,
) -> str:
    """The request's record, as Markdown.

    ``identity_claim`` names the requester's claim that identifies them;
    ``data_dir`` / ``uploads_root`` are where the ingester places the room's
    database and reads its uploads; ``issue_url`` links the tracking issue,
    when one was filed.
    """
    room_id = request.room
    requested_at = (
        request.requested_at.astimezone(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines = [
        f"# {_one_line(request.name)}",
        "",
        f"- **Room id:** {code(room_id)}",
        f"- **Requested by:** {_requester(request, identity_claim)}",
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
        f"- **Room config:** {code(f'{room_id}/room_config.yaml')} in the "
        "provisioned-rooms clone",
        f"- **RAG database:** {code(room_id)} at "
        f"{code(f'{data_dir}/{room_id}.lancedb')}, ingested from the "
        f"room's uploads under {code(f'{uploads_root}/{room_id}/')}",
        "- **Access:** private; READ and WRITE granted "
        f"{describe_rule(request.owner.json_path)}",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The ingester fragment
# ---------------------------------------------------------------------------
def fragment_name(room_id: str) -> str:
    """The file name of ``room_id``'s fragment."""
    return f"{room_id}.yaml"


def fragment(
    request: interfaces.RoomRequest,
    *,
    data_dir: str = DEFAULT_DATA_DIR,
    uploads_root: str = DEFAULT_UPLOADS_ROOT,
) -> str:
    """The text of the room's ingester fragment, every value YAML-safe.

    - ``ingester.databases."<room_id>"``: one ``fs`` source over
      ``<uploads_root>/<room_id>``, with ``delete_orphans`` on
    - ``lancedb.databases."<room_id>"``: placed at
      ``<data_dir>/<room_id>.lancedb``
    """
    name = request.room
    return _FRAGMENT.format(
        name=_scalar(name),
        root=_scalar(f"{uploads_root}/{name}"),
        location=_scalar(f"{data_dir}/{name}.lancedb"),
    )


# ---------------------------------------------------------------------------
# The commit, and the tracking issue's resolution
# ---------------------------------------------------------------------------
def commit_message(
    request: interfaces.RoomRequest, *, tracking_reference: str | None = None
) -> str:
    """The message of the commit recording the room.

    ``tracking_reference`` (e.g. ``ops/requests#7``) names its tracking
    issue, when one was filed.
    """
    message = f"Provision room {request.room}\n\n{request.name}\n"
    if tracking_reference is not None:
        message += f"\nTracking issue: {tracking_reference}\n"
    return message


def provisioned_comment(provisioned: interfaces.Provisioned) -> str:
    """The comment resolving a provisioned room's tracking issue."""
    return f"Provisioned room `{provisioned.request.room}`."
