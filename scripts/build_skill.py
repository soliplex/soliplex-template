#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-skills>=0.2.2"]
# ///
"""Assemble and validate the soliplex-template skill into dist/.

Thin wrapper over ``soliplex_skills.build.build_skill``:

- copies ``skills/soliplex-template/`` to ``dist/soliplex-template/``
- stamps ``SKILL.md`` with the source commit (``--commit``, default git HEAD)
- validates with the ``skills-ref`` library.

Packaging is the CI workflow's job; ``dist/`` is gitignored.

    uv run scripts/build_skill.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from soliplex_skills import build

SKILL_NAME = "soliplex-template"
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_DIR / "skills"
DIST = REPO_DIR / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble + validate the skill."
    )
    parser.add_argument(
        "--commit",
        help="Commit to stamp into SKILL.md metadata (default: git HEAD).",
    )
    args = parser.parse_args(argv)

    try:
        out = build.build_skill(
            SKILL_NAME, src=SKILLS_DIR, dist=DIST, commit=args.commit
        )
    except (build.SkillNotFound, build.ValidationFailed) as exc:
        print(f"build_skill: error: {exc}", file=sys.stderr)
        return 1

    print(f"built & validated: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
