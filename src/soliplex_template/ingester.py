"""Register a provisioned room's database with the stack's ingester.

The ``haiku-ingester`` service reads ``haiku.rag/haiku.rag.yaml``, which
names a directory of fragments with ``databases_include``: haiku.rag merges
every ``*.yaml`` there into its ``ingester.databases`` and
``lancedb.databases`` maps (in name order, skipping dotfiles). Each
provisioned room's database has a fragment of its own
(:func:`render_fragment`):

- ``ingester.databases."<room_id>"``: one ``fs`` source over
  ``<uploads_root>/<room_id>``, with ``delete_orphans`` on
- ``lancedb.databases."<room_id>"``: placed at
  ``<data_dir>/<room_id>.lancedb``

:func:`check_config` refuses a main file the fragments cannot work with,
and :func:`registrations` reads what the main file and the fragments
already register. :func:`reload` then asks the running ingester to apply the
config again (``POST /reload``). The ingester opens each added database with
its own configuration, creating it when missing, so the new database records
the ingester's embedder.

Files are read with PyYAML, through a loader that refuses duplicate keys.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import posixpath
import urllib.error
import urllib.request

import yaml

from soliplex_template import room_ids

# The fragments' directory in the provisioned-rooms clone.
FRAGMENTS_DIR = "databases.d"
DEFAULT_DATA_DIR = "/data"
DEFAULT_UPLOADS_ROOT = "/uploads/rooms"
RELOAD_TIMEOUT = 120

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


class IngesterError(Exception):
    """A user-facing error (printed without a traceback)."""


class UnusableConfig(IngesterError):
    """An ingester config file the provisioner cannot work with."""

    def __init__(self, detail):
        self.detail = detail
        super().__init__(
            f"the ingester config cannot take provisioned rooms: {detail}"
        )


class UnparsableConfig(UnusableConfig):
    def __init__(self, error):
        self.error = error
        super().__init__(f"it is not valid YAML: {error}")


class DuplicateKey(UnusableConfig):
    def __init__(self, key, line):
        self.key, self.line = key, line
        super().__init__(
            f"key {key!r} appears twice in one mapping (line {line})"
        )


class FragmentsNotIncluded(UnusableConfig):
    def __init__(self, expected, found):
        self.expected, self.found = expected, found
        super().__init__(
            f"its 'databases_include' must name {expected!r} (it names "
            f"{found!r}); edit 'haiku.rag/haiku.rag.yaml'"
        )


class DatabaseUnplaced(UnusableConfig):
    def __init__(self, name):
        self.name = name
        super().__init__(
            f"database {name!r} is in 'ingester.databases' but not "
            "'lancedb.databases', which haiku.rag requires once fragments "
            "place databases there; edit 'haiku.rag/haiku.rag.yaml'"
        )


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


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def fragment_name(room_id: room_ids.RoomId | str) -> str:
    """The file name of ``room_id``'s fragment."""
    return f"{room_id}.yaml"


def render_fragment(
    room_id: room_ids.RoomId,
    *,
    data_dir: str = DEFAULT_DATA_DIR,
    uploads_root: str = DEFAULT_UPLOADS_ROOT,
) -> str:
    """The text of ``room_id``'s fragment, every value YAML-safe."""
    name = str(room_id)
    return _FRAGMENT.format(
        name=_scalar(name),
        root=_scalar(f"{uploads_root}/{name}"),
        location=_scalar(f"{data_dir}/{name}.lancedb"),
    )


def fragment_texts(directory: pathlib.Path) -> list[str]:
    """The fragments in ``directory``, as haiku.rag reads them.

    Every ``*.yaml`` file in name order, dotfiles skipped; a missing
    directory has none.
    """
    if not directory.is_dir():
        return []
    paths = sorted(
        path
        for path in directory.glob("*.yaml")
        if not path.name.startswith(".")
    )
    return [path.read_text() for path in paths]


def check_config(text: str, *, config_dir: str, fragments_dir: str) -> None:
    """Refuse a main ingester config the room fragments cannot work with.

    Its ``databases_include`` must name ``fragments_dir`` (relative to
    ``config_dir``, the config file's directory, as haiku.rag resolves
    it; both as the ingester sees them), and every database in its
    ``ingester.databases`` must be placed in its ``lancedb.databases``.
    Raises :class:`FragmentsNotIncluded` or :class:`DatabaseUnplaced`.
    """
    tree = _load(text)
    found = tree.get("databases_include") if isinstance(tree, dict) else None
    included = (
        posixpath.normpath(posixpath.join(config_dir, found))
        if isinstance(found, str)
        else None
    )
    if included != posixpath.normpath(fragments_dir):
        raise FragmentsNotIncluded(fragments_dir, found)
    placed = _mapping(tree, "lancedb", "databases")
    for name in _mapping(tree, "ingester", "databases"):
        if name not in placed:
            raise DatabaseUnplaced(name)


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


def registrations(*texts: str) -> Registrations:
    """The databases ingester config ``texts`` name together, in either map.

    The texts are the main file and its fragments. Raises
    :class:`UnparsableConfig` or :class:`DuplicateKey` for a text PyYAML
    refuses, or one repeating a key.
    """
    sources = set()
    locations = {}
    for text in texts:
        tree = _load(text)
        sources.update(_mapping(tree, "ingester", "databases"))
        locations.update(
            {
                name: _placed_at(entry)
                for name, entry in _mapping(
                    tree, "lancedb", "databases"
                ).items()
            }
        )
    return Registrations(sources=frozenset(sources), locations=locations)


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
