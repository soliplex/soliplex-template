"""Unit tests for the generic ``soliplex_template.env`` core.

It ships as an installed package, so it is imported directly. Tests are
hermetic: everything is routed through ``tmp_path``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import stat

import pytest

from soliplex_template import env as st_env

_FULL_MANIFEST = """\
[tool.soliplex-template.params]
package_name = "example_stack"
ollama_base_url = "http://ollama.lan:11434"
puid = 1500
pgid = 1501
"""


def _make_stack(tmp_path, *, pyproject_text: str | None = _FULL_MANIFEST):
    project = tmp_path / "stack"
    project.mkdir()
    if pyproject_text is not None:
        pyproject = project / "pyproject.toml"
        pyproject.write_text(pyproject_text)
    return project


# ---- read_env_params ----


def test_read_env_params_requires_pyproject(tmp_path):
    project = _make_stack(tmp_path, pyproject_text=None)

    with pytest.raises(st_env.EnvError) as caught:
        st_env.read_env_params(project)

    assert type(caught.value) is st_env.PyProjectNotFound
    assert caught.value.path == project / "pyproject.toml"
    assert "cannot find pyproject.toml" in str(caught.value)


def test_read_env_params_requires_manifest(tmp_path):
    bare = '[project]\nname = "not-a-stack"\n'
    project = _make_stack(tmp_path, pyproject_text=bare)

    with pytest.raises(st_env.EnvError) as caught:
        st_env.read_env_params(project)

    assert type(caught.value) is st_env.ManifestMissing
    assert "[tool.soliplex-template.params]" in str(caught.value)


def test_read_env_params_rejects_bad_toml(tmp_path):
    project = _make_stack(tmp_path, pyproject_text="!! nope\n")

    with pytest.raises(st_env.EnvError) as caught:
        st_env.read_env_params(project)

    assert type(caught.value) is st_env.ManifestBroken
    assert "cannot parse" in str(caught.value)


def test_read_env_params_names_missing_keys(tmp_path):
    no_ollama = "[tool.soliplex-template.params]\npuid = 1500\npgid = 1501\n"
    project = _make_stack(tmp_path, pyproject_text=no_ollama)

    with pytest.raises(st_env.EnvError) as caught:
        st_env.read_env_params(project)

    assert type(caught.value) is st_env.ManifestIncomplete
    assert caught.value.missing == ["ollama_base_url"]
    assert "lacks key(s): ollama_base_url" in str(caught.value)


def test_read_env_params_maps_manifest_keys(tmp_path):
    project = _make_stack(tmp_path)

    assignments = st_env.read_env_params(project)

    assert assignments == {
        "OLLAMA_BASE_URL": "http://ollama.lan:11434",
        "PUID": "1500",
        "PGID": "1501",
    }


# ---- generate_env ----


def test_generate_env_writes_commented_env(tmp_path, capsys):
    project = _make_stack(tmp_path)

    st_env.generate_env(project)

    recovery = project / ".env"
    assert recovery.is_file()
    content = recovery.read_text()
    assert "# Recreated by scripts/generate_env.py" in content
    for line in (
        "OLLAMA_BASE_URL=http://ollama.lan:11434",
        "PUID=1500",
        "PGID=1501",
    ):
        assert f"# {line}" in content
        # nothing live: every assignment ships commented
        assert f"\n{line}" not in content
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
    assert "wrote" in capsys.readouterr().out


def test_generate_env_leaves_existing_env_alone(tmp_path):
    project = _make_stack(tmp_path)
    env = project / ".env"
    env.write_text("PUID=2000\n")

    st_env.generate_env(project)

    assert env.read_text() == "PUID=2000\n"
