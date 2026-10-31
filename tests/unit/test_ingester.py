"""Unit tests for ``soliplex_template.ingester``.

Config texts are parsed back with PyYAML; the reload tests replace
``urllib.request.urlopen``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest
import yaml

from soliplex_template import ingester
from soliplex_template import room_ids

ROOM = room_ids.RoomId(owner="alice", slug="q3.notes")

# A main config as the stack ships it: the default corpus, placed, and the
# fragments directory included.
MAIN = """\
storage:
  data_dir: /data
databases_include: rooms-provisioned/databases.d
ingester:
  databases:
    haiku.rag:
      sources:
        - type: fs
          id: local-docs
          root: /docs
lancedb:
  databases:
    haiku.rag: /data/haiku.rag.lancedb
"""


# ---- render_fragment / fragment_name ----


def test_render_fragment():
    result = ingester.render_fragment(ROOM)

    assert result.startswith("# Written by the soliplex-template provisioner.")
    assert yaml.safe_load(result) == {
        "ingester": {
            "databases": {
                "alice:q3.notes": {
                    "sources": [
                        {
                            "type": "fs",
                            "id": "alice:q3.notes",
                            "root": "/uploads/rooms/alice:q3.notes",
                            "delete_orphans": True,
                        }
                    ]
                }
            }
        },
        "lancedb": {
            "databases": {"alice:q3.notes": "/data/alice:q3.notes.lancedb"}
        },
    }


def test_render_fragment_honors_data_dir_and_uploads_root():
    result = ingester.render_fragment(
        ROOM, data_dir="/srv/db", uploads_root="/up"
    )

    tree = yaml.safe_load(result)
    [source] = tree["ingester"]["databases"]["alice:q3.notes"]["sources"]
    assert source["root"] == "/up/alice:q3.notes"
    assert tree["lancedb"]["databases"] == {
        "alice:q3.notes": "/srv/db/alice:q3.notes.lancedb"
    }


def test_fragment_name():
    result = ingester.fragment_name(ROOM)

    assert result == "alice:q3.notes.yaml"


# ---- fragment_texts ----


def test_fragment_texts_as_haiku_rag_reads_them(tmp_path):
    (tmp_path / "b.yaml").write_text("b")
    (tmp_path / "a.yaml").write_text("a")
    (tmp_path / ".hidden.yaml").write_text("hidden")
    (tmp_path / "notes.txt").write_text("text")
    (tmp_path / "c.yml").write_text("yml")

    result = ingester.fragment_texts(tmp_path)

    assert result == ["a", "b"]


def test_fragment_texts_of_a_missing_directory(tmp_path):
    result = ingester.fragment_texts(tmp_path / "databases.d")

    assert result == []


# ---- check_config ----


_INCLUDE_ARGS = {
    "config_dir": "/app",
    "fragments_dir": "/app/rooms-provisioned/databases.d",
}


@pytest.mark.parametrize(
    "include",
    [
        "rooms-provisioned/databases.d",
        "./rooms-provisioned/databases.d/",
        "/app/rooms-provisioned/databases.d",
    ],
)
def test_check_config_accepts(include):
    text = MAIN.replace("rooms-provisioned/databases.d", include)

    result = ingester.check_config(text, **_INCLUDE_ARGS)

    assert result is None


@pytest.mark.parametrize(
    "text, found",
    [
        (
            MAIN.replace(
                "databases_include: rooms-provisioned/databases.d\n", ""
            ),
            None,
        ),
        (
            MAIN.replace("rooms-provisioned/databases.d", "elsewhere.d"),
            "elsewhere.d",
        ),
        (MAIN.replace("rooms-provisioned/databases.d", "[a, b]"), ["a", "b"]),
        ("", None),
    ],
    ids=["missing", "elsewhere", "not-a-path", "empty-file"],
)
def test_check_config_refuses_fragments_not_included(text, found):
    with pytest.raises(ingester.UnusableConfig) as caught:
        ingester.check_config(text, **_INCLUDE_ARGS)

    assert type(caught.value) is ingester.FragmentsNotIncluded
    assert caught.value.expected == "/app/rooms-provisioned/databases.d"
    assert caught.value.found == found


def test_check_config_refuses_an_unplaced_database():
    text = MAIN[: MAIN.index("lancedb:")]

    with pytest.raises(ingester.UnusableConfig) as caught:
        ingester.check_config(text, **_INCLUDE_ARGS)

    assert type(caught.value) is ingester.DatabaseUnplaced
    assert caught.value.name == "haiku.rag"
    assert "'lancedb.databases'" in str(caught.value)


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


def test_registrations_of_the_main_file_and_fragments():
    fragment = ingester.render_fragment(ROOM)

    result = ingester.registrations(MAIN, fragment)

    assert result == ingester.Registrations(
        sources=frozenset({"haiku.rag", "alice:q3.notes"}),
        locations={
            "haiku.rag": "/data/haiku.rag.lancedb",
            "alice:q3.notes": "/data/alice:q3.notes.lancedb",
        },
    )


def test_registrations_location_forms():
    text = (
        "lancedb:\n"
        "  databases:\n"
        '    "dq": "/data/dq.lancedb"  # a comment\n'
        "    'sq': '/data/it''s.lancedb'\n"
        "    plain: /data/plain.lancedb # note\n"
        "    tagged:\n"
        "      location: /data/tagged.lancedb\n"
        "      tag: v1\n"
        "    unplaced:\n"
        "      tag: v1\n"
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
        "",
    ],
)
def test_registrations_without_entries(text):
    result = ingester.registrations(text)

    assert result == ingester.Registrations(sources=frozenset(), locations={})


# ---- reading with PyYAML: refusals ----


_DUPLICATE = "lancedb:\n  databases:\n    a: /x\n    a: /y\n"


def test_reading_refuses_a_duplicate_key():
    with pytest.raises(ingester.UnusableConfig) as caught:
        ingester.registrations(MAIN, _DUPLICATE)

    assert type(caught.value) is ingester.DuplicateKey
    assert caught.value.key == "a"
    assert caught.value.line == 4


@pytest.mark.parametrize(
    "text", ["ingester: [\n", "? [a, b]\n: 1\n"], ids=["unclosed", "list-key"]
)
def test_reading_refuses_unparsable_yaml(text):
    with pytest.raises(ingester.UnusableConfig) as caught:
        ingester.check_config(text, **_INCLUDE_ARGS)

    assert type(caught.value) is ingester.UnparsableConfig
    assert "not valid YAML" in str(caught.value)
