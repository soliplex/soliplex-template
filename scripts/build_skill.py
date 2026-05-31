#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["skills-ref"]
# ///
"""Assemble and validate the soliplex-project-generator skill into dist/.

Copies the tracked source under ``skill/`` into the published skill directory:

    dist/soliplex-project-generator/

and validates it with the agent-skills reference tool (``skills-ref`` package,
``agentskills`` CLI). Packaging into release assets (tarball/zip) is the CI
workflow's job (see .github/workflows/build-skill.yaml), which writes those
under ``dist/`` too. ``dist/`` is gitignored.

Run with uv (provisions the validator automatically):

    uv run scripts/build_skill.py

or, without uv (falls back to ``uvx --from skills-ref agentskills``):

    python3 scripts/build_skill.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "soliplex-project-generator"
REPO_DIR = Path(__file__).resolve().parent.parent
SRC = REPO_DIR / "skill"
DIST = REPO_DIR / "dist"
OUT = DIST / SKILL_NAME


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"build_skill: error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def validator_cmd() -> list[str]:
    """Resolve how to invoke the agent-skills validator.

    Prefer the ``agentskills`` executable on PATH (present when this script is
    run via ``uv run``, which installs the PEP 723 ``skills-ref`` dependency);
    otherwise fall back to ``uvx --from skills-ref agentskills``.
    """
    exe = shutil.which("agentskills")
    if exe:
        return [exe, "validate"]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "--from", "skills-ref", "agentskills", "validate"]
    die("cannot find the agent-skills validator; install 'skills-ref' "
        "(pip install skills-ref) or run this script with 'uv run'")


def main() -> int:
    if not SRC.is_dir():
        die(f"source dir not found: {SRC}")
    if not (SRC / "SKILL.md").is_file():
        die(f"missing {SRC / 'SKILL.md'}")

    # Assemble dist/<skill name>/ from the skill source.
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SRC, OUT)

    # Validate the assembled skill (directory name must match the frontmatter
    # 'name', required fields present, etc.). Fail the build on any error.
    cmd = validator_cmd() + [str(OUT)]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die("skill validation failed")

    print(f"built & validated: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
