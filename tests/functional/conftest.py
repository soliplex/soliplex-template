"""Shared fixtures for the functional generator tests.

The embedded template under ``skills/soliplex-template/assets/template/``
is a build artifact (gitignored -- issue #135), generated from the repo
exemplars by ``scripts/refresh_skill_template.py``.

Regenerate it once per session before any test drives the generator,
mirroring what ``build_skill.py`` does at build time,
and ensuring the functests exercise the *current* exemplars.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REFRESH = _REPO_ROOT / "scripts" / "refresh_skill_template.py"


@pytest.fixture(scope="session")
def refresh_embedded_template():
    """(Re)generate the gitignored embedded template before generating.

    Requested explicitly by the fixtures/tests that drive the generator, so the
    dependency is visible at their signatures (no ``autouse``).
    """
    result = subprocess.run(
        [sys.executable, str(_REFRESH)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"refresh_skill_template failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
