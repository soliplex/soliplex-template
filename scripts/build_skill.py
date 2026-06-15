#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-skills>=0.5"]
# ///
"""Assemble and validate the soliplex-template skill into dist/.

First regenerates the embedded template tree (a build artifact, gitignored --
see issue #135), then is a thin wrapper over
``soliplex_skills.build.build_skill``:

- runs ``scripts/refresh_skill_template.py`` to (re)generate
  ``skills/soliplex-template/assets/template/`` from the repo exemplars
- copies ``skills/soliplex-template/`` to ``dist/soliplex-template/``
- stamps ``SKILL.md`` with the source commit (``--commit``, default git HEAD)
- validates with the ``skills-ref`` library.

Packaging is the CI workflow's job; ``dist/`` is gitignored.

    uv run scripts/build_skill.py
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from soliplex_skills import build

SKILL_NAME = "soliplex-template"
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_DIR / "skills"
DIST = REPO_DIR / "dist"
REFRESH_SCRIPT = REPO_DIR / "scripts" / "refresh_skill_template.py"


def refresh_template() -> None:
    """Regenerate the gitignored embedded template before assembling.

    The template under ``skills/soliplex-template/assets/template/`` is not
    committed (issue #135), so generate it fresh here. Run via ``uv run`` so
    the refresh script's own PEP 723 dependency (mako) is resolved.
    """
    subprocess.run(["uv", "run", str(REFRESH_SCRIPT)], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble + validate the skill."
    )
    parser.add_argument(
        "--commit",
        help="Commit to stamp into SKILL.md metadata (default: git HEAD).",
    )
    parser.add_argument(
        "--version",
        help="Published version to stamp into SKILL.md (omit for rolling "
        "builds).",
    )
    parser.add_argument(
        "--date",
        help="Build date (ISO YYYY-MM-DD) to stamp as 'generated' (default: "
        "today).",
    )
    args = parser.parse_args(argv)

    refresh_template()

    try:
        out = build.build_skill(
            SKILL_NAME,
            src=SKILLS_DIR,
            dist=DIST,
            commit=args.commit,
            version=args.version,
            generated=args.date,
        )
    except (build.SkillNotFound, build.ValidationFailed) as exc:
        print(f"build_skill: error: {exc}", file=sys.stderr)
        return 1

    print(f"built & validated: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
