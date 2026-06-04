"""Unit tests for ``scripts/refresh_skill_template.py``.

The script is repo build tooling, not part of an importable package, so it is
loaded here by file path via ``importlib.util``. Tests are hermetic: the pure
text transforms are driven with minimal inline exemplar snippets, and the git /
filesystem seams are mocked or routed through ``tmp_path`` -- no real git, no
Docker, no network, and the live ``skill/assets/template`` tree is never
touched. The ``.mako`` render check runs against real Mako (the ``dev`` group
dependency).

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import pathlib
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
    """Pin REPO/TEMPLATE into ``tmp_path``; ``.git`` is left to the test."""
    repo = tmp_path / "repo"
    template = repo / "skill" / "assets" / "template"
    template.parent.mkdir(parents=True)
    monkeypatch.setattr(rst, "REPO", repo)
    monkeypatch.setattr(rst, "TEMPLATE", template)
    return repo, template


# --------------------------------------------------------------------------
# require
# --------------------------------------------------------------------------
def test_require_truthy_is_noop():
    assert rst.require(True, "unused") is None


def test_require_falsy_raises():
    with pytest.raises(rst.RefreshError, match="boom"):
        rst.require(False, "boom")


# --------------------------------------------------------------------------
# esc / repl
# --------------------------------------------------------------------------
def test_esc_wraps_literal():
    assert rst.esc("a ${X} b", "${X}") == "a <%text>${X}</%text> b"


def test_esc_missing_literal_raises():
    with pytest.raises(rst.RefreshError, match="expected literal"):
        rst.esc("nothing here", "${X}")


def test_repl_applies_pairs_in_order():
    assert (
        rst.repl("hello world", [("hello", "goodbye"), ("world", "all")])
        == "goodbye all"
    )


def test_repl_missing_old_raises():
    with pytest.raises(rst.RefreshError, match="expected to find"):
        rst.repl("hello", [("absent", "x")])


# --------------------------------------------------------------------------
# t_* transforms (pure)
# --------------------------------------------------------------------------
def test_t_compose():
    text = (
        "name: soliplex-template\n"
        'OLLAMA_BASE_URL: "${OLLAMA_BASE_URL}"\n'
        'INGESTER_TOKEN: "${INGESTER_TOKEN:-secret}"\n'
        'AUTO_CREATE_DATABASE: "${AUTO_CREATE_DATABASE:-1}"\n'
        'OPENAI_API_KEY: "${OPENAI_API_KEY}"\n'
        'ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"\n'
        'VOYAGE_API_KEY: "${VOYAGE_API_KEY}"\n'
        'CO_API_KEY: "${CO_API_KEY}"\n'
        "        PUID: ${PUID:-1000}\n"
        "        PGID: ${PGID:-1000}\n"
        '    user: "${PUID:-1000}:${PGID:-1000}"\n'
        '      - "9000:9000"\n'
        '      - "9443:9443"\n'
        "      command: --public-url https://soliplex.localhost:9443/tui\n"
        "      command: soliplex-cli serve --no-auth-mode --reload=config\n"
        '      - "8765:8765"\n'
        '      - "5001:5001"\n'
        '      - "5432:5432"\n'
        "      - ./rag/docs:/docs\n"
        # Backend-only anchors for the src/ bind mount + PYTHONPATH injection.
        "    environment:\n"
        "      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL}\n"
        "\n"
        "    volumes:\n"
        "      - type: bind\n"
        '        source: "rag/db/"\n'
        '        target: "/db"\n'
    )

    out = rst.t_compose(text)

    assert "name: ${project_name}" in out
    assert '- "${nginx_http}:9000"' in out
    assert '- "${nginx_https}:9443"' in out
    assert "https://${server_name}:${nginx_https}/tui" in out
    assert "soliplex-cli serve ${backend_auth_flag}--reload=config" in out
    assert '- "${ingester_port}:8765"' in out
    assert '- "${docling_port}:5001"' in out
    assert '- "${postgres_port}:5432"' in out
    assert "- ./${docs_dir}:/docs" in out
    assert "<%text>${OLLAMA_BASE_URL}</%text>" in out
    assert "<%text>${INGESTER_TOKEN:-secret}</%text>" in out
    # UID/GID alignment interpolations reach the rendered compose verbatim.
    assert "<%text>${PUID:-1000}</%text>" in out
    assert "<%text>${PGID:-1000}</%text>" in out
    # The backend gets this project's src/ on its import path.
    assert "PYTHONPATH: /app/src" in out
    assert 'source: "./src"' in out


def test_t_installation():
    text = (
        'id: "soliplex-conf-minimal"\n'
        '  # - "my_package.config.MyToolConfig"\n'
        '  - id: "default_chat"\n    model_name: "gpt-oss:latest"\n'
        '  - id: "title"\n    model_name: "gpt-oss:latest"\n'
        '    alt: "gpt-oss:20b"\n'
        "db: soliplex_agui authz: soliplex_authz\n"
        'haiku_rag_config_file: "./haiku.rag.yaml"\n'
        '  - "./rooms/bwrap_sandbox"\n'
    )

    out = rst.t_installation(text)

    assert 'id: "${setup_id}"' in out
    assert 'model_name: "${chat_model}"' in out
    assert 'model_name: "${title_model}"' in out
    assert '"${chat_model_alt}"' in out
    assert "${agui_db}" in out
    assert "${authz_db}" in out
    # The hypothetical 'my_package' meta example now points at this project.
    assert '"${package_name}.config.MyToolConfig"' in out
    assert "my_package" not in out
    # The demo room is loaded and the package's router is registered.
    assert '  - "./rooms/custom"' in out
    assert 'router_name: "${package_name}.views.router"' in out


def test_t_backend_haiku():
    text = (
        "name: qwen3-embedding:4b\n"
        "vector_dim: 2560\n"
        "qa:\n  model:\n    name: gpt-oss:latest\n"
        "research:\n  model:\n    name: gpt-oss:latest\n"
        "chunk_size: 256\n"
    )

    out = rst.t_backend_haiku(text)

    assert "name: ${rag_embed_model}" in out
    assert "vector_dim: ${rag_embed_dim}" in out
    assert "name: ${rag_qa_model}" in out
    assert "name: ${rag_research_model}" in out
    assert "chunk_size: ${chunk_size}" in out


def test_t_ingester_haiku():
    assert (
        rst.t_ingester_haiku("chunk_size: 256\n")
        == "chunk_size: ${chunk_size}\n"
    )


def test_t_backend_constraints():
    out = rst.t_backend_constraints("soliplex >= 0.68, < 0.69\nother==1\n")

    assert out == "soliplex ${soliplex_backend_constraint}\nother==1\n"


def test_t_backend_constraints_no_match_raises():
    with pytest.raises(rst.RefreshError, match="backend constraints"):
        rst.t_backend_constraints("nothing here\n")


def test_t_tui_constraints():
    out = rst.t_tui_constraints("soliplex >= 0.60.6, < 0.61\n")

    assert out == "soliplex ${soliplex_tui_constraint}\n"


def test_t_tui_constraints_no_match_raises():
    with pytest.raises(rst.RefreshError, match="tui constraints"):
        rst.t_tui_constraints("nothing here\n")


def test_t_nginx_conf():
    text = (
        "server { server_name localhost; }\n"
        "server { server_name localhost; }\n"
    )

    out = rst.t_nginx_conf(text)

    assert out.count("server_name ${server_name};") == 2


def test_t_nginx_conf_wrong_count_raises():
    with pytest.raises(rst.RefreshError, match="two 'server_name localhost;'"):
        rst.t_nginx_conf("server_name localhost;\n")


def test_t_nginx_dockerfile():
    text = (
        "RUN curl https://api/repos/x/releases/latest -o x && \\\n"
        '    openssl req -subj "/C=US/CN=localhost" -out cert\n'
    )

    out = rst.t_nginx_dockerfile(text)

    assert out == (
        "<%text>RUN curl https://api/repos/x/releases/</%text>"
        "${frontend_release_path}"
        "<%text> -o x && \\\n"
        '    openssl req -subj "</%text>'
        "${tls_subject}"
        '<%text>" -out cert</%text>\n'
    )


def test_t_nginx_dockerfile_no_subj_raises():
    with pytest.raises(rst.RefreshError, match="-subj"):
        rst.t_nginx_dockerfile("RUN echo hi\n")


def test_t_nginx_dockerfile_no_release_url_raises():
    text = 'RUN openssl req -subj "/CN=x" -out cert\n'

    with pytest.raises(rst.RefreshError, match="releases/latest"):
        rst.t_nginx_dockerfile(text)


def test_t_nginx_dockerfile_non_unique_subject_raises():
    text = 'RUN openssl req -subj "/CN=x" -out a\necho "/CN=x"\n'

    with pytest.raises(rst.RefreshError, match="not unique"):
        rst.t_nginx_dockerfile(text)


def test_t_init_sh():
    out = rst.t_init_sh("createdb soliplex_agui; createdb soliplex_authz\n")

    assert "${agui_db}" in out
    assert "${authz_db}" in out


# The always-on exemplars carry the gitea service / db / proxy; each transform
# must wrap its gitea fragment(s) in an include_gitea Mako conditional so
# generated projects can opt out. Driven by the real exemplars -- the same
# inputs refresh feeds the transforms.
def test_t_compose_wraps_gitea_on_real_exemplar():
    exemplar = (rst.REPO / "docker-compose.yml").read_text()

    mako = rst.t_compose(exemplar)

    assert "% if include_gitea:\n  gitea:\n" in mako
    assert "    restart: unless-stopped\n\n% endif\nvolumes:\n" in mako
    assert "<%text>${GITEA_ROOT_URL" in mako


def test_t_init_sh_wraps_gitea_on_real_exemplar():
    exemplar = (rst.REPO / "postgres" / "config" / "init.sh").read_text()

    mako = rst.t_init_sh(exemplar)

    assert "% if include_gitea:\n" in mako
    assert "CREATE DATABASE soliplex_gitea;" in mako


def test_t_nginx_conf_wraps_gitea_on_real_exemplar():
    exemplar = (rst.REPO / "nginx" / "nginx.conf").read_text()

    mako = rst.t_nginx_conf(exemplar)

    assert "% if include_gitea:\n        # Gitea under /gitea/" in mako


def test_t_claude_wraps_gitea_on_real_exemplar():
    exemplar = (rst.REPO / "CLAUDE.md").read_text()

    mako = rst.t_claude(exemplar)

    assert (
        '${", `3000` (gitea HTTP), `2222` (gitea SSH)"'
        ' if include_gitea else ""}' in mako
    )
    assert "% if include_gitea:\n- **gitea**" in mako
    assert "<%text>${INGESTER_TOKEN:-secret}</%text>" in mako


def test_t_gitignore():
    text = "/.env\n# Skill build artifacts:\n# more notes\n/dist/\n\n/tmp/\n"

    out = rst.t_gitignore(text)

    assert out == "/.env\n/tmp/\n"


def test_t_gitignore_missing_block_raises():
    with pytest.raises(rst.RefreshError, match="/dist/"):
        rst.t_gitignore("/.env\n/tmp/\n")


# --------------------------------------------------------------------------
# tracked_files
# --------------------------------------------------------------------------
def test_tracked_files_splits_and_filters(check_output):
    check_output.return_value = "a/b.txt\0c.txt\0\0"

    assert rst.tracked_files() == ["a/b.txt", "c.txt"]
    check_output.assert_called_once_with(
        ["git", "ls-files", "-z", "--", ".", *rst.EXCLUDE_PATHSPECS],
        cwd=rst.REPO,
        text=True,
    )


# --------------------------------------------------------------------------
# render_check (real Mako)
# --------------------------------------------------------------------------
def test_render_check_counts_failures(tmp_path, capsys):
    (tmp_path / "good.mako").write_text(
        "hello ${project_name}\n", encoding="utf-8"
    )
    (tmp_path / "bad.mako").write_text(
        "oops ${undefined_var}\n", encoding="utf-8"
    )

    bad = rst.render_check(tmp_path)

    assert bad == 1
    assert "render FAILED: bad.mako" in capsys.readouterr().err


# --------------------------------------------------------------------------
# _build_into
# --------------------------------------------------------------------------
def test_build_into_happy_path(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "plain.txt").write_text("verbatim\n", encoding="utf-8")
    (src / "v.txt").write_text("edit me\n", encoding="utf-8")
    (src / "d.txt").write_text("derive me\n", encoding="utf-8")
    monkeypatch.setattr(rst, "REPO", src)
    monkeypatch.setattr(rst, "VERBATIM_EDITS", {"v.txt": str.upper})
    monkeypatch.setattr(rst, "DERIVED", {"d.txt": lambda t: "MAKO:" + t})
    monkeypatch.setattr(rst, "AUTHORED", {"authored.md": "authored body\n"})
    dest = tmp_path / "dest"

    derived, authored = rst._build_into(dest, ["plain.txt", "v.txt", "d.txt"])

    assert (derived, authored) == (1, 1)
    assert (dest / "plain.txt").read_text() == "verbatim\n"
    assert (dest / "v.txt").read_text() == "EDIT ME\n"
    assert (dest / "d.txt.mako").read_text() == "MAKO:derive me\n"
    assert not (dest / "d.txt").exists()
    assert (dest / "authored.md").read_text() == "authored body\n"


def test_build_into_verbatim_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rst, "REPO", tmp_path / "repo")
    monkeypatch.setattr(rst, "VERBATIM_EDITS", {"gone.txt": str.upper})
    monkeypatch.setattr(rst, "DERIVED", {})
    monkeypatch.setattr(rst, "AUTHORED", {})

    with pytest.raises(
        rst.RefreshError, match="verbatim-edit source gone.txt"
    ):
        rst._build_into(tmp_path / "dest", [])


def test_build_into_verbatim_transform_error_is_wrapped(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "v.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(rst, "REPO", src)

    def boom(_text):
        raise rst.RefreshError("inner")

    monkeypatch.setattr(rst, "VERBATIM_EDITS", {"v.txt": boom})
    monkeypatch.setattr(rst, "DERIVED", {})
    monkeypatch.setattr(rst, "AUTHORED", {})

    with pytest.raises(rst.RefreshError, match="v.txt: inner"):
        rst._build_into(tmp_path / "dest", ["v.txt"])


def test_build_into_derived_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rst, "REPO", tmp_path / "repo")
    monkeypatch.setattr(rst, "VERBATIM_EDITS", {})
    monkeypatch.setattr(rst, "DERIVED", {"gone.txt": str.upper})
    monkeypatch.setattr(rst, "AUTHORED", {})

    with pytest.raises(rst.RefreshError, match="derived source gone.txt"):
        rst._build_into(tmp_path / "dest", [])


def test_build_into_derived_transform_error_is_wrapped(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "d.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(rst, "REPO", src)

    def boom(_text):
        raise rst.RefreshError("inner")

    monkeypatch.setattr(rst, "VERBATIM_EDITS", {})
    monkeypatch.setattr(rst, "DERIVED", {"d.txt": boom})
    monkeypatch.setattr(rst, "AUTHORED", {})

    with pytest.raises(rst.RefreshError, match="d.txt: inner"):
        rst._build_into(tmp_path / "dest", ["d.txt"])


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def test_main_requires_git_repo(repo):
    with pytest.raises(rst.RefreshError, match="must run inside the repo"):
        rst.main()


def test_main_no_tracked_files(repo, tracked_files):
    repo_dir, _template = repo
    (repo_dir / ".git").mkdir()
    tracked_files.return_value = []

    with pytest.raises(rst.RefreshError, match="returned nothing"):
        rst.main()

    tracked_files.assert_called_once_with()


def test_main_happy_path_fresh(
    repo, tracked_files, build_into, render_check, capsys
):
    repo_dir, template = repo
    (repo_dir / ".git").mkdir()
    staging = template.parent / "template.new"
    tracked_files.return_value = ["a"]
    build_into.return_value = (3, 2)
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
    build_into.return_value = (3, 2)
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
    build_into.return_value = (3, 2)
    render_check.return_value = 2

    with pytest.raises(rst.RefreshError, match="failed the render check"):
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
