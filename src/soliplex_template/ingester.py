"""Register a provisioned room's database with the stack's ingester.

The ``haiku-ingester`` service reads ``haiku.rag/haiku.rag.yaml``.
:func:`register_database` edits that file's text, preserving every existing
line, so that the ingester writes the room's database from the room's
uploads:

- ``ingester.databases."<room_id>"`` gains one ``fs`` source over
  ``<uploads_root>/<room_id>``, with ``delete_orphans`` on
- ``lancedb.databases`` places ``"<room_id>"`` at
  ``<data_dir>/<room_id>.lancedb``; when the file has no
  ``lancedb.databases`` yet, it is created with the ``haiku.rag`` database
  the ingester otherwise places implicitly, since haiku.rag requires every
  ingested database to be listed there once the map is non-empty

:func:`reload` then asks the running ingester to apply the change
(``POST /reload``). The ingester opens each added database with its own
configuration, creating it when missing, so the new database records the
ingester's embedder.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.error
import urllib.request

from soliplex_template import room_ids

DEFAULT_DATABASE = "haiku.rag"
DEFAULT_DATA_DIR = "/data"
DEFAULT_UPLOADS_ROOT = "/uploads/rooms"
RELOAD_TIMEOUT = 120

# A mapping-key line: indent, then a double-quoted, single-quoted, or plain
# key, then ':' and an optional inline value (or comment).
_KEY_RE = re.compile(
    r"""^(?P<indent>[ ]*)
    (?:"(?P<dq>(?:[^"\\]|\\.)*)"|'(?P<sq>(?:[^']|'')*)'|(?P<plain>[^\s#"'-][^#]*?))
    [ ]*:(?:[ ]+(?P<value>.*?))?[ ]*$""",
    re.VERBOSE,
)


class IngesterError(Exception):
    """A user-facing error (printed without a traceback)."""


class UnsupportedConfigShape(IngesterError):
    """The ingester config has a shape :func:`register_database` won't edit."""

    def __init__(self, detail):
        self.detail = detail
        super().__init__(
            f"cannot register the database in the ingester config: {detail} "
            "(edit 'haiku.rag/haiku.rag.yaml' by hand)"
        )


class NoIngesterSection(UnsupportedConfigShape):
    def __init__(self):
        super().__init__("it has no 'ingester' section")


class NoIngesterDatabases(UnsupportedConfigShape):
    def __init__(self):
        super().__init__("it has no 'ingester.databases'")


class NotAMapping(UnsupportedConfigShape):
    def __init__(self, path):
        self.path = path
        super().__init__(f"'{path}' is not a mapping")


class InlineSection(UnsupportedConfigShape):
    def __init__(self, path):
        self.path = path
        super().__init__(f"'{path}' has an inline value")


class IngesterUnreachable(IngesterError):
    def __init__(self, url, reason):
        self.url, self.reason = url, reason
        super().__init__(f"cannot reach the ingester at {url}: {reason}")


class ReloadRefused(IngesterError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(
            f"the ingester refused the reload (HTTP {code}): {detail}"
        )


class ReloadFailed(IngesterError):
    def __init__(self, report):
        self.report = report
        failed = [
            f"{change.get('database')}: {change.get('error')}"
            for change in report.get("databases", [])
            if change.get("change") == "failed"
        ]
        super().__init__(
            f"the ingester's reload was {report.get('outcome')}: "
            + ("; ".join(failed) or json.dumps(report))
        )


@dataclasses.dataclass(frozen=True)
class _Key:
    indent: int
    name: str
    value: str | None


def _parse_key(line: str) -> _Key | None:
    match = _KEY_RE.match(line.rstrip("\n"))
    if match is None:
        return None
    if match["dq"] is not None:
        name = json.loads(f'"{match["dq"]}"')
    elif match["sq"] is not None:
        name = match["sq"].replace("''", "'")
    else:
        name = match["plain"]
    value = match["value"]
    if value is not None and value.startswith("#"):
        value = None
    return _Key(len(match["indent"]), name, value or None)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    return not line.strip()


def _block_end(lines: list[str], start: int) -> int:
    """Index after the last non-blank line of the block keyed at ``start``."""
    indent = _indent(lines[start])
    end = start + 1
    for i in range(start + 1, len(lines)):
        if _is_blank(lines[i]):
            continue
        if _indent(lines[i]) <= indent:
            break
        end = i + 1
    return end


def _child_keys(
    lines: list[str], start: int, path: str
) -> tuple[int | None, dict]:
    """The indent of the block's keys and ``{name: line index}`` of them.

    Raises :class:`NotAMapping` when a line at that indent is not a key.
    """
    end = _block_end(lines, start)
    child_indent = None
    keys = {}
    for i in range(start + 1, end):
        line = lines[i]
        if _is_blank(line) or line.lstrip().startswith("#"):
            continue
        if child_indent is None:
            child_indent = _indent(line)
        if _indent(line) == child_indent:
            key = _parse_key(line)
            if key is None:
                raise NotAMapping(path)
            keys[key.name] = i
    return child_indent, keys


def _top_level(lines: list[str], name: str) -> int | None:
    for i, line in enumerate(lines):
        key = _parse_key(line)
        if key is not None and key.indent == 0 and key.name == name:
            return i
    return None


def _require_block(lines: list[str], index: int, path: str) -> None:
    if _parse_key(lines[index]).value is not None:
        raise InlineSection(path)


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _source_lines(
    room_id: str, indent: int, step: int, uploads_root: str
) -> list[str]:
    pad = " " * indent
    sub = " " * step
    return [
        f"{pad}{_scalar(room_id)}:\n",
        f"{pad}{sub}sources:\n",
        f"{pad}{sub * 2}- type: fs\n",
        f"{pad}{sub * 3}id: {_scalar(room_id)}\n",
        f"{pad}{sub * 3}root: {_scalar(f'{uploads_root}/{room_id}')}\n",
        f"{pad}{sub * 3}delete_orphans: true\n",
    ]


def _location_line(name: str, indent: int, data_dir: str) -> str:
    location = _scalar(f"{data_dir}/{name}.lancedb")
    return f"{' ' * indent}{_scalar(name)}: {location}\n"


def _register_source(
    lines: list[str], room_id: str, uploads_root: str
) -> bool:
    ingester = _top_level(lines, "ingester")
    if ingester is None:
        raise NoIngesterSection()
    _require_block(lines, ingester, "ingester")
    step, keys = _child_keys(lines, ingester, "ingester")
    if "databases" not in keys:
        raise NoIngesterDatabases()
    databases = keys["databases"]
    _require_block(lines, databases, "ingester.databases")
    entry_indent, entries = _child_keys(lines, databases, "ingester.databases")
    if room_id in entries:
        return False
    if entry_indent is None:
        entry_indent = step * 2
    at = _block_end(lines, databases)
    lines[at:at] = _source_lines(room_id, entry_indent, step, uploads_root)
    return True


def _register_location(lines: list[str], room_id: str, data_dir: str) -> bool:
    lancedb = _top_level(lines, "lancedb")
    if lancedb is None:
        lines.extend(
            [
                "\n",
                "# Databases the ingester writes, by name.\n",
                "lancedb:\n",
                "  databases:\n",
                _location_line(DEFAULT_DATABASE, 4, data_dir),
                _location_line(room_id, 4, data_dir),
            ]
        )
        return True
    _require_block(lines, lancedb, "lancedb")
    step, keys = _child_keys(lines, lancedb, "lancedb")
    if "databases" not in keys:
        step = step or 2
        at = _block_end(lines, lancedb)
        lines[at:at] = [
            f"{' ' * step}databases:\n",
            _location_line(DEFAULT_DATABASE, step * 2, data_dir),
            _location_line(room_id, step * 2, data_dir),
        ]
        return True
    databases = keys["databases"]
    _require_block(lines, databases, "lancedb.databases")
    entry_indent, entries = _child_keys(lines, databases, "lancedb.databases")
    if room_id in entries:
        return False
    if entry_indent is None:
        entry_indent = step * 2
    at = _block_end(lines, databases)
    lines[at:at] = [_location_line(room_id, entry_indent, data_dir)]
    return True


def register_database(
    text: str,
    room_id: room_ids.RoomId,
    *,
    data_dir: str = DEFAULT_DATA_DIR,
    uploads_root: str = DEFAULT_UPLOADS_ROOT,
) -> tuple[str, bool]:
    """Register ``room_id``'s database in the ingester config ``text``.

    Returns the new text and whether it changed: a database already
    registered (in either map) is left as it is. Raises a subclass of
    :class:`UnsupportedConfigShape` when the file lacks
    ``ingester.databases``, or a section to edit has an inline value.
    """
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines(keepends=True)
    name = str(room_id)
    added_source = _register_source(lines, name, uploads_root)
    added_location = _register_location(lines, name, data_dir)
    return "".join(lines), added_source or added_location


def reload(base_url: str, token: str, *, timeout: float = RELOAD_TIMEOUT):
    """Ask the ingester at ``base_url`` to apply its config file again.

    Returns the ingester's reload report when every change applied. Raises
    :class:`ReloadFailed` (with the report) when a database failed,
    :class:`ReloadRefused` when the ingester refused the new config, and
    :class:`IngesterUnreachable` when it cannot be reached.
    """
    url = f"{base_url.rstrip('/')}/reload"
    request = urllib.request.Request(
        url,
        data=b"",
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if error.code == 500 and isinstance(payload, dict):
            if "outcome" in payload:
                raise ReloadFailed(payload) from error
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise ReloadRefused(error.code, detail or body) from error
    except urllib.error.URLError as error:
        raise IngesterUnreachable(url, error.reason) from error
