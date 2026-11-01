"""Unit tests for the repo-level ``scripts/lint_docs_anchors.py``.

The script lints the docs site's '#fragment' links against the heading
ids of the pages they name, in pre-commit; these tests drive its helpers
and pipeline with synthetic pages, written into ``tmp_path`` where a
directory is needed -- no page in the repo is read. The check itself
bites at hook time, plus the script's own ``--self-test`` scenarios.

Each test is laid out in three blank-line-separated phases -- setup, then
the single call under test (the "act"), then the assertions -- and
performs that act exactly once.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts"
    / "lint_docs_anchors.py"
)
_spec = importlib.util.spec_from_file_location(
    "lint_docs_anchors", _MODULE_PATH
)
lint_anchors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_anchors)


def _docs_tree(tmp_path: pathlib.Path, pages: dict[str, str]) -> pathlib.Path:
    """Write 'pages' ({docs path: text}) under a 'docs' dir; return it."""
    docs_dir = tmp_path / "docs"

    for path, text in pages.items():
        page = docs_dir / path
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")

    return docs_dir


@pytest.mark.parametrize(
    "text, expected",
    [
        ("# A\n", [(1, "# A")]),
        ("---\nicon: lucide/x\n---\n# A\n", [(4, "# A")]),
        ("# A\n```\n# B\n```\nc\n", [(1, "# A"), (5, "c")]),
    ],
)
def test_prose_lines(text, expected) -> None:
    lines = list(lint_anchors.prose_lines(text))

    assert lines == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("# Plain title\n", {"plain-title"}),
        ("## Closed title ##\n", {"closed-title"}),
        ("## The `src/` layout\n", {"the-src-layout"}),
        ("## Custom { #my-id }\n", {"my-id"}),
        ("## Classed { .wide }\n", {"classed"}),
        ("## [Linked](x.md) text\n", {"linked-text"}),
        ("## :lucide-wand-sparkles: Icon\n", {"icon"}),
        (
            "## Twice\n\n## Twice\n\n## Twice\n",
            {"twice", "twice_1", "twice_2"},
        ),
        ('<a id="appendix"></a>\n', {"appendix"}),
        ("Not a heading\n", set()),
    ],
)
def test_heading_ids(text, expected) -> None:
    ids = lint_anchors.heading_ids(text)

    assert ids == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[a](b.md#c)\n", [(1, "b.md", "c")]),
        ("x\n[a](#c)\n", [(2, "", "c")]),
        ('[a](b.md#c "A title")\n', [(1, "b.md", "c")]),
        ("[a](b.md#c){ .skill-link }\n", [(1, "b.md", "c")]),
        ("[a](https://example.com/b.md#c)\n", []),
        ("[a](b.md)\n", []),
        ("`[a](b.md#c)` and [d](#e)\n", [(1, "", "e")]),
    ],
)
def test_fragment_links(text, expected) -> None:
    links = list(lint_anchors.fragment_links(text))

    assert links == expected


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case[0]) for case in lint_anchors._SELF_TEST_CASES],
)
def test_check_scenarios(case) -> None:
    _, pages, expected = case

    found = lint_anchors.check(pages)

    assert found == expected


def test_load_pages(tmp_path) -> None:
    docs_dir = _docs_tree(
        tmp_path,
        {
            "a.md": "# A\n",
            "sub/b.md": "# B\n",
            "getting-started/includes/c.md": "# C\n",
            "d.txt": "D\n",
        },
    )

    pages = lint_anchors.load_pages(docs_dir)

    assert pages == {"a.md": "# A\n", "sub/b.md": "# B\n"}


def test_lint_clean(tmp_path) -> None:
    docs_dir = _docs_tree(tmp_path, {"a.md": "# A\n\n[a](#a)\n"})

    status = lint_anchors.lint(docs_dir, False)

    assert status == 0


def test_lint_clean_verbose(tmp_path, capsys) -> None:
    docs_dir = _docs_tree(tmp_path, {"a.md": "# A\n\n[a](#a)\n"})

    status = lint_anchors.lint(docs_dir, True)

    captured = capsys.readouterr()

    assert status == 0
    assert "1 fragment link(s) in 1 page(s) resolve" in captured.err


def test_lint_broken(tmp_path, capsys) -> None:
    docs_dir = _docs_tree(tmp_path, {"a.md": "# A\n\n[b](b.md#b)\n"})

    status = lint_anchors.lint(docs_dir, False)

    captured = capsys.readouterr()

    assert status == 1
    assert "docs/a.md:3: b.md#b: no page 'b.md'" in captured.err
    assert "1 broken fragment link(s)" in captured.err


def test_main_self_test() -> None:
    status = lint_anchors.main(["--self-test"])

    assert status == 0


def test_main_self_test_verbose() -> None:
    status = lint_anchors.main(["--self-test", "--verbose"])

    assert status == 0


def test_self_test_failure(monkeypatch, capsys) -> None:
    cases = lint_anchors._SELF_TEST_CASES
    counter = {"n": 0}

    def _bites(pages):
        index = counter["n"]
        counter["n"] += 1
        expected = cases[index][2]

        if not expected:
            return [lint_anchors.Finding("x.md", 1, "y.md#z", "w")]

        return []

    monkeypatch.setattr(lint_anchors, "check", _bites)

    status = lint_anchors.self_test(False)

    captured = capsys.readouterr()

    assert status == 1
    assert "self-test FAILED" in captured.err
    assert "spurious" in captured.err
    assert "missed" in captured.err


def test_main_lint_invocation(tmp_path, monkeypatch) -> None:
    docs_dir = _docs_tree(tmp_path, {"a.md": "# A\n"})
    monkeypatch.setattr(lint_anchors, "DOCS_DIR", docs_dir)

    status = lint_anchors.main([])

    assert status == 0
