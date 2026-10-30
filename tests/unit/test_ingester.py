"""Unit tests for ``soliplex_template.ingester``.

The config tests edit the ingester config the template ships
(``haiku.rag/haiku.rag.yaml.mako``, rendered with Mako, a dev dependency)
and parse results back with PyYAML. The reload tests replace
``urllib.request.urlopen``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import difflib
import io
import json
import pathlib
import urllib.error

import pytest
import yaml
from mako import template as mako_template

from soliplex_template import ingester
from soliplex_template import room_ids

REPO = pathlib.Path(__file__).resolve().parents[2]
SHIPPED_CONFIG = REPO / "haiku.rag" / "haiku.rag.yaml.mako"
ROOM = room_ids.RoomId(owner="alice", slug="notes")
OTHER_ROOM = room_ids.RoomId(owner="bob", slug="plans")


def _shipped_config() -> str:
    template = mako_template.Template(
        filename=str(SHIPPED_CONFIG), strict_undefined=True
    )
    return template.render(
        chunk_size=256,
        rag_embed_model="qwen3-embedding:0.6b",
        rag_embed_dim=1024,
    )


def _room_source(room_id, uploads_root="/uploads/rooms"):
    return {
        "sources": [
            {
                "type": "fs",
                "id": room_id,
                "root": f"{uploads_root}/{room_id}",
                "delete_orphans": True,
            }
        ]
    }


# ---- register_database: the shipped config ----


def test_register_database_in_shipped_config():
    text = _shipped_config()
    before = yaml.safe_load(text)

    result, changed = ingester.register_database(text, ROOM)

    after = yaml.safe_load(result)
    assert changed is True
    assert after["ingester"]["databases"] == {
        "haiku.rag": before["ingester"]["databases"]["haiku.rag"],
        "alice:notes": _room_source("alice:notes"),
    }
    assert after["lancedb"] == {
        "databases": {
            "haiku.rag": "/data/haiku.rag.lancedb",
            "alice:notes": "/data/alice:notes.lancedb",
        }
    }
    # Nothing else moves.
    for section in before:
        if section != "ingester":
            assert after[section] == before[section]


def test_register_database_only_inserts_lines():
    text = _shipped_config()

    result, _changed = ingester.register_database(text, ROOM)

    matcher = difflib.SequenceMatcher(
        a=text.splitlines(), b=result.splitlines(), autojunk=False
    )
    tags = {tag for tag, *_ in matcher.get_opcodes()}
    assert tags <= {"equal", "insert"}


def test_register_database_is_idempotent():
    text, _changed = ingester.register_database(_shipped_config(), ROOM)

    result, changed = ingester.register_database(text, ROOM)

    assert changed is False
    assert result == text


def test_register_database_adds_to_existing_maps():
    text, _changed = ingester.register_database(_shipped_config(), ROOM)

    result, changed = ingester.register_database(text, OTHER_ROOM)

    after = yaml.safe_load(result)
    assert changed is True
    assert set(after["ingester"]["databases"]) == {
        "haiku.rag",
        "alice:notes",
        "bob:plans",
    }
    assert after["ingester"]["databases"]["bob:plans"] == _room_source(
        "bob:plans"
    )
    assert after["lancedb"]["databases"] == {
        "haiku.rag": "/data/haiku.rag.lancedb",
        "alice:notes": "/data/alice:notes.lancedb",
        "bob:plans": "/data/bob:plans.lancedb",
    }


def test_register_database_honors_data_dir_and_uploads_root():
    text = _shipped_config()

    result, _changed = ingester.register_database(
        text, ROOM, data_dir="/srv/db", uploads_root="/srv/uploads"
    )

    after = yaml.safe_load(result)
    assert after["ingester"]["databases"]["alice:notes"] == _room_source(
        "alice:notes", uploads_root="/srv/uploads"
    )
    # The room goes under the given data_dir; 'haiku.rag' stays where the
    # file's own 'storage.data_dir' (/data) places it.
    assert after["lancedb"]["databases"] == {
        "haiku.rag": "/data/haiku.rag.lancedb",
        "alice:notes": "/srv/db/alice:notes.lancedb",
    }


# ---- register_database: other shapes ----


_INGESTER = """\
storage:
  data_dir: /data
ingester:
  queue:
    namespace: soliplex
  databases:
    haiku.rag:
      sources:
        - type: fs
          id: local-docs
          root: /docs
"""


@pytest.mark.parametrize(
    "lancedb, expected",
    [
        # 'lancedb' without 'databases'
        (
            "lancedb:\n  read_consistency_interval_seconds: 30\n",
            {
                "read_consistency_interval_seconds": 30,
                "databases": {
                    "haiku.rag": "/data/haiku.rag.lancedb",
                    "alice:notes": "/data/alice:notes.lancedb",
                },
            },
        ),
        # 'lancedb' holding only a comment
        (
            "lancedb:\n# nothing set yet\n",
            {
                "databases": {
                    "haiku.rag": "/data/haiku.rag.lancedb",
                    "alice:notes": "/data/alice:notes.lancedb",
                },
            },
        ),
        # an empty 'lancedb.databases' (a comment after the key): about to be
        # non-empty, so it lists 'haiku.rag' too
        (
            "lancedb:\n  databases:  # by name\n",
            {
                "databases": {
                    "haiku.rag": "/data/haiku.rag.lancedb",
                    "alice:notes": "/data/alice:notes.lancedb",
                },
            },
        ),
        # keys spelled with quotes
        (
            "lancedb:\n  'databases':\n    \"haiku.rag\": /data/h.lancedb\n",
            {
                "databases": {
                    "haiku.rag": "/data/h.lancedb",
                    "alice:notes": "/data/alice:notes.lancedb",
                },
            },
        ),
    ],
)
def test_register_database_lancedb_shapes(lancedb, expected):
    text = _INGESTER + lancedb

    result, changed = ingester.register_database(text, ROOM)

    assert changed is True
    assert yaml.safe_load(result)["lancedb"] == expected


def test_register_database_into_empty_ingester_databases():
    text = (
        "storage:\n  data_dir: /data\n"
        "ingester:\n  queue:\n    namespace: soliplex\n  databases:\n"
    )

    result, changed = ingester.register_database(text, ROOM)

    assert changed is True
    assert yaml.safe_load(result)["ingester"]["databases"] == {
        "alice:notes": _room_source("alice:notes")
    }


def test_register_database_without_trailing_newline():
    text = _INGESTER.rstrip("\n")

    result, _changed = ingester.register_database(text, ROOM)

    assert yaml.safe_load(result)["lancedb"]["databases"] == {
        "haiku.rag": "/data/haiku.rag.lancedb",
        "alice:notes": "/data/alice:notes.lancedb",
    }


def test_register_database_completes_a_partial_registration():
    text = (
        _INGESTER
        + '    "alice:notes":\n'
        + "      sources:\n"
        + "        - type: fs\n"
        + "          root: /uploads/rooms/alice:notes\n"
    )

    result, changed = ingester.register_database(text, ROOM)

    after = yaml.safe_load(result)
    assert changed is True
    assert after["ingester"]["databases"]["alice:notes"]["sources"] == [
        {"type": "fs", "root": "/uploads/rooms/alice:notes"}
    ]
    assert "alice:notes" in after["lancedb"]["databases"]


@pytest.mark.parametrize(
    "text, exc_type, detail",
    [
        (
            "storage:\n  data_dir: /data\n",
            ingester.NoIngesterSection,
            "it has no 'ingester' section",
        ),
        (
            "ingester:\n  queue:\n    namespace: x\n",
            ingester.NoIngesterDatabases,
            "it has no 'ingester.databases'",
        ),
        (
            "ingester: {}\n",
            ingester.InlineSection,
            "'ingester' has an inline value",
        ),
        (
            "ingester:\n  databases: {}\n",
            ingester.InlineSection,
            "'ingester.databases' has an inline value",
        ),
        (
            _INGESTER + "lancedb: {}\n",
            ingester.InlineSection,
            "'lancedb' has an inline value",
        ),
        (
            _INGESTER + "lancedb:\n  databases: {}\n",
            ingester.InlineSection,
            "'lancedb.databases' has an inline value",
        ),
        (
            "ingester:\n  databases:\n    - haiku.rag\n",
            ingester.NotAMapping,
            "'ingester.databases' is not a mapping",
        ),
        (
            _INGESTER + "lancedb:\n  databases:\n    - /data/x.lancedb\n",
            ingester.NotAMapping,
            "'lancedb.databases' is not a mapping",
        ),
    ],
)
def test_register_database_refuses_unsupported_shapes(text, exc_type, detail):
    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        ingester.register_database(text, ROOM)

    assert type(caught.value) is exc_type
    assert caught.value.detail == detail
    assert "edit 'haiku.rag/haiku.rag.yaml' by hand" in str(caught.value)


# ---- reload ----


class _FakeUrlopen:
    """Records the request; answers with ``response`` or raises ``error``."""

    def __init__(self, *, response=None, error=None):
        self.response, self.error = response, error
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode())


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://haiku-ingester:8765/reload",
        code,
        "error",
        {},
        io.BytesIO(body.encode()),
    )


_APPLIED = {
    "outcome": "applied",
    "databases": [
        {"database": "alice:notes", "change": "added", "error": None}
    ],
}


def test_reload(monkeypatch):
    fake = _FakeUrlopen(response=_APPLIED)
    monkeypatch.setattr(ingester.urllib.request, "urlopen", fake)

    result = ingester.reload(
        "http://haiku-ingester:8765/", "s3cret", timeout=7
    )

    assert result == _APPLIED
    [(request, timeout)] = fake.requests
    assert request.full_url == "http://haiku-ingester:8765/reload"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer s3cret"
    assert timeout == 7


@pytest.mark.parametrize(
    "report, message",
    [
        (
            {
                "outcome": "partial",
                "databases": [
                    {"database": "bob:plans", "change": "added"},
                    {
                        "database": "alice:notes",
                        "change": "failed",
                        "error": "config mismatch",
                    },
                ],
            },
            "reload was partial: alice:notes: config mismatch",
        ),
        (
            {"outcome": "failed"},
            'reload was failed: {"outcome": "failed"}',
        ),
    ],
)
def test_reload_reports_failed_databases(monkeypatch, report, message):
    fake = _FakeUrlopen(error=_http_error(500, json.dumps(report)))
    monkeypatch.setattr(ingester.urllib.request, "urlopen", fake)

    with pytest.raises(ingester.IngesterError) as caught:
        ingester.reload("http://haiku-ingester:8765", "s3cret")

    assert type(caught.value) is ingester.ReloadFailed
    assert caught.value.report == report
    assert message in str(caught.value)


@pytest.mark.parametrize(
    "code, body, detail",
    [
        (
            409,
            '{"detail": "storage.data_dir changed"}',
            "storage.data_dir changed",
        ),
        (422, '{"detail": "bad yaml"}', "bad yaml"),
        (
            503,
            '{"detail": "the ingester is not running"}',
            "the ingester is not running",
        ),
        (401, "Unauthorized", "Unauthorized"),
        (500, '{"detail": "boom"}', "boom"),
        (500, "[1, 2]", "[1, 2]"),
    ],
)
def test_reload_refused(monkeypatch, code, body, detail):
    fake = _FakeUrlopen(error=_http_error(code, body))
    monkeypatch.setattr(ingester.urllib.request, "urlopen", fake)

    with pytest.raises(ingester.IngesterError) as caught:
        ingester.reload("http://haiku-ingester:8765", "s3cret")

    assert type(caught.value) is ingester.ReloadRefused
    assert caught.value.code == code
    assert caught.value.detail == detail


def test_reload_unreachable(monkeypatch):
    error = urllib.error.URLError("Name or service not known")
    fake = _FakeUrlopen(error=error)
    monkeypatch.setattr(ingester.urllib.request, "urlopen", fake)

    with pytest.raises(ingester.IngesterError) as caught:
        ingester.reload("http://haiku-ingester:8765", "s3cret")

    assert type(caught.value) is ingester.IngesterUnreachable
    assert caught.value.url == "http://haiku-ingester:8765/reload"
    assert caught.value.reason == "Name or service not known"


def test_reload_timed_out(monkeypatch):
    fake = _FakeUrlopen(error=TimeoutError("timed out"))
    monkeypatch.setattr(ingester.urllib.request, "urlopen", fake)

    with pytest.raises(ingester.IngesterError) as caught:
        ingester.reload("http://haiku-ingester:8765", "s3cret", timeout=7)

    assert type(caught.value) is ingester.ReloadTimedOut
    assert caught.value.url == "http://haiku-ingester:8765/reload"
    assert caught.value.timeout == 7
    assert "may still apply" in str(caught.value)


# ---- registrations ----


def test_registrations_in_shipped_config():
    text = _shipped_config()

    result = ingester.registrations(text)

    assert result == ingester.Registrations(
        sources=frozenset({"haiku.rag"}), locations={}
    )


def test_registrations_after_register_database():
    text, _changed = ingester.register_database(_shipped_config(), ROOM)

    result = ingester.registrations(text)

    assert result == ingester.Registrations(
        sources=frozenset({"haiku.rag", "alice:notes"}),
        locations={
            "haiku.rag": "/data/haiku.rag.lancedb",
            "alice:notes": "/data/alice:notes.lancedb",
        },
    )


def test_registrations_location_forms():
    text = (
        _INGESTER
        + "lancedb:\n"
        + "  databases:\n"
        + '    "dq": "/data/dq.lancedb"  # a comment\n'
        + "    'sq': '/data/it''s.lancedb'\n"
        + "    plain: /data/plain.lancedb # note\n"
        + "    tagged:\n"
        + "      location: /data/tagged.lancedb\n"
        + "      tag: v1\n"
        + "    unplaced:\n"
        + "      tag: v1\n"
    )

    result = ingester.registrations(text)

    assert result.locations == {
        "dq": "/data/dq.lancedb",
        "sq": "/data/it's.lancedb",
        "plain": "/data/plain.lancedb",
        "tagged": "/data/tagged.lancedb",
        "unplaced": None,
    }


@pytest.mark.parametrize(
    "text",
    [
        "storage:\n  data_dir: /data\n",
        "ingester:\n  queue:\n    namespace: x\n",
        "ingester:\n  databases:\n",
    ],
)
def test_registrations_without_entries(text):
    result = ingester.registrations(text)

    assert result == ingester.Registrations(sources=frozenset(), locations={})


def test_registrations_reads_an_inline_section():
    # Only the editor refuses inline sections; reading them is fine.
    text = _INGESTER + "lancedb:\n  databases: {}\n"

    result = ingester.registrations(text)

    assert result.locations == {}


# ---- register_database: placing the implicit 'haiku.rag' database ----


# _INGESTER without its 'storage' section.
_INGESTER_ONLY = _INGESTER[_INGESTER.index("ingester:") :]


@pytest.mark.parametrize(
    "storage, expected",
    [
        ("storage:\n  data_dir: /var/lib/rag\n", "/var/lib/rag"),
        ('storage:\n  data_dir: "/var/lib/rag/"\n', "/var/lib/rag"),
        (
            "storage:\n  vacuum_retention_seconds: 1\n  data_dir: /r  # x\n",
            "/r",
        ),
    ],
)
def test_register_database_places_haiku_rag_from_storage_data_dir(
    storage, expected
):
    text = storage + _INGESTER_ONLY

    result, _changed = ingester.register_database(
        text, ROOM, data_dir="/srv/db"
    )

    assert yaml.safe_load(result)["lancedb"]["databases"] == {
        "haiku.rag": f"{expected}/haiku.rag.lancedb",
        "alice:notes": "/srv/db/alice:notes.lancedb",
    }


@pytest.mark.parametrize(
    "storage, data_dir",
    [
        ("", None),
        ("storage:\n  vacuum_retention_seconds: 1\n", None),
        ('storage:\n  data_dir: ""\n', ""),
        ("storage:\n  data_dir:\n", ""),
        ("storage:\n  data_dir: ${RAG_DATA_DIR}\n", "${RAG_DATA_DIR}"),
    ],
)
def test_register_database_refuses_an_unplaced_haiku_rag(storage, data_dir):
    text = storage + _INGESTER_ONLY

    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        ingester.register_database(text, ROOM)

    assert type(caught.value) is ingester.DefaultDatabaseUnplaced
    assert caught.value.data_dir == data_dir
    assert "'storage.data_dir'" in str(caught.value)


def test_register_database_needs_no_storage_when_lancedb_lists_databases():
    text = (
        _INGESTER_ONLY
        + "lancedb:\n  databases:\n    haiku.rag: /elsewhere/h.lancedb\n"
    )

    result, changed = ingester.register_database(text, ROOM)

    assert changed is True
    assert yaml.safe_load(result)["lancedb"]["databases"] == {
        "haiku.rag": "/elsewhere/h.lancedb",
        "alice:notes": "/data/alice:notes.lancedb",
    }


# ---- reading with PyYAML: refusals ----


_DUPLICATE = _INGESTER + "lancedb:\n  databases:\n    a: /x\n    a: /y\n"


@pytest.mark.parametrize(
    "read",
    [
        ingester.registrations,
        lambda text: ingester.register_database(text, ROOM),
    ],
)
def test_reading_refuses_a_duplicate_key(read):
    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        read(_DUPLICATE)

    assert type(caught.value) is ingester.DuplicateKey
    assert caught.value.key == "a"
    assert caught.value.line == _DUPLICATE.splitlines().index("    a: /y") + 1


@pytest.mark.parametrize(
    "text", ["ingester: [\n", "? [a, b]\n: 1\n"], ids=["unclosed", "list-key"]
)
def test_reading_refuses_unparsable_yaml(text):
    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        ingester.registrations(text)

    assert type(caught.value) is ingester.UnparsableConfig
    assert "not valid YAML" in str(caught.value)


# ---- register_database: verifying the edit ----


def test_register_database_refuses_an_edit_repeating_a_key():
    # Review item #4: a comment at column 0 inside 'ingester.databases'
    # hides the entries below it from the editor, which then adds the room
    # a second time. The edit is refused rather than written.
    text = (
        _INGESTER
        + "# a column-0 comment\n"
        + '    "alice:notes":\n'
        + "      sources:\n"
        + "        - type: fs\n"
        + "          root: /uploads/rooms/alice:notes\n"
    )

    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        ingester.register_database(text, ROOM)

    assert type(caught.value) is ingester.EditNotVerified
    assert type(caught.value.__cause__) is ingester.DuplicateKey


def test_register_database_refuses_an_edit_with_unexpected_content(
    monkeypatch,
):
    real = ingester._source_lines

    def wrong_root(room_id, indent, step, uploads_root):
        return real(room_id, indent, step, "/somewhere/else")

    monkeypatch.setattr(ingester, "_source_lines", wrong_root)

    with pytest.raises(ingester.UnsupportedConfigShape) as caught:
        ingester.register_database(_shipped_config(), ROOM)

    assert type(caught.value) is ingester.EditNotVerified
    assert caught.value.__cause__ is None
