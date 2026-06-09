"""Drift guard for the skill's shortcut-spelling map, ``assets/aliases.json``.

The ``soliplex-template`` skill resolves inline ``key=value`` generation
shortcuts (issue #92) against ``assets/aliases.json`` rather than a vocabulary
spelled out in SKILL.md prose. Nothing imports that file at runtime, so these
tests are its contract: every alias target and group member must name a real
``generate_soliplex_project.py`` parameter (so the map can't silently drift
from ``DEFAULTS``), and no secret parameter may be reachable inline.

The generator ships inside the skill and is not an importable package, so it is
loaded here by file path via ``importlib.util`` -- the same seam the sibling
``test_generate_soliplex_project.py`` uses.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO_ROOT / "skills" / "soliplex-template"

_spec = importlib.util.spec_from_file_location(
    "generate_soliplex_project",
    _SKILL_DIR / "scripts" / "generate_soliplex_project.py",
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# ``where`` / ``output_dir`` is the one alias target that is NOT a generator
# parameter: it routes to the ``--out`` argument (the target directory).
_OUT_SPECIAL = "output_dir"

_ALIASES_PATH = _SKILL_DIR / "assets" / "aliases.json"
_ALIASES = json.loads(_ALIASES_PATH.read_text())


def test_aliases_json_parses_and_has_expected_top_level_keys():
    text = _ALIASES_PATH.read_text()

    loaded = json.loads(text)

    assert "aliases" in loaded
    assert "groups" in loaded
    # Any key beyond aliases/groups must be a leading-underscore comment field
    # (JSON has no comments), never a stray vocabulary section.
    extra = set(loaded) - {"aliases", "groups"}
    assert all(key.startswith("_") for key in extra)


@pytest.mark.parametrize(
    "token,target",
    sorted(_ALIASES["aliases"].items()),
)
def test_alias_target_is_a_known_parameter(token, target):
    known = target in gen.DEFAULTS or target == _OUT_SPECIAL

    assert known, f"alias {token!r} -> unknown target {target!r}"


@pytest.mark.parametrize(
    "group,member",
    sorted(
        (group, member)
        for group, members in _ALIASES["groups"].items()
        for member in members
    ),
)
def test_group_member_is_a_default_key(group, member):
    assert member in gen.DEFAULTS, (
        f"group {group!r} member {member!r} is not a DEFAULTS key"
    )


@pytest.mark.parametrize("secret", sorted(gen._SENSITIVE_PARAMS))
def test_secret_is_not_reachable_inline(secret):
    alias_targets = set(_ALIASES["aliases"].values())
    group_members = {
        member for members in _ALIASES["groups"].values() for member in members
    }

    reachable = secret in alias_targets or secret in group_members

    assert not reachable, f"secret {secret!r} must not be reachable inline"
