#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Extract the soliplex / ``haiku.rag-slim`` pairing table (PEP 723 shim).

Walks the release tags of a soliplex checkout and, for each, reads the
``haiku.rag-slim`` pin it declares in the root ``pyproject.toml`` -- the
same ``Requires-Dist`` line published on PyPI when the release is built.
The ingester tag of a generated stack must stay inside that range (the
ingester writes the LanceDB the backend reads), so the table is the
documentation of the safe pairing boundary.

Writes the table where the tooling and the docs consume it:

- ``docs/getting-started/includes/haiku-rag-pairings.md`` (rendered table,
  ``--8<--``-included by ``docs/getting-started/upgrading-stack.md``)
- ``skills/soliplex-template/references/version_pairings.toml``
  (machine-readable; ships with every ``template-skill-*`` release)

Run it from this repo after a new soliplex release lands::

    uv run scripts/extract_version_pairings.py --repo /path/to/soliplex

Overrides: ``--markdown PATH`` / ``--toml PATH``. Two-part tags (e.g.
``v0.85``) are retargeted by upstream to the latest patch of their series,
so their rows carry the newest patch's pin as of the run.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys
import tomllib

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_MARKDOWN = (
    REPO_DIR
    / "docs"
    / "getting-started"
    / "includes"
    / "haiku-rag-pairings.md"
)
DEFAULT_TOML = (
    REPO_DIR
    / "skills"
    / "soliplex-template"
    / "references"
    / "version_pairings.toml"
)

_TAG_GLOB = "v[0-9]*"
_TAG_RE = re.compile(r"^v(\d+(?:\.\d+)*)$")

_PIN = "haiku.rag-slim"


class PairingsError(Exception):
    """Base error extracting the pairings"""


class NoReleaseTags(PairingsError):
    def __init__(self, repo: pathlib.Path):
        self.repo = repo
        super().__init__(f"no release tags found in '{repo}'")


class TagsUnavailable(PairingsError):
    def __init__(self, repo: pathlib.Path, exc):
        self.repo, self.exc = repo, exc
        super().__init__(f"reading tags from '{repo}': {exc.stderr.strip()}")


class PyProjectUnavailable(PairingsError):
    def __init__(self, tag: str, exc):
        self.tag, self.exc = tag, exc
        super().__init__(
            f"{tag}: reading 'pyproject.toml': {exc.stderr.strip()}"
        )


class PyProjectInvalid(PairingsError):
    def __init__(self, tag: str, exc):
        self.tag, self.exc = tag, exc
        super().__init__(f"{tag}: 'pyproject.toml' is not valid TOML: {exc}")


class NoSlimPins(PairingsError):
    def __init__(self, repo: pathlib.Path):
        self.repo = repo
        super().__init__(
            f"no release of '{repo}' declares a '{_PIN}' dependency"
        )


def _version_key(version: str) -> tuple[int, ...]:
    """Numeric sort key for a dotted release version, ``0.101 < 0.102``."""
    return tuple(int(part) for part in version.split("."))


def list_release_tags(repo: pathlib.Path) -> list[str]:
    """List the repo's ``v<version>`` release tags, oldest first."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "tag", "--list", _TAG_GLOB],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TagsUnavailable(repo, exc) from exc
    tags = [tag for tag in proc.stdout.split() if _TAG_RE.fullmatch(tag)]
    if not tags:
        raise NoReleaseTags(repo)

    return sorted(tags, key=lambda tag: _version_key(tag.removeprefix("v")))


def _read_pin_at_tag(repo: pathlib.Path, tag: str) -> str | None:
    """The ``haiku.rag-slim`` pin the release tagged ``tag`` declares.

    ``None`` for releases predating the ``haiku.rag-slim`` split, which
    still pinned the full ``haiku.rag`` distribution (or nothing).
    """

    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "show", f"{tag}:pyproject.toml"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise PyProjectUnavailable(tag, exc) from exc

    try:
        data = tomllib.loads(proc.stdout)
    except tomllib.TOMLDecodeError as exc:
        raise PyProjectInvalid(tag, exc) from exc

    for dep in data.get("project", {}).get("dependencies", []):
        if str(dep).startswith(_PIN):
            return re.sub(r"\s+", " ", str(dep)).removeprefix(_PIN).strip()

    return None


def collect_pairings(
    repo: pathlib.Path,
) -> tuple[list[dict[str, str]], list[str]]:
    """Pair each tagged release with the ``haiku.rag-slim`` pin it declares.

    Returns the pairings (newest first) plus the list of skipped tags:
    releases predating the ``haiku.rag-slim`` split, which declare no pin
    to record.
    """
    pairings = []
    skippable: list[str] = []

    for tag in list_release_tags(repo):
        pin = _read_pin_at_tag(repo, tag)
        if pin is None:
            skippable.append(tag)
        else:
            pairings.append(
                {
                    "version": tag.removeprefix("v"),
                    "haiku_rag": pin,
                }
            )

    if not pairings:
        raise NoSlimPins(repo)

    # The published tables read newest first.
    pairings.reverse()
    skippable.reverse()

    return pairings, skippable


def _render_markdown(rows: list[dict[str, str]]) -> str:
    """Render the pairings as the include file's markdown table."""
    lines = [
        "<!-- Generated by 'scripts/extract_version_pairings.py' from the",
        "soliplex repo's release tags; do not edit. -->",
        "",
        "| soliplex tag | requires `haiku.rag-slim` |",
        "|--------------|--------------------------------|",
    ]
    lines.extend(
        f"| v{row['version']} | `{row['haiku_rag']}` |" for row in rows
    )
    return "\n".join(lines) + "\n"


def _render_toml(rows: list[dict[str, str]], extracted: str) -> str:
    """Render the pairings as the skill's machine-readable TOML table."""
    lines = [
        "# Generated by 'scripts/extract_version_pairings.py' from the",
        "# soliplex repo's release tags; do not edit.",
        "#",
        "# A two-part tag (e.g. 'v0.85') names the series' *first* release:",
        "# later patches (v0.85.1 ...) tag their own commits and may raise",
        "# the pin. For the moving compose image tag ('0.85'), the effective",
        "# pairing is the newest patch's pin, as of this run.",
        f'extracted = "{extracted}"',
        "",
    ]
    for row in rows:
        lines.extend(
            (
                "[[pairings]]",
                f'version = "{row["version"]}"',
                f'haiku_rag = "{row["haiku_rag"]}"',
                "",
            )
        )
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract the soliplex / 'haiku.rag-slim' pairing table."
    )
    parser.add_argument(
        "--repo",
        type=pathlib.Path,
        required=True,
        help="Checkout of the soliplex repo to read the release tags from.",
    )
    parser.add_argument(
        "--markdown",
        type=pathlib.Path,
        default=DEFAULT_MARKDOWN,
        help="Generated markdown table (default: %(default)s).",
    )
    parser.add_argument(
        "--toml",
        type=pathlib.Path,
        default=DEFAULT_TOML,
        help="Generated TOML table (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    rows, skipped = collect_pairings(args.repo)
    extracted = datetime.date.today().isoformat()

    for path, content in (
        (args.markdown, _render_markdown(rows)),
        (args.toml, _render_toml(rows, extracted)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    print(f"extracted {len(rows)} pairings as of {extracted}")
    if skipped:
        print(
            f"  skipped {len(skipped)} pre-'{_PIN}' releases "
            f"({skipped[0]} .. {skipped[-1]})"
        )
    print(f"  markdown: {args.markdown}")
    print(f"  toml:     {args.toml}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except PairingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
