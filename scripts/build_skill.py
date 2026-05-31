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

The assembled SKILL.md is stamped with ``metadata.source_commit`` (from
``--commit``, default git HEAD) so the bundled ``scripts/skill_versions.py``
can tell which published build is installed.

Run with uv (provisions the validator automatically):

    uv run scripts/build_skill.py

or, without uv (falls back to ``uvx --from skills-ref agentskills``):

    python3 scripts/build_skill.py
"""

from __future__ import annotations

import argparse
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


def git_head_commit() -> str | None:
    """Return the repo's current commit SHA, or None if unavailable."""
    if shutil.which("git") is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return None
    return out.stdout.strip() or None


def stamp_source_commit(skill_md: Path, commit: str) -> None:
    """Record ``metadata.source_commit: "<commit>"`` in SKILL.md frontmatter.

    Mirrors the soliplex-docs skill so scripts/skill_versions.py can identify
    the installed build. Inserts under an existing ``metadata:`` block if one
    is present, else appends a new block before the closing frontmatter fence.
    """
    lines = skill_md.read_text(encoding="utf-8").split("\n")
    fences = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) < 2:
        die(f"{skill_md} has no YAML frontmatter to stamp")
    start, close = fences[0], fences[1]
    front = lines[start + 1:close]
    if any(line.strip().startswith("source_commit:") for line in front):
        return  # already stamped
    entry = f'  source_commit: "{commit}"'
    meta_idx = next(
        (i for i, line in enumerate(front) if line.strip() == "metadata:"), None
    )
    if meta_idx is not None:
        front.insert(meta_idx + 1, entry)
    else:
        front += ["metadata:", entry]
    lines[start + 1:close] = front
    skill_md.write_text("\n".join(lines), encoding="utf-8")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble + validate the skill.")
    parser.add_argument(
        "--commit",
        help="Source commit to stamp into SKILL.md metadata (default: git HEAD).",
    )
    args = parser.parse_args(argv)

    if not SRC.is_dir():
        die(f"source dir not found: {SRC}")
    if not (SRC / "SKILL.md").is_file():
        die(f"missing {SRC / 'SKILL.md'}")

    # Assemble dist/<skill name>/ from the skill source.
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SRC, OUT)

    # Stamp the build's source commit so skill_versions.py can detect it.
    commit = args.commit or git_head_commit()
    if commit:
        stamp_source_commit(OUT / "SKILL.md", commit)
    else:
        print("build_skill: warning: no commit available; SKILL.md left unstamped",
              file=sys.stderr)

    # Validate the assembled skill (directory name must match the frontmatter
    # 'name', required fields present, etc.). Fail the build on any error.
    cmd = validator_cmd() + [str(OUT)]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die("skill validation failed")

    print(f"built & validated: {OUT}" + (f" (commit {commit[:7]})" if commit else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
