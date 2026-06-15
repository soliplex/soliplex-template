"""Unit tests for ``scripts/build_skill.py``.

Since the adoption of ``soliplex-skills`` this is a thin wrapper over
``soliplex_skills.build.build_skill`` (copy -> stamp -> validate); these tests
pin that it delegates with the right arguments and translates the library's
errors to a clean nonzero exit. The build/stamp/validate behavior itself is
covered by the ``soliplex-skills`` test suite.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once.
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest import mock

import pytest
from soliplex_skills import build

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "build_skill.py"
)
_spec = importlib.util.spec_from_file_location("build_skill", _MODULE_PATH)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


@pytest.fixture(autouse=True)
def run(monkeypatch):
    """Stub the refresh subprocess so unit tests never shell out to uv."""
    run = mock.Mock()
    monkeypatch.setattr(bs.subprocess, "run", run)
    return run


@pytest.fixture
def build_skill(monkeypatch):
    """Record calls to the library's build_skill; return the recorder list."""
    calls = []

    def fake(name, *, src, dist, commit=None, version=None, generated=None):
        calls.append((name, src, dist, commit, version, generated))
        return dist / name

    monkeypatch.setattr(bs.build, "build_skill", fake)
    return calls


def test_main_delegates_to_library(build_skill, capsys):
    rc = bs.main(["--commit", "abc1234"])

    assert rc == 0
    assert build_skill == [
        ("soliplex-template", bs.SKILLS_DIR, bs.DIST, "abc1234", None, None)
    ]
    assert "built & validated" in capsys.readouterr().out


def test_main_refreshes_template_before_build(run, build_skill):
    rc = bs.main([])

    assert rc == 0
    # The embedded template is regenerated (via `uv run refresh`) before the
    # library assembles the skill -- it is a gitignored build artifact (#135).
    run.assert_called_once_with(
        ["uv", "run", str(bs.REFRESH_SCRIPT)], check=True
    )
    assert build_skill  # and the build still ran


def test_main_defaults_commit_to_none(build_skill):
    rc = bs.main([])

    assert rc == 0
    assert build_skill[0] == (
        "soliplex-template",
        bs.SKILLS_DIR,
        bs.DIST,
        None,
        None,
        None,
    )


def test_main_forwards_version_and_date(build_skill):
    rc = bs.main(["--version", "v1.2.3", "--date", "2026-06-14"])

    assert rc == 0
    assert build_skill[0] == (
        "soliplex-template",
        bs.SKILLS_DIR,
        bs.DIST,
        None,
        "v1.2.3",
        "2026-06-14",
    )


def test_main_reports_skill_not_found(monkeypatch, capsys):
    def boom(name, *, src, dist, commit=None, version=None, generated=None):
        raise build.SkillNotFound(name, src)

    monkeypatch.setattr(bs.build, "build_skill", boom)

    rc = bs.main([])

    assert rc == 1
    assert "no skill" in capsys.readouterr().err


def test_main_reports_validation_failure(monkeypatch, capsys):
    def boom(name, *, src, dist, commit=None, version=None, generated=None):
        raise build.ValidationFailed(name, ["bad frontmatter"])

    monkeypatch.setattr(bs.build, "build_skill", boom)

    rc = bs.main([])

    assert rc == 1
    err = capsys.readouterr().err
    assert "validation failed" in err
    assert "bad frontmatter" in err
