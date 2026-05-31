"""Unit tests for ``scripts/build_skill.py``.

The script is repo build tooling, not part of an importable package, so it is
loaded here by file path via ``importlib.util``. Tests are hermetic: the git,
filesystem, and validator seams are mocked or driven through ``tmp_path`` -- no
real git, no Docker, no network, and the live ``skill/`` tree is never touched.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "build_skill.py"
)
_spec = importlib.util.spec_from_file_location("build_skill", _MODULE_PATH)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


# The exact argv git_head_commit() hands to subprocess.run.
_GIT_REV_PARSE = ["git", "-C", str(bs.REPO_DIR), "rev-parse", "HEAD"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _skill_md(extra_meta: bool = False) -> str:
    """A minimal valid SKILL.md frontmatter, optionally with a metadata block."""
    lines = ["---", "name: soliplex-template", "description: scaffold a stack"]
    if extra_meta:
        lines.append("metadata:")
        lines.append('  source_commit: "deadbee"')
    lines += ["---", "# soliplex-template", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Fixtures
#
# Each installs a Mock at a seam in the module under test and returns it, so a
# test configures its ``return_value`` / ``side_effect`` and asserts the call.
# --------------------------------------------------------------------------
@pytest.fixture
def which(monkeypatch):
    which = mock.Mock()
    monkeypatch.setattr(bs.shutil, "which", which)
    return which


@pytest.fixture
def run(monkeypatch):
    run = mock.Mock()
    monkeypatch.setattr(bs.subprocess, "run", run)
    return run


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """Pin SRC/DIST/OUT into ``tmp_path`` and return them."""
    src = tmp_path / "skill"
    dist = tmp_path / "dist"
    out = dist / bs.SKILL_NAME
    monkeypatch.setattr(bs, "SRC", src)
    monkeypatch.setattr(bs, "DIST", dist)
    monkeypatch.setattr(bs, "OUT", out)
    return src, dist, out


# --------------------------------------------------------------------------
# die
# --------------------------------------------------------------------------
def test_die_exits_nonzero_with_stderr(capsys):
    with pytest.raises(SystemExit) as excinfo:
        bs.die("boom")

    assert excinfo.value.code == 1
    assert "build_skill: error: boom" in capsys.readouterr().err


# --------------------------------------------------------------------------
# git_head_commit
# --------------------------------------------------------------------------
def test_git_head_commit_no_git(which, run):
    which.return_value = None

    assert bs.git_head_commit() is None
    which.assert_called_once_with("git")
    run.assert_not_called()


def test_git_head_commit_called_process_error(which, run):
    which.return_value = "/usr/bin/git"
    run.side_effect = subprocess.CalledProcessError(1, ["git"])

    assert bs.git_head_commit() is None
    which.assert_called_once_with("git")
    run.assert_called_once_with(
        _GIT_REV_PARSE, capture_output=True, text=True, check=True
    )


def test_git_head_commit_success(which, run):
    which.return_value = "/usr/bin/git"
    run.return_value = mock.Mock(stdout="abc1234def\n")

    assert bs.git_head_commit() == "abc1234def"
    run.assert_called_once_with(
        _GIT_REV_PARSE, capture_output=True, text=True, check=True
    )


def test_git_head_commit_empty_stdout(which, run):
    which.return_value = "/usr/bin/git"
    run.return_value = mock.Mock(stdout="\n")

    assert bs.git_head_commit() is None
    run.assert_called_once_with(
        _GIT_REV_PARSE, capture_output=True, text=True, check=True
    )


# --------------------------------------------------------------------------
# stamp_source_commit
# --------------------------------------------------------------------------
def test_stamp_no_frontmatter_dies(tmp_path, capsys):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("no frontmatter here\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        bs.stamp_source_commit(skill_md, "abc1234")

    assert "no YAML frontmatter" in capsys.readouterr().err


def test_stamp_already_stamped_is_noop(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    original = _skill_md(extra_meta=True)
    skill_md.write_text(original, encoding="utf-8")

    bs.stamp_source_commit(skill_md, "feedface")

    assert skill_md.read_text(encoding="utf-8") == original


def test_stamp_inserts_under_existing_metadata(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\nname: soliplex-template\nmetadata:\n  other: 1\n---\n# x\n",
        encoding="utf-8",
    )

    bs.stamp_source_commit(skill_md, "abc1234")

    text = skill_md.read_text(encoding="utf-8")
    assert 'metadata:\n  source_commit: "abc1234"\n  other: 1\n' in text


def test_stamp_appends_metadata_block(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(_skill_md(), encoding="utf-8")

    bs.stamp_source_commit(skill_md, "abc1234")

    text = skill_md.read_text(encoding="utf-8")
    assert 'metadata:\n  source_commit: "abc1234"' in text


# --------------------------------------------------------------------------
# validator_cmd
# --------------------------------------------------------------------------
def test_validator_cmd_agentskills_on_path(which):
    which.side_effect = lambda name: (
        "/bin/agentskills" if name == "agentskills" else None
    )

    assert bs.validator_cmd() == ["/bin/agentskills", "validate"]
    which.assert_called_once_with("agentskills")


def test_validator_cmd_uvx_fallback(which):
    which.side_effect = lambda name: "/bin/uvx" if name == "uvx" else None

    assert bs.validator_cmd() == [
        "/bin/uvx",
        "--from",
        "skills-ref",
        "agentskills",
        "validate",
    ]
    assert which.call_args_list == [mock.call("agentskills"), mock.call("uvx")]


def test_validator_cmd_none_available_dies(which, capsys):
    which.return_value = None

    with pytest.raises(SystemExit):
        bs.validator_cmd()

    assert "cannot find the agent-skills validator" in capsys.readouterr().err
    assert which.call_args_list == [mock.call("agentskills"), mock.call("uvx")]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def test_main_missing_src_dir_dies(layout, capsys):
    with pytest.raises(SystemExit):
        bs.main([])

    assert "source dir not found" in capsys.readouterr().err


def test_main_missing_skill_md_dies(layout, capsys):
    src, _dist, _out = layout
    src.mkdir()

    with pytest.raises(SystemExit):
        bs.main([])

    assert "missing" in capsys.readouterr().err


def test_main_happy_path_with_commit_arg(layout, run, monkeypatch):
    src, _dist, out = layout
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    monkeypatch.setattr(bs, "validator_cmd", lambda: ["validator"])
    run.return_value = mock.Mock(returncode=0)

    result = bs.main(["--commit", "abc1234"])

    assert result == 0
    assert 'source_commit: "abc1234"' in (out / "SKILL.md").read_text()
    run.assert_called_once_with(["validator", str(out)])


def test_main_existing_dist_is_removed(layout, run, monkeypatch):
    src, dist, out = layout
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    dist.mkdir()
    (dist / "stale").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(bs, "validator_cmd", lambda: ["validator"])
    run.return_value = mock.Mock(returncode=0)

    bs.main(["--commit", "abc1234"])

    assert not (dist / "stale").exists()
    assert (out / "SKILL.md").is_file()
    run.assert_called_once_with(["validator", str(out)])


def test_main_falls_back_to_git_head_commit(layout, run, monkeypatch):
    src, _dist, out = layout
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    monkeypatch.setattr(bs, "git_head_commit", lambda: "headsha")
    monkeypatch.setattr(bs, "validator_cmd", lambda: ["validator"])
    run.return_value = mock.Mock(returncode=0)

    bs.main([])

    assert 'source_commit: "headsha"' in (out / "SKILL.md").read_text()
    run.assert_called_once_with(["validator", str(out)])


def test_main_no_commit_warns_and_skips_stamp(
    layout, run, monkeypatch, capsys
):
    src, _dist, out = layout
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    monkeypatch.setattr(bs, "git_head_commit", lambda: None)
    monkeypatch.setattr(bs, "validator_cmd", lambda: ["validator"])
    run.return_value = mock.Mock(returncode=0)

    bs.main([])

    assert "no commit available" in capsys.readouterr().err
    assert "source_commit" not in (out / "SKILL.md").read_text()
    run.assert_called_once_with(["validator", str(out)])


def test_main_validation_failure_dies(layout, run, monkeypatch, capsys):
    src, _dist, out = layout
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    monkeypatch.setattr(bs, "validator_cmd", lambda: ["validator"])
    run.return_value = mock.Mock(returncode=1)

    with pytest.raises(SystemExit):
        bs.main(["--commit", "abc1234"])

    assert "skill validation failed" in capsys.readouterr().err
    run.assert_called_once_with(["validator", str(out)])
