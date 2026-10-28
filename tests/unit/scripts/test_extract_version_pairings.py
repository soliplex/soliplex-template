"""Unit tests for the repo-level ``scripts/extract_version_pairings.py``.

The script walks the release tags of a soliplex checkout and extracts the
``haiku.rag-slim`` pin each declares; the tests drive it against a tiny
stand-in git repo built on the fly (no network, no soliplex checkout).

Each test is laid out in three blank-line-separated phases -- setup, then
the single call under test (the "act"), then the assertions -- and performs
that act exactly once.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import tomllib

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts"
    / "extract_version_pairings.py"
)
_spec = importlib.util.spec_from_file_location(
    "extract_version_pairings", _MODULE_PATH
)
pairings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pairings)


def _git(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _make_soliplex_repo(
    tmp_path: pathlib.Path, releases: list[tuple[str, str | None]]
) -> pathlib.Path:
    """A stand-in soliplex repo: a commit per ``(tag, pyproject.toml)``.

    ``None`` content stands for a tagged commit with no ``pyproject.toml``.
    """
    repo = tmp_path / "soliplex"
    repo.mkdir()
    _git(repo, "init", "-q")
    # The container's global git config signs commits; gpg is unavailable.
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    for tag, content in releases:
        pyproject = repo / "pyproject.toml"
        if content is None:
            pyproject.unlink(missing_ok=True)
        else:
            pyproject.write_text(content)
        _git(repo, "add", "-A")
        # Releases without a dependency change still tag their own commit.
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"rel {tag}")
        _git(repo, "tag", tag)
    return repo


def _soliplex_pin(spec: str) -> str:
    return (
        '[project]\nname = "soliplex"\ndependencies = [\n'
        f'  "haiku.rag-slim {spec}",\n]\n'
    )


def _pre_slim_pyproject() -> str:
    return (
        '[project]\nname = "soliplex"\ndependencies = [\n'
        '  "haiku.rag >= 0.11.3",\n]\n'
    )


def test_list_release_tags_sorted_and_noise_filtered(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path,
        [
            ("v0.2", _soliplex_pin(">= 0.21.0, < 0.22")),
            ("v1x", _soliplex_pin(">= 9.9.0")),  # matches glob, not the regex
            ("v0.1", _soliplex_pin(">= 0.19.0")),
            ("v0.1.1", _soliplex_pin(">= 0.19.0, < 0.20")),
        ],
    )

    tags = pairings.list_release_tags(repo)

    assert tags == ["v0.1", "v0.1.1", "v0.2"]


def test_list_release_tags_without_any_releases(tmp_path):
    repo = _make_soliplex_repo(tmp_path, [])

    with pytest.raises(pairings.NoReleaseTags) as ctx:
        pairings.list_release_tags(repo)

    assert "no release tags found" in str(ctx.value)


def test_list_release_tags_not_a_git_repo(tmp_path):
    repo = tmp_path / "not-a-repo"
    repo.mkdir()

    with pytest.raises(pairings.TagsUnavailable) as ctx:
        pairings.list_release_tags(repo)

    assert "reading tags from" in str(ctx.value)


def test_read_pin_at_tag_squeezes_whitespace(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path, [("v0.19", _soliplex_pin(">=  0.19.0,   < 0.20"))]
    )

    pin = pairings._read_pin_at_tag(repo, "v0.19")

    assert pin == ">= 0.19.0, < 0.20"


def test_read_pin_at_tag_skips_pre_slim_release(tmp_path):
    repo = _make_soliplex_repo(tmp_path, [("v0.9", _pre_slim_pyproject())])

    pin = pairings._read_pin_at_tag(repo, "v0.9")

    assert pin is None


def test_read_pin_at_tag_without_any_dependencies(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path, [("v0.19", '[project]\nname = "soliplex"\n')]
    )

    pin = pairings._read_pin_at_tag(repo, "v0.19")

    assert pin is None


def test_read_pin_at_tag_with_missing_pyproject(tmp_path):
    repo = _make_soliplex_repo(tmp_path, [("v0.19", None)])

    with pytest.raises(pairings.PyProjectUnavailable) as ctx:
        pairings._read_pin_at_tag(repo, "v0.19")

    assert "reading 'pyproject.toml'" in str(ctx.value)


def test_read_pin_at_tag_with_invalid_toml(tmp_path):
    repo = _make_soliplex_repo(tmp_path, [("v0.19", "[project\nname = no.")])

    with pytest.raises(pairings.PyProjectInvalid) as ctx:
        pairings._read_pin_at_tag(repo, "v0.19")

    assert "not valid TOML" in str(ctx.value)


def test_collect_pairings_splits_rows_from_skipped_tags(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path,
        [
            ("v0.9", _pre_slim_pyproject()),
            ("v0.19", _soliplex_pin(">= 0.19.0, < 0.20")),
            ("v0.19.1", _soliplex_pin(">= 0.19.0, < 0.20")),
        ],
    )

    rows, skipped = pairings.collect_pairings(repo)

    assert rows == [
        {"version": "0.19.1", "haiku_rag": ">= 0.19.0, < 0.20"},
        {"version": "0.19", "haiku_rag": ">= 0.19.0, < 0.20"},
    ]
    assert skipped == ["v0.9"]


def test_collect_pairings_without_any_slim_pins(tmp_path):
    repo = _make_soliplex_repo(tmp_path, [("v0.9", _pre_slim_pyproject())])

    with pytest.raises(pairings.NoSlimPins) as ctx:
        pairings.collect_pairings(repo)

    assert "no release of" in str(ctx.value)


def test_render_markdown_table():
    rows = [
        {"version": "0.19", "haiku_rag": ">= 0.19.0, < 0.20"},
        {"version": "0.19.1", "haiku_rag": ">= 0.19.0, < 0.20"},
    ]

    rendered = pairings._render_markdown(rows)

    assert rendered == (
        "<!-- Generated by 'scripts/extract_version_pairings.py' from the\n"
        "soliplex repo's release tags; do not edit. -->\n"
        "\n"
        "| soliplex tag | requires `haiku.rag-slim` |\n"
        "|--------------|--------------------------------|\n"
        "| v0.19 | `>= 0.19.0, < 0.20` |\n"
        "| v0.19.1 | `>= 0.19.0, < 0.20` |\n"
    )


def test_render_toml_table():
    rows = [
        {"version": "0.19", "haiku_rag": ">= 0.19.0, < 0.20"},
        {"version": "0.19.1", "haiku_rag": ">= 0.19.0, < 0.20"},
    ]

    rendered = pairings._render_toml(rows, "2026-10-27")

    assert rendered.startswith(
        "# Generated by 'scripts/extract_version_pairings.py' from the\n"
        "# soliplex repo's release tags; do not edit.\n"
    )
    assert "later patches (v0.85.1 ...) tag their own commits" in rendered


def test_render_toml_table_round_trips():
    rows = [{"version": "0.19", "haiku_rag": ">= 0.19.0, < 0.20"}]

    rendered = pairings._render_toml(rows, "2026-10-27")

    parsed = tomllib.loads(rendered)

    assert parsed["extracted"] == "2026-10-27"
    assert parsed["pairings"] == rows


def test_main_writes_both_tables(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path, [("v0.19", _soliplex_pin(">= 0.19.0"))]
    )
    markdown = tmp_path / "out" / "haiku-rag-pairings.md"
    toml = tmp_path / "out" / "version_pairings.toml"

    result = pairings.main(
        [
            "--repo",
            str(repo),
            "--markdown",
            str(markdown),
            "--toml",
            str(toml),
        ]
    )

    assert result == 0
    assert markdown.is_file()
    assert "| v0.19 | `>= 0.19.0` |" in markdown.read_text()
    assert tomllib.loads(toml.read_text())["pairings"] == [
        {"version": "0.19", "haiku_rag": ">= 0.19.0"}
    ]


def test_main_reports_pre_slim_releases(tmp_path):
    repo = _make_soliplex_repo(
        tmp_path,
        [
            ("v0.9", _pre_slim_pyproject()),
            ("v0.19", _soliplex_pin(">= 0.19.0")),
        ],
    )
    markdown = tmp_path / "out" / "haiku-rag-pairings.md"
    toml = tmp_path / "out" / "version_pairings.toml"

    result = pairings.main(
        [
            "--repo",
            str(repo),
            "--markdown",
            str(markdown),
            "--toml",
            str(toml),
        ]
    )

    assert result == 0
    assert "| v0.9 |" not in markdown.read_text()
    assert [
        row["version"] for row in tomllib.loads(toml.read_text())["pairings"]
    ] == ["0.19"]


def test_main_propagates_pairings_errors(tmp_path):
    empty = tmp_path / "empty-repo"
    empty.mkdir()

    with pytest.raises(pairings.PairingsError):
        pairings.main(["--repo", str(empty)])
