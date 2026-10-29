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
    assert after["lancedb"]["databases"] == {
        "haiku.rag": "/srv/db/haiku.rag.lancedb",
        "alice:notes": "/srv/db/alice:notes.lancedb",
    }


# ---- register_database: other shapes ----


_INGESTER = """\
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
        # an empty 'lancedb.databases' (a comment after the key)
        (
            "lancedb:\n  databases:  # by name\n",
            {"databases": {"alice:notes": "/data/alice:notes.lancedb"}},
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
    text = "ingester:\n  queue:\n    namespace: soliplex\n  databases:\n"

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
