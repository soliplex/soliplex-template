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
  ingested database to be listed there once the map is non-empty; that
  entry goes where haiku.rag places it,
  ``<storage.data_dir>/haiku.rag.lancedb``, as the file itself sets
  ``storage.data_dir``

:func:`reload` then asks the running ingester to apply the change
(``POST /reload``). The ingester opens each added database with its own
configuration, creating it when missing, so the new database records the
ingester's embedder.

The file is read with PyYAML, through a loader that refuses duplicate
keys. Edits keep the file's own lines and comments, and each one is
verified by parsing its result: it must equal the original plus exactly
the entries added.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import urllib.error
import urllib.request

import yaml

from soliplex_template import room_ids

DEFAULT_DATABASE = "haiku.rag"
# Where haiku.rag places DEFAULT_DATABASE: this file, in 'storage.data_dir'.
DEFAULT_DATABASE_FILENAME = "haiku.rag.lancedb"
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


class DefaultDatabaseUnplaced(UnsupportedConfigShape):
    def __init__(self, data_dir):
        self.data_dir = data_dir
        what = (
            "is not set"
            if data_dir is None
            else f"is {data_dir!r}, not a plain directory"
        )
        super().__init__(
            "the 'haiku.rag' database must be listed in 'lancedb.databases', "
            f"but its location is unknown: 'storage.data_dir' {what}"
        )


class UnparsableConfig(UnsupportedConfigShape):
    def __init__(self, error):
        self.error = error
        super().__init__(f"it is not valid YAML: {error}")


class DuplicateKey(UnsupportedConfigShape):
    def __init__(self, key, line):
        self.key, self.line = key, line
        super().__init__(
            f"key {key!r} appears twice in one mapping (line {line})"
        )


class EditNotVerified(UnsupportedConfigShape):
    def __init__(self):
        super().__init__(
            "the edited text does not parse to the original plus the room's "
            "entries"
        )


class InlineSection(UnsupportedConfigShape):
    def __init__(self, path):
        self.path = path
        super().__init__(f"'{path}' has an inline value")


class IngesterUnreachable(IngesterError):
    def __init__(self, url, reason):
        self.url, self.reason = url, reason
        super().__init__(f"cannot reach the ingester at {url}: {reason}")


class ReloadTimedOut(IngesterError):
    """The ingester did not answer the reload in time.

    It may still be applying the config, and may finish after the caller
    has given up.
    """

    def __init__(self, url, timeout):
        self.url, self.timeout = url, timeout
        super().__init__(
            f"the ingester at {url} did not answer within {timeout} seconds; "
            "its reload may still apply"
        )


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


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a key repeated in one mapping.

    PyYAML (and so the ingester) otherwise keeps the last value silently.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                repeated = key in seen
            except TypeError:  # an unhashable key: let PyYAML refuse it
                break
            if repeated:
                raise DuplicateKey(key, key_node.start_mark.line + 1)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load(text: str):
    """Parse ``text`` safely, refusing duplicate keys.

    Raises :class:`UnparsableConfig` or :class:`DuplicateKey`.
    """
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise UnparsableConfig(error) from error


def _mapping(tree, *path):
    """The mapping at ``path`` in ``tree``, or an empty one."""
    for key in path:
        tree = tree.get(key) if isinstance(tree, dict) else None
    return tree if isinstance(tree, dict) else {}


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


def _default_location(tree) -> str:
    """Where haiku.rag places ``haiku.rag`` implicitly, per ``tree``.

    That is ``<storage.data_dir>/haiku.rag.lancedb``. Raises
    :class:`DefaultDatabaseUnplaced` when the file gives no plain directory
    there: unset or empty (haiku.rag then uses a platform default), or an
    interpolated ``${...}`` value.
    """
    storage = _mapping(tree, "storage")
    data_dir = None
    if "data_dir" in storage:
        value = storage["data_dir"]
        data_dir = "" if value is None else value
    if not isinstance(data_dir, str) or not data_dir or "${" in data_dir:
        raise DefaultDatabaseUnplaced(data_dir)
    return f"{data_dir.rstrip('/')}/{DEFAULT_DATABASE_FILENAME}"


def _default_location_line(tree, indent: int) -> str:
    location = _scalar(_default_location(tree))
    return f"{' ' * indent}{_scalar(DEFAULT_DATABASE)}: {location}\n"


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


def _register_location(
    lines: list[str], room_id: str, data_dir: str, tree
) -> bool:
    lancedb = _top_level(lines, "lancedb")
    if lancedb is None:
        lines.extend(
            [
                "\n",
                "# Databases the ingester writes, by name.\n",
                "lancedb:\n",
                "  databases:\n",
                _default_location_line(tree, 4),
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
            _default_location_line(tree, step * 2),
            _location_line(room_id, step * 2, data_dir),
        ]
        return True
    databases = keys["databases"]
    _require_block(lines, databases, "lancedb.databases")
    entry_indent, entries = _child_keys(lines, databases, "lancedb.databases")
    if room_id in entries:
        return False
    added = [_location_line(room_id, entry_indent or step * 2, data_dir)]
    if not entries:
        # An empty map, about to be non-empty: list 'haiku.rag' too.
        added.insert(0, _default_location_line(tree, entry_indent or step * 2))
    at = _block_end(lines, databases)
    lines[at:at] = added
    return True


@dataclasses.dataclass(frozen=True)
class Registrations:
    """The databases an ingester config already names.

    ``sources`` holds the names in ``ingester.databases``; ``locations``
    maps each name in ``lancedb.databases`` to its location (``None`` when
    the entry's form names none this reader recognizes).
    """

    sources: frozenset[str]
    locations: dict[str, str | None]


def _placed_at(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    location = entry.get("location") if isinstance(entry, dict) else None
    return location if isinstance(location, str) else None


def registrations(text: str) -> Registrations:
    """The databases ingester config ``text`` names, in either map.

    Raises :class:`UnparsableConfig` or :class:`DuplicateKey` for a file
    PyYAML refuses, or one repeating a key.
    """
    tree = _load(text)
    return Registrations(
        sources=frozenset(_mapping(tree, "ingester", "databases")),
        locations={
            name: _placed_at(entry)
            for name, entry in _mapping(tree, "lancedb", "databases").items()
        },
    )


def _expected(before, name, *, source, location, default_location):
    """``before`` plus the entries an edit added."""
    tree = copy.deepcopy(before)
    if source is not None:
        ingester = tree["ingester"]
        if ingester.get("databases") is None:
            ingester["databases"] = {}
        ingester["databases"][name] = source
    if location is not None:
        if tree.get("lancedb") is None:
            tree["lancedb"] = {}
        if tree["lancedb"].get("databases") is None:
            tree["lancedb"]["databases"] = {}
            tree["lancedb"]["databases"][DEFAULT_DATABASE] = default_location
        tree["lancedb"]["databases"][name] = location
    return tree


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
    ``ingester.databases``, a section to edit has an inline value, or
    ``lancedb.databases`` must be created but the file's
    ``storage.data_dir`` is no plain directory
    (:class:`DefaultDatabaseUnplaced`). Raises :class:`UnparsableConfig` or
    :class:`DuplicateKey` for a file it cannot read, and
    :class:`EditNotVerified` when the edited text does not parse to the
    original plus the room's entries.
    """
    if not text.endswith("\n"):
        text += "\n"
    before = _load(text)
    lines = text.splitlines(keepends=True)
    name = str(room_id)
    created_map = not _mapping(before, "lancedb", "databases")
    added_source = _register_source(lines, name, uploads_root)
    added_location = _register_location(lines, name, data_dir, before)
    edited = "".join(lines)

    root = f"{uploads_root}/{name}"
    expected = _expected(
        before,
        name,
        source={
            "sources": [
                {
                    "type": "fs",
                    "id": name,
                    "root": root,
                    "delete_orphans": True,
                }
            ]
        }
        if added_source
        else None,
        location=f"{data_dir}/{name}.lancedb" if added_location else None,
        default_location=_default_location(before)
        if added_location and created_map
        else None,
    )
    try:
        after = _load(edited)
    except UnsupportedConfigShape as error:
        # e.g. a key the edit repeated: the original had no such fault
        raise EditNotVerified() from error
    if after != expected:
        raise EditNotVerified()
    return edited, added_source or added_location


def reload(base_url: str, token: str, *, timeout: float = RELOAD_TIMEOUT):
    """Ask the ingester at ``base_url`` to apply its config file again.

    Returns the ingester's reload report when every change applied. Raises
    :class:`ReloadFailed` (with the report) when a database failed,
    :class:`ReloadRefused` when the ingester refused the new config,
    :class:`ReloadTimedOut` when it does not answer within ``timeout``
    seconds, and :class:`IngesterUnreachable` when it cannot be reached.
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
    except TimeoutError as error:
        raise ReloadTimedOut(url, timeout) from error
