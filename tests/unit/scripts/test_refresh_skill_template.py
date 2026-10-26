"""Unit tests for ``scripts/refresh_skill_template.py``.

The script is repo build tooling, not part of an importable package, so it is
loaded here by file path via ``importlib.util``. Tests are hermetic: the git /
filesystem seams are mocked or routed through ``tmp_path`` -- no real git, no
Docker, no network -- and the tests never write to the tree (the live
``skills/soliplex-template/assets/template`` and the checked-in ``*.mako``
exemplars are read but never touched). The ``.mako`` render check runs against
real Mako (the ``dev`` group dependency).

The pipeline is placeholder-first: the checked-in ``*.mako`` templates carry
their ``${...}``` spellings verbatim, and the invariant tests below pin that
they render under the same parameters the generator uses and name no concrete
release tags.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "scripts"
    / "refresh_skill_template.py"
)
_spec = importlib.util.spec_from_file_location(
    "refresh_skill_template", _MODULE_PATH
)
rst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rst)


# --------------------------------------------------------------------------
# Fixtures
#
# Each installs a Mock at a seam in the module under test and returns it, so a
# test configures its ``return_value`` / ``side_effect`` and asserts the call.
# --------------------------------------------------------------------------
@pytest.fixture
def check_output(monkeypatch):
    check_output = mock.Mock()
    monkeypatch.setattr(rst.subprocess, "check_output", check_output)
    return check_output


@pytest.fixture
def tracked_files(monkeypatch):
    tracked_files = mock.Mock()
    monkeypatch.setattr(rst, "tracked_files", tracked_files)
    return tracked_files


@pytest.fixture
def build_into(monkeypatch):
    build_into = mock.Mock()
    monkeypatch.setattr(rst, "_build_into", build_into)
    return build_into


@pytest.fixture
def render_check(monkeypatch):
    render_check = mock.Mock()
    monkeypatch.setattr(rst, "render_check", render_check)
    return render_check


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    (repo_dir / "skills" / "soliplex-template" / "assets").mkdir(parents=True)
    template = (
        repo_dir / "skills" / "soliplex-template" / "assets" / "template"
    )
    monkeypatch.setattr(rst, "REPO", repo_dir)
    monkeypatch.setattr(rst, "TEMPLATE", template)
    return repo_dir, template


# --------------------------------------------------------------------------
# require()
# --------------------------------------------------------------------------
def test_require_truthy_is_noop():
    assert rst.require(True, "unused") is None


def test_require_falsy_raises():
    with pytest.raises(rst.RefreshError, match="boom"):
        rst.require(False, "boom")


# --------------------------------------------------------------------------
# Checked-in template invariants
#
# These read the live tree (never touching it) and pin the de-maze shape:
# the parameterized files are checked in as .mako, spell their parameters,
# and name no concrete release tags.
# --------------------------------------------------------------------------
_RELEASE_TAG = re.compile(
    r"ghcr\.io/soliplex/soliplex-(postgres|tui):[0-9v]"
    r"|ghcr\.io/ggozad/haiku\.rag-slim:[0-9v]"
    r"|quay\.io/docling-project/docling-serve[-\w]*:[0-9v]"
)

_CHECKED_IN_MAKO = [
    "AGENTS.md.mako",
    "docker-compose.yml.mako",
    ".gitignore.mako",
    "backend/Dockerfile.mako",
    "backend/environment/installation.yaml.mako",
    "backend/environment/haiku.rag.yaml.mako",
    "haiku.rag/haiku.rag.yaml.mako",
    "nginx/Dockerfile.mako",
    "nginx/nginx.conf.mako",
    "postgres/config/init.sh.mako",
    "tui/Dockerfile.mako",
]


def _checked_in(rel: str) -> str:
    return (rst.REPO / rel).read_text()


@pytest.mark.parametrize("rel", _CHECKED_IN_MAKO)
def test_checked_in_mako_carry_no_release_tags(rel):
    text = _checked_in(rel)

    assert _RELEASE_TAG.search(text) is None, rel


@pytest.mark.parametrize("rel", _CHECKED_IN_MAKO)
def test_checked_in_mako_renders_under_probe_params(rel, tmp_path):
    # One checked-in file, copied flat into a scratch dir, renders under the
    # same parameters the generator uses; render_check reports failures
    # rather than raising.
    (tmp_path / "d.mako").write_text(_checked_in(rel))

    assert rst.render_check(tmp_path) == 0


@pytest.mark.parametrize(
    "rel, param",
    [
        ("docker-compose.yml.mako", "haiku_rag_version"),
        ("docker-compose.yml.mako", "docling_serve_image"),
        ("AGENTS.md.mako", "haiku_rag_version"),
        ("AGENTS.md.mako", "soliplex_image_tag"),
        ("backend/Dockerfile.mako", "soliplex_image_tag"),
        ("tui/Dockerfile.mako", "soliplex_image_tag"),
    ],
)
def test_version_parameters_are_spelled_not_baked(rel, param):
    needle = f"${{{param}}}"

    text = _checked_in(rel)

    assert needle in text, rel


def test_backend_dockerfile_mako_wraps_dockerfile_literal():
    # The Dockerfile body (including the shell ${PUID}/${PGID} expansions and
    # every line-continuation backslash) lives inside one <%text> block, with
    # only the ${soliplex_image_tag} spelling outside it.
    text = _checked_in("backend/Dockerfile.mako")

    assert text.count("<%text>") == 2
    assert "</%text>${soliplex_image_tag}<%text>" in text
    assert "ARG PUID=1000" in text


def test_agents_mako_escapes_ingester_token_expansion():
    # haiku.rag's own ${INGESTER_TOKEN} interpolation is still shell
    # literal after rendering (the secret is exported into the environment,
    # #244), so AGENTS.md.mako must keep it <%text>-escaped.
    text = _checked_in("AGENTS.md.mako")

    assert "<%text>${INGESTER_TOKEN}</%text>" in text


def test_agents_mako_folds_gitea_into_conditionals():
    text = _checked_in("AGENTS.md.mako")

    assert "% if include_gitea:" in text


# --------------------------------------------------------------------------
# t_user_doc(): shipped docs derive from docs/users/ Markdown.
# --------------------------------------------------------------------------
def test_t_user_doc():
    text = (
        "# Title\n"
        "\n"
        "## Section\n"
        "\n"
        "Open <http://localhost:${nginx_http}>. Token"
        " `${INGESTER_TOKEN:-secret}`.\n"
        "\n"
        "<!-- if:tui -->\n"
        "tui only\n"
        "<!-- endif -->\n"
    )

    out = rst.t_user_doc(text)

    # H1 left alone; H2+ wrapped so Mako doesn't read '##' as a comment.
    assert "# Title\n" in out
    assert "<%text>## Section</%text>" in out
    # The source's own ${...} parameter spelling passes through for the
    # generator to bake; literal shell expansions are escaped.
    assert "Open <http://localhost:${nginx_http}>." in out
    assert "Token `<%text>${INGESTER_TOKEN:-secret}</%text>`." in out
    # HTML-comment conditional fences become Mako control lines.
    assert "% if include_tui:\ntui only\n% endif\n" in out


def test_t_user_doc_strips_site_only_block():
    text = (
        "# Title\n"
        "\n"
        "<!-- site-only -->\n"
        '!!! note "About this page"\n'
        "    Repo-site banner.\n"
        "<!-- endsite-only -->\n"
        "\n"
        "Body paragraph.\n"
    )

    out = rst.t_user_doc(text)

    assert out == "# Title\n\nBody paragraph.\n"


def test_t_user_doc_escapes_non_parameter_expansions():
    text = (
        "`${INGESTER_TOKEN}` and `${VAR:-default}` and"
        " `${nginx_http}` and `${VAR}`.\n"
    )

    out = rst.t_user_doc(text)

    assert (
        "`<%text>${INGESTER_TOKEN}</%text>` and"
        " `<%text>${VAR:-default}</%text>` and `${nginx_http}` and"
        " `<%text>${VAR}</%text>`.\n" == out
    )


def test_tracked_files_splits_and_filters(repo, check_output):
    root, _ = repo
    (root / "a").mkdir()
    (root / "a" / "b.txt").write_text("b")
    (root / "c.txt").write_text("c")
    # 'gone.txt' is a deletion not yet staged: still in the index, not on disk.
    check_output.return_value = "c.txt\0gone.txt\0a/b.txt\0\0"

    files = rst.tracked_files()

    assert files == ["a/b.txt", "c.txt"]
    check_output.assert_called_once_with(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ".",
            *rst.EXCLUDE_PATHSPECS,
        ],
        cwd=root,
        text=True,
    )


# --------------------------------------------------------------------------
# render_check (real Mako)
# --------------------------------------------------------------------------
def test_render_check_counts_failures(tmp_path, capsys):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "good.mako").write_text("plain\n")
    (tmp_path / "subdir" / "bad.mako").write_text("% if broken\n")

    bad = rst.render_check(tmp_path)

    assert bad == 1
    assert "render FAILED: bad.mako" in capsys.readouterr().err


def test_build_into_copies_checked_in_mako_and_authors(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plain.txt").write_text("verbatim\n")
    (repo_dir / "d.mako").write_text("${nginx_http}\n")
    monkeypatch.setattr(rst, "REPO", repo_dir)
    # src/__package__/pyproject.toml.mako only; its file content is unused.
    monkeypatch.setattr(rst, "AUTHORED", {"nested/authored.md": "authored\n"})

    derived, authored, checked = rst._build_into(
        tmp_path / "dest", ["plain.txt", "d.mako"]
    )

    assert (derived, authored, checked) == (0, 1, 1)
    assert (tmp_path / "dest" / "plain.txt").read_text() == "verbatim\n"
    assert (tmp_path / "dest" / "d.mako").read_text() == "${nginx_http}\n"
    assert (
        tmp_path / "dest" / "nested" / "authored.md"
    ).read_text() == "authored\n"


def test_build_into_derives_user_docs(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    (repo_dir / "docs" / "users").mkdir(parents=True)
    (repo_dir / "docs" / "users" / "index.md").write_text(
        "port `${nginx_http}`\n"
    )
    (repo_dir / "docs" / "users" / "sub").mkdir()
    (repo_dir / "docs" / "users" / "sub" / "page.md").write_text("# Home\n")
    monkeypatch.setattr(rst, "REPO", repo_dir)
    monkeypatch.setattr(rst, "USER_DOCS_SRC", "docs/users")
    monkeypatch.setattr(rst, "AUTHORED", {})

    derived, authored, _checked = rst._build_into(tmp_path / "dest", [])

    assert (derived, authored) == (2, 0)
    assert (
        tmp_path / "dest" / "docs" / "index.md.mako"
    ).read_text() == "port `${nginx_http}`\n"
    assert (
        tmp_path / "dest" / "docs" / "sub" / "page.md.mako"
    ).read_text() == "# Home\n"


def test_main_requires_git_repo(repo):
    with pytest.raises(rst.RefreshError, match="must run inside the repo"):
        rst.main()


def test_main_no_tracked_files(repo, tracked_files):
    repo_dir, _template = repo
    (repo_dir / ".git").mkdir(exist_ok=True)
    tracked_files.return_value = []

    with pytest.raises(rst.RefreshError, match="returned nothing"):
        rst.main()


def test_main_happy_path_fresh(
    repo, tracked_files, build_into, render_check, capsys
):
    repo_dir, template = repo
    (repo_dir / ".git").mkdir()
    staging = template.parent / "template.new"
    tracked_files.return_value = ["a"]
    build_into.return_value = (3, 1, 2)
    render_check.return_value = 0

    result = rst.main()

    assert result == 0
    assert template.is_dir()
    assert not staging.exists()
    assert "render-check OK" in capsys.readouterr().out
    tracked_files.assert_called_once_with()
    build_into.assert_called_once_with(staging, ["a"])
    render_check.assert_called_once_with(staging)


def test_main_happy_path_replaces_existing(
    repo, tracked_files, build_into, render_check
):
    repo_dir, template = repo
    (repo_dir / ".git").mkdir()
    template.mkdir(parents=True)
    (template / "stale").write_text("old\n", encoding="utf-8")
    staging = template.parent / "template.new"
    staging.mkdir()
    tracked_files.return_value = ["a"]
    build_into.return_value = (3, 1, 2)
    render_check.return_value = 0

    result = rst.main()

    assert result == 0
    assert template.is_dir()
    assert not (template / "stale").exists()
    build_into.assert_called_once_with(staging, ["a"])
    render_check.assert_called_once_with(staging)


def test_main_render_check_failure_leaves_template_intact(
    repo, tracked_files, build_into, render_check
):
    repo_dir, template = repo
    (repo_dir / ".git").mkdir()
    template.mkdir(parents=True)
    (template / "keep").write_text("intact\n", encoding="utf-8")
    staging = template.parent / "template.new"
    tracked_files.return_value = ["a"]
    build_into.return_value = (3, 1, 2)
    render_check.return_value = 2

    with pytest.raises(rst.RenderFailures, match="failed the render check"):
        rst.main()

    assert (template / "keep").read_text() == "intact\n"
    assert not staging.exists()
    build_into.assert_called_once_with(staging, ["a"])
    render_check.assert_called_once_with(staging)


def test_main_build_error_removes_staging(
    repo, tracked_files, build_into, render_check
):
    repo_dir, template = repo
    (repo_dir / ".git").mkdir()
    staging = template.parent / "template.new"
    tracked_files.return_value = ["a"]
    build_into.side_effect = rst.RefreshError("build blew up")

    with pytest.raises(rst.RefreshError, match="build blew up"):
        rst.main()

    assert not staging.exists()
    build_into.assert_called_once_with(staging, ["a"])
    render_check.assert_not_called()
