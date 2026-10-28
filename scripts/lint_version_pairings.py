#!/usr/bin/env python
"""Check the generator's default version pins against the published tables.

The skill's generator defaults 'soliplex_image_tag' and
'haiku_rag_version' to a release pair; the published pairing tables --
'skills/soliplex-template/references/version_pairings.toml' and
'docs/getting-started/includes/haiku-rag-pairings.md' -- must carry the
matching row, with the haiku default as the row's '>= ' floor and the
two tables agreeing. A bump of either default without regenerating the
tables (issue #229) makes the generator produce a stack that breaks at
run time; this check has teeth on every platform, in CI-free pre-commit
hooks.

A release missing from the tables -- or a default that resolves only
through a two-part tag, which no row covers -- is a finding, as is any
disagreement within either table's copy of that row.

Exits non-zero when any drift is found.

Usage::

    python scripts/lint_version_pairings.py
    python scripts/lint_version_pairings.py --verbose
    python scripts/lint_version_pairings.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys
import tomllib
from typing import NamedTuple

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
GENERATOR_PATH = (
    REPO_DIR
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "generate_soliplex_project.py"
)
TOML_PATH = (
    REPO_DIR
    / "skills"
    / "soliplex-template"
    / "references"
    / "version_pairings.toml"
)
MD_PATH = (
    REPO_DIR
    / "docs"
    / "getting-started"
    / "includes"
    / "haiku-rag-pairings.md"
)


class Finding(NamedTuple):
    """One way the published tables disagree with the generator."""

    label: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.label}: {self.kind}: {self.detail}"


def load_defaults(generator_path: pathlib.Path) -> dict[str, str]:
    """The generator's default version pins, by artifact-facing name."""
    spec = importlib.util.spec_from_file_location(
        "generate_soliplex_project", generator_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults = module.DEFAULTS

    return {
        "image_tag": defaults["soliplex_image_tag"],
        "haiku_rag": defaults["haiku_rag_version"],
    }


def toml_rows(path: pathlib.Path) -> dict[str, str]:
    """The TOML table, as {version: haiku_rag spec}."""
    with path.open("rb") as fp:
        data = tomllib.load(fp)

    return {row["version"]: row["haiku_rag"] for row in data["pairings"]}


_MD_ROW_RE = re.compile(r"^\| (v\S+) \| `([^`]+)` \|", re.MULTILINE)


def md_specs(path: pathlib.Path) -> dict[str, str]:
    """The docs-include table, as {version: haiku_rag spec}."""
    text = path.read_text(encoding="utf-8")
    pairs = _MD_ROW_RE.findall(text)

    return {version.removeprefix("v"): spec for version, spec in pairs}


def resolve_row(rows: dict[str, str], version: str) -> str | None:
    """Find the row for a release: exact, else its x.y.0 -> x.y ancestor.

    Release x.y.z tagged only with the series' two-part tag x.y carries
    that row's pin; a miss means the release is not covered yet.
    """
    if version in rows:
        return rows[version]

    return rows.get(re.sub(r"\.0$", "", version))


_FLOOR_RE = re.compile(r">=\s*(\d+(?:\.\d+)*)")


def floor(spec: str) -> str | None:
    """The spec's >=-anchored floor; None when it has none."""
    match = _FLOOR_RE.search(spec)

    return match.group(1) if match else None


def check(
    defaults: dict[str, str],
    rows: dict[str, str],
    specs: dict[str, str],
) -> list[Finding]:
    """Findings for one default version pair against both tables."""
    image_tag = defaults["image_tag"]
    haiku_rag = defaults["haiku_rag"]
    findings: list[Finding] = []

    toml_row = resolve_row(rows, image_tag)
    doc_row = resolve_row(specs, image_tag)

    if toml_row is None:
        findings.append(
            Finding(
                "version_pairings.toml",
                "no pairing row",
                f"no row covers default soliplex_image_tag {image_tag!r}",
            )
        )
    else:
        matched = floor(toml_row)

        if matched is None:
            findings.append(
                Finding(
                    "version_pairings.toml",
                    "no version specifier",
                    f"row for {image_tag!r} is {toml_row!r}",
                )
            )
        elif matched != haiku_rag:
            findings.append(
                Finding(
                    "version_pairings.toml",
                    "floor mismatch",
                    f"row floor {matched!r} != default haiku_rag_version "
                    f"{haiku_rag!r}",
                )
            )

    if doc_row is None:
        findings.append(
            Finding(
                "haiku-rag-pairings.md",
                "no pairing row",
                f"no row covers default soliplex_image_tag {image_tag!r}",
            )
        )
    elif toml_row is not None and doc_row != toml_row:
        findings.append(
            Finding(
                "haiku-rag-pairings.md",
                "row mismatch",
                f"docs row {doc_row!r} != toml row {toml_row!r} "
                f"for {image_tag!r}",
            )
        )

    return findings


def report(
    findings: list[Finding],
    rows: dict[str, str],
    specs: dict[str, str],
    verbose: bool,
) -> None:
    """Print findings to stderr, both tables when 'verbose'."""
    for finding in findings:
        print(finding, file=sys.stderr)

    tail = (
        "; regenerate both tables by running "
        "'scripts/extract_version_pairings.py --repo <soliplex checkout>' "
        "and committing the result."
    )
    print(
        f"\nlint_version_pairings: {len(findings)} drift finding(s){tail}",
        file=sys.stderr,
    )
    if verbose:
        for label, table in (
            ("version_pairings.toml", rows),
            ("haiku-rag-pairings.md", specs),
        ):
            print(f"{label} rows:", file=sys.stderr)
            for version, spec in table.items():
                print(f"  {version}: {spec}", file=sys.stderr)


# Every check() must-catch scenario, with its exact findings.
_SELF_TEST_CASES: list[tuple[str, dict, dict, dict, list[Finding]]] = [
    (
        "defaults agree with both tables",
        {"image_tag": "0.88.0", "haiku_rag": "0.105.0"},
        {"0.88": ">= 0.105.0, < 0.106"},
        {"0.88": ">= 0.105.0, < 0.106"},
        [],
    ),
    (
        "image tag beyond both tables",
        {"image_tag": "999.999.999", "haiku_rag": "0.105.0"},
        {"0.88": ">= 0.105.0, < 0.106"},
        {"0.88": ">= 0.105.0, < 0.106"},
        [
            Finding(
                "version_pairings.toml",
                "no pairing row",
                "no row covers default soliplex_image_tag '999.999.999'",
            ),
            Finding(
                "haiku-rag-pairings.md",
                "no pairing row",
                "no row covers default soliplex_image_tag '999.999.999'",
            ),
        ],
    ),
    (
        "haiku default off the row floor",
        {"image_tag": "0.88.0", "haiku_rag": "0.104.0"},
        {"0.88": ">= 0.105.0, < 0.106"},
        {"0.88": ">= 0.105.0, < 0.106"},
        [
            Finding(
                "version_pairings.toml",
                "floor mismatch",
                "row floor '0.105.0' != default haiku_rag_version '0.104.0'",
            ),
        ],
    ),
    (
        "docs include disagrees with the toml",
        {"image_tag": "0.88.0", "haiku_rag": "0.105.0"},
        {"0.88": ">= 0.105.0, < 0.106"},
        {"0.88": ">= 0.104.0, < 0.105"},
        [
            Finding(
                "haiku-rag-pairings.md",
                "row mismatch",
                "docs row '>= 0.104.0, < 0.105' != toml row "
                "'>= 0.105.0, < 0.106' for '0.88.0'",
            ),
        ],
    ),
    (
        "row without a version specifier",
        {"image_tag": "0.88.0", "haiku_rag": "0.105.0"},
        {"0.88": "< 0.106"},
        {"0.88": "< 0.106"},
        [
            Finding(
                "version_pairings.toml",
                "no version specifier",
                "row for '0.88.0' is '< 0.106'",
            ),
            # The unparseable row still equals its toml twin, so the
            # docs include stays quiet.
        ],
    ),
    (
        "two-part tag covers the series' first release",
        {"image_tag": "0.87.0", "haiku_rag": "0.101.0"},
        {"0.87": ">= 0.101.0, < 0.102"},
        {"0.87": ">= 0.101.0, < 0.102"},
        [],
    ),
]


def self_test(verbose: bool) -> int:
    """Check that the lint bites -- rather than passing by finding nothing.

    Returns '0' when every embedded scenario yields exactly its expected
    findings, '1' otherwise.
    """
    mismatches: list[str] = []

    for name, defaults, rows, specs, expected in _SELF_TEST_CASES:
        found = check(defaults, rows, specs)

        if found == expected:
            if verbose:
                print(
                    f"lint_version_pairings: {name}: passed", file=sys.stderr
                )
            continue

        missed = [one for one in expected if one not in found]
        spurious = [one for one in found if one not in expected]
        for finding in missed:
            print(
                f"lint_version_pairings: self-test {name!r}: missed {finding}",
                file=sys.stderr,
            )
        for finding in spurious:
            print(
                f"lint_version_pairings: self-test {name!r}: "
                f"spurious {finding}",
                file=sys.stderr,
            )
        mismatches.append(name)

    if not mismatches:
        print(
            f"lint_version_pairings: self-test passed "
            f"({len(_SELF_TEST_CASES)} scenarios).",
            file=sys.stderr,
        )
        return 0

    print(
        "\nlint_version_pairings: self-test FAILED -- the check no "
        "longer reports what it is supposed to report.",
        file=sys.stderr,
    )
    return 1


def lint(
    generator_path: pathlib.Path,
    toml_path: pathlib.Path,
    md_path: pathlib.Path,
    verbose: bool,
) -> int:
    """Check the artifacts at the given paths; 0 when clean, 1 when not."""
    defaults = load_defaults(generator_path)
    rows = toml_rows(toml_path)
    specs = md_specs(md_path)
    findings = check(defaults, rows, specs)

    if findings:
        report(findings, rows, specs, verbose)
        return 1

    if verbose:
        print(
            f"lint_version_pairings: {defaults['image_tag']!r} pairs with "
            f"{defaults['haiku_rag']!r} in both published tables.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Dump what was loaded from both tables.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify the check against embedded scenarios, then exit "
        "without reading any artifact.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.verbose)

    return lint(GENERATOR_PATH, TOML_PATH, MD_PATH, args.verbose)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
