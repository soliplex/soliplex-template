"""Unit tests for the repo-level ``scripts/lint_version_pairings.py``.

The script lints the published version-pairing artifacts against the
generator's default version pins in pre-commit; these tests drive its
helpers and pipeline with synthetic data written into ``tmp_path`` --
no artifact in the repo is read, and no git, Docker, or network is
touched. The check itself bites at hook time, plus the script's own
``--self-test`` scenarios.

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
    / "lint_version_pairings.py"
)
_spec = importlib.util.spec_from_file_location(
    "lint_version_pairings", _MODULE_PATH
)
lint_pairings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_pairings)

_GENERATOR = (
    'DEFAULTS = {"soliplex_image_tag": "0.88.0", '
    '"haiku_rag_version": "0.105.0"}\n'
)
_TOML = '[[pairings]]\nversion = "0.88"\nhaiku_rag = ">= 0.105.0, < 0.106"\n'
_MD = (
    "| soliplex tag | requires `haiku.rag-slim` |\n"
    "|--------------|--------------------------------|\n"
    "| v0.88 | `>= 0.105.0, < 0.106` |\n"
)


def _artifact_files(
    tmp_path: pathlib.Path,
    generator: str = _GENERATOR,
    toml: str = _TOML,
    md: str = _MD,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Write a generator / toml / docs-include triple; return the paths."""
    generator_path = tmp_path / "generate_soliplex_project.py"
    toml_path = tmp_path / "version_pairings.toml"
    md_path = tmp_path / "haiku-rag-pairings.md"
    generator_path.write_text(generator, encoding="utf-8")
    toml_path.write_text(toml, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")

    return generator_path, toml_path, md_path


def test_load_defaults(tmp_path) -> None:
    generator_path, _, _ = _artifact_files(tmp_path)

    defaults = lint_pairings.load_defaults(generator_path)

    assert defaults == {
        "image_tag": "0.88.0",
        "haiku_rag": "0.105.0",
    }


def test_toml_rows(tmp_path) -> None:
    _, toml_path, _ = _artifact_files(tmp_path)

    rows = lint_pairings.toml_rows(toml_path)

    assert rows == {"0.88": ">= 0.105.0, < 0.106"}


def test_md_strips_the_tag_prefix(tmp_path) -> None:
    _, _, md_path = _artifact_files(tmp_path)

    specs = lint_pairings.md_specs(md_path)

    assert specs == {"0.88": ">= 0.105.0, < 0.106"}


@pytest.mark.parametrize(
    "rows, version, expected",
    [
        ({"0.88": "spec"}, "0.88", "spec"),
        ({"0.88.0": "spec"}, "0.88.0", "spec"),
        ({"0.88": "spec"}, "0.88.0", "spec"),
        ({"0.88": "spec"}, "0.89.0", None),
    ],
)
def test_resolve_row(rows, version, expected) -> None:
    assert lint_pairings.resolve_row(rows, version) == expected


@pytest.mark.parametrize(
    "spec, expected",
    [
        (">= 0.105.0, < 0.106", "0.105.0"),
        (">= 0.19.0", "0.19.0"),
        ("> 0.105.0", None),
    ],
)
def test_floor(spec, expected) -> None:
    assert lint_pairings.floor(spec) == expected


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, id=case[0])
        for case in lint_pairings._SELF_TEST_CASES
    ],
)
def test_check_scenarios(case) -> None:
    _, defaults, rows, specs, expected = case

    found = lint_pairings.check(defaults, rows, specs)

    assert found == expected


def test_lint_clean(tmp_path) -> None:
    generator_path, toml_path, md_path = _artifact_files(tmp_path)

    status = lint_pairings.lint(generator_path, toml_path, md_path, False)

    assert status == 0


def test_lint_drift(tmp_path, capsys) -> None:
    generator_path, toml_path, md_path = _artifact_files(
        tmp_path,
        generator=(
            'DEFAULTS = {"soliplex_image_tag": "0.88.0", '
            '"haiku_rag_version": "0.104.0"}\n'
        ),
    )

    status = lint_pairings.lint(generator_path, toml_path, md_path, True)

    captured = capsys.readouterr()

    assert status == 1
    assert "floor mismatch" in captured.err
    assert "version_pairings.toml rows:" in captured.err
    assert "  0.88: >= 0.105.0, < 0.106" in captured.err


def test_lint_drift_terse(tmp_path, capsys) -> None:
    generator_path, toml_path, md_path = _artifact_files(
        tmp_path,
        generator=(
            'DEFAULTS = {"soliplex_image_tag": "0.88.0", '
            '"haiku_rag_version": "0.104.0"}\n'
        ),
    )

    status = lint_pairings.lint(generator_path, toml_path, md_path, False)

    captured = capsys.readouterr()

    assert status == 1
    assert "floor mismatch" in captured.err
    assert "rows:" not in captured.err


def test_lint_findings_and_verbose_rows(tmp_path, capsys) -> None:
    generator_path, toml_path, md_path = _artifact_files(tmp_path)

    status = lint_pairings.lint(generator_path, toml_path, md_path, True)

    captured = capsys.readouterr()

    assert status == 0
    assert "0.88.0' pairs with '0.105.0'" in captured.err


def test_main_self_test() -> None:
    status = lint_pairings.main(["--self-test"])

    assert status == 0


def test_main_self_test_verbose() -> None:
    status = lint_pairings.main(["--self-test", "--verbose"])

    assert status == 0


def test_main_self_test_failure(monkeypatch, capsys) -> None:
    cases = lint_pairings._SELF_TEST_CASES
    counter = {"n": 0}

    def _bites(defaults, rows, specs):
        index = counter["n"]
        counter["n"] += 1
        expected = cases[index][4]

        if not expected:
            return [lint_pairings.Finding("x", "y", "z")]

        return expected[:1]

    monkeypatch.setattr(lint_pairings, "check", _bites)

    status = lint_pairings.self_test(False)

    captured = capsys.readouterr()

    assert status == 1
    assert "self-test FAILED" in captured.err
    assert "spurious" in captured.err
    assert "missed" in captured.err


def test_main_lint_invocation(tmp_path, monkeypatch) -> None:
    generator_path, toml_path, md_path = _artifact_files(tmp_path)
    monkeypatch.setattr(lint_pairings, "GENERATOR_PATH", generator_path)
    monkeypatch.setattr(lint_pairings, "TOML_PATH", toml_path)
    monkeypatch.setattr(lint_pairings, "MD_PATH", md_path)

    status = lint_pairings.main([])

    assert status == 0
