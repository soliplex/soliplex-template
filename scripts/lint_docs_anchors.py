#!/usr/bin/env python
"""Check that every '#fragment' link between doc pages names a real heading.

A link such as '[Using the skill](using-the-skill.md#add-a-room)' only
works while the target page still has a heading whose id is 'add-a-room';
renaming the heading breaks it silently, since the docs build does not
validate anchors. This check computes each page's heading ids the way the
'toc' extension does -- python-markdown's 'slugify', '_1' / '_2' suffixes
for repeats, and an explicit '{ #id }' attribute winning -- plus the 'id'
of any raw HTML element (e.g. '<a id="appendix"></a>'), and reports every
fragment link under 'docs/' that names a missing page or heading.

Links to other sites, links without a fragment, and links inside fenced
code blocks or inline code spans are not checked.
'docs/getting-started/includes/' holds verbatim copies of stack files and
is skipped.

Exits non-zero when any broken link is found.

Usage::

    python scripts/lint_docs_anchors.py
    python scripts/lint_docs_anchors.py --verbose
    python scripts/lint_docs_anchors.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import posixpath
import re
import sys
from typing import NamedTuple

from markdown.extensions import toc

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_DIR / "docs"
SKIPPED_PREFIXES = ("getting-started/includes/",)


class Finding(NamedTuple):
    """One fragment link that does not resolve."""

    page: str
    line: int
    link: str
    detail: str

    def __str__(self) -> str:
        return f"docs/{self.page}:{self.line}: {self.link}: {self.detail}"


_FRONT_MATTER_RE = re.compile(r"\A---\n.*?^---\n", re.DOTALL | re.MULTILINE)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
_ATTR_LIST_RE = re.compile(r"\s*\{([^}]*)\}\s*$")
_ATTR_ID_RE = re.compile(r"#([\w-]+)")
_HTML_ID_RE = re.compile(r"<[a-zA-Z][^>]*\sid=\"([\w-]+)\"")
_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SHORTCODE_RE = re.compile(r":[a-z0-9-]+:")
_LINK_RE = re.compile(r"\]\(([^)\s#]*)#([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:")
_CODE_SPAN_RE = re.compile(r"(`+).+?\1")


def prose_lines(text: str):
    """Yield '(line number, line)' for each line outside fenced code.

    YAML front matter is skipped, but still counted in the numbering.
    """
    front_matter = _FRONT_MATTER_RE.match(text)
    skip = front_matter.group(0).count("\n") if front_matter else 0
    in_fence = False

    for number, line in enumerate(text.splitlines(), start=1):
        if number <= skip:
            continue

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue

        if not in_fence:
            yield number, line


def heading_ids(text: str) -> set[str]:
    """The ids a link can target: headings, as 'toc' names them, and HTML."""
    ids: set[str] = set()

    for _, line in prose_lines(text):
        ids.update(_HTML_ID_RE.findall(line))
        heading = _HEADING_RE.match(line)

        if heading is None:
            continue

        title = heading.group(1)
        attrs = _ATTR_LIST_RE.search(title)

        if attrs is not None:
            explicit = _ATTR_ID_RE.search(attrs.group(1))

            if explicit is not None:
                ids.add(explicit.group(1))
                continue

            title = title[: attrs.start()]

        title = _INLINE_LINK_RE.sub(r"\1", title)
        title = _SHORTCODE_RE.sub("", title)
        slug = toc.slugify(title, "-")
        candidate = slug
        suffix = 0

        while candidate in ids:
            suffix += 1
            candidate = f"{slug}_{suffix}"

        ids.add(candidate)

    return ids


def fragment_links(text: str):
    """Yield '(line number, target, fragment)' for each local fragment link.

    'target' is '' for a link within the same page.
    """
    for number, line in prose_lines(text):
        for match in _LINK_RE.finditer(_CODE_SPAN_RE.sub("", line)):
            target, fragment = match.groups()

            if _SCHEME_RE.match(target):
                continue

            yield number, target, fragment


def check(pages: dict[str, str]) -> list[Finding]:
    """Findings for every fragment link in 'pages' ({docs path: text})."""
    ids = {path: heading_ids(text) for path, text in pages.items()}
    findings: list[Finding] = []

    for path, text in pages.items():
        for number, target, fragment in fragment_links(text):
            link = f"{target}#{fragment}"
            resolved = (
                posixpath.normpath(
                    posixpath.join(posixpath.dirname(path), target)
                )
                if target
                else path
            )

            if resolved not in ids:
                findings.append(
                    Finding(path, number, link, f"no page {resolved!r}")
                )
            elif fragment not in ids[resolved]:
                findings.append(
                    Finding(
                        path,
                        number,
                        link,
                        f"no heading with id {fragment!r} in {resolved!r}",
                    )
                )

    return findings


def load_pages(docs_dir: pathlib.Path) -> dict[str, str]:
    """Every checked page under 'docs_dir', as {docs path: text}."""
    pages: dict[str, str] = {}

    for page in sorted(docs_dir.rglob("*.md")):
        path = page.relative_to(docs_dir).as_posix()

        if path.startswith(SKIPPED_PREFIXES):
            continue

        pages[path] = page.read_text(encoding="utf-8")

    return pages


# Every check() must-catch scenario, with its exact findings.
_SELF_TEST_CASES: list[tuple[str, dict[str, str], list[Finding]]] = [
    (
        "link to a heading on another page",
        {
            "a.md": "# A\n\nSee [b](sub/b.md#second-part).\n",
            "sub/b.md": "# B\n\n## Second part\n",
        },
        [],
    ),
    (
        "link up a directory and within the page",
        {
            "sub/b.md": "# B\n\n## Here\n\n[a](../a.md#a), [here](#here)\n",
            "a.md": "# A\n",
        },
        [],
    ),
    (
        "renamed heading",
        {
            "a.md": "# A\n\nSee [b](b.md#old-name).\n",
            "b.md": "# B\n\n## New name\n",
        },
        [
            Finding(
                "a.md",
                3,
                "b.md#old-name",
                "no heading with id 'old-name' in 'b.md'",
            ),
        ],
    ),
    (
        "missing page",
        {"a.md": "# A\n\nSee [c](c.md#top).\n"},
        [Finding("a.md", 3, "c.md#top", "no page 'c.md'")],
    ),
    (
        "headings shaped like the docs' own",
        {
            "a.md": (
                "---\nicon: lucide/wand-sparkles\n---\n\n"
                "# Migrating to the `src/` project layout\n\n"
                "## 1. Install the skills\n\n"
                "## Option 1 — relay [Ollama](https://ollama.com)\n\n"
                "## Custom { #my-id }\n\n"
                "## Classed { .wide }\n\n"
                "## Twice\n\n## Twice\n\n"
                '    <a id="appendix"></a>\n\n'
                "[a](#migrating-to-the-src-project-layout)\n"
                "[b](#1-install-the-skills) [c](#option-1-relay-ollama)\n"
                "[d](#my-id) [e](#classed) [f](#twice) [g](#twice_1)\n"
                "[h](#appendix)\n"
            ),
        },
        [],
    ),
    (
        "code, other sites, and titled links",
        {
            "a.md": (
                "# A\n\n```text\n[x](b.md#nowhere)\n# Not a heading\n```\n\n"
                "[y](https://example.com/b.md#nowhere)\n"
                '[z](#a "a title")\n'
                "Write `[w](b.md#nowhere)` or ``[v](`b.md#nowhere`)``.\n"
            ),
        },
        [],
    ),
]


def self_test(verbose: bool) -> int:
    """Check that the lint bites -- rather than passing by finding nothing.

    Returns '0' when every embedded scenario yields exactly its expected
    findings, '1' otherwise.
    """
    mismatches: list[str] = []

    for name, pages, expected in _SELF_TEST_CASES:
        found = check(pages)

        if found == expected:
            if verbose:
                print(f"lint_docs_anchors: {name}: passed", file=sys.stderr)
            continue

        missed = [one for one in expected if one not in found]
        spurious = [one for one in found if one not in expected]
        for finding in missed:
            print(
                f"lint_docs_anchors: self-test {name!r}: missed {finding}",
                file=sys.stderr,
            )
        for finding in spurious:
            print(
                f"lint_docs_anchors: self-test {name!r}: spurious {finding}",
                file=sys.stderr,
            )
        mismatches.append(name)

    if not mismatches:
        print(
            f"lint_docs_anchors: self-test passed "
            f"({len(_SELF_TEST_CASES)} scenarios).",
            file=sys.stderr,
        )
        return 0

    print(
        "\nlint_docs_anchors: self-test FAILED -- the check no longer "
        "reports what it is supposed to report.",
        file=sys.stderr,
    )
    return 1


def lint(docs_dir: pathlib.Path, verbose: bool) -> int:
    """Check the pages under 'docs_dir'; 0 when clean, 1 when not."""
    pages = load_pages(docs_dir)
    findings = check(pages)

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"\nlint_docs_anchors: {len(findings)} broken fragment link(s); "
            "fix the link, or the heading it names.",
            file=sys.stderr,
        )
        return 1

    if verbose:
        count = sum(1 for text in pages.values() for _ in fragment_links(text))
        print(
            f"lint_docs_anchors: {count} fragment link(s) in "
            f"{len(pages)} page(s) resolve.",
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
        help="Report how many links were checked.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify the check against embedded scenarios, then exit "
        "without reading any page.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.verbose)

    return lint(DOCS_DIR, args.verbose)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
