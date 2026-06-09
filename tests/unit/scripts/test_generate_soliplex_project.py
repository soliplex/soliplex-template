"""Unit tests for the bundled ``skill/scripts/generate_soliplex_project.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``.
Tests are hermetic: parameter logic is driven with plain dicts, the ``.mako``
render path runs against real Mako (the ``dev`` dependency), and the git /
secrets / filesystem seams are mocked or routed through ``tmp_path`` -- no real
git, no Docker, no network.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import stat
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "generate_soliplex_project.py"
)
_spec = importlib.util.spec_from_file_location(
    "generate_soliplex_project", _MODULE_PATH
)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _args(**overrides) -> argparse.Namespace:
    """A parsed-args namespace with the script's defaults, overridable."""
    base = dict(
        out=None,
        params=None,
        interactive=False,
        force=False,
        generate_secrets=True,
        no_git=False,
        disable_gpg_sign=False,
        print_defaults=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _valid_params() -> dict[str, object]:
    """A coerced, valid parameter set (passes ``validate``)."""
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://ollama:11434"
    return gen.coerce_and_derive(params)


# --------------------------------------------------------------------------
# Fixtures
#
# Each installs a Mock at a seam in the module under test and returns it.
# --------------------------------------------------------------------------
@pytest.fixture
def which(monkeypatch):
    which = mock.Mock()
    monkeypatch.setattr(gen.shutil, "which", which)
    return which


@pytest.fixture
def run(monkeypatch):
    run = mock.Mock()
    monkeypatch.setattr(gen.subprocess, "run", run)
    return run


# --------------------------------------------------------------------------
# load_params
# --------------------------------------------------------------------------
def test_load_params_defaults_only():
    params = gen.load_params(_args())

    assert params == dict(gen.DEFAULTS)


def test_load_params_reads_overrides(tmp_path):
    params_file = tmp_path / "p.json"
    params_file.write_text('{"project_name": "custom"}', encoding="utf-8")

    params = gen.load_params(_args(params=str(params_file)))

    assert params["project_name"] == "custom"


def test_load_params_unreadable_file(tmp_path):
    with pytest.raises(gen.GenError, match="cannot read --params"):
        gen.load_params(_args(params=str(tmp_path / "missing.json")))


def test_load_params_invalid_json(tmp_path):
    params_file = tmp_path / "p.json"
    params_file.write_text("not json", encoding="utf-8")

    with pytest.raises(gen.GenError, match="not valid JSON"):
        gen.load_params(_args(params=str(params_file)))


def test_load_params_not_an_object(tmp_path):
    params_file = tmp_path / "p.json"
    params_file.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(gen.GenError, match="must be an object/dict"):
        gen.load_params(_args(params=str(params_file)))


def test_load_params_unknown_key(tmp_path):
    params_file = tmp_path / "p.json"
    params_file.write_text('{"bogus": 1}', encoding="utf-8")

    with pytest.raises(gen.GenError, match="unknown parameter"):
        gen.load_params(_args(params=str(params_file)))


def test_load_params_interactive(monkeypatch):
    sentinel = {"sentinel": True}
    prompt = mock.Mock(return_value=sentinel)
    monkeypatch.setattr(gen, "prompt_interactive", prompt)

    params = gen.load_params(_args(interactive=True))

    assert params is sentinel
    prompt.assert_called_once_with(dict(gen.DEFAULTS))


# --------------------------------------------------------------------------
# prompt_interactive
# --------------------------------------------------------------------------
def test_prompt_interactive_blank_keeps_defaults(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    params = dict(gen.DEFAULTS)

    result = gen.prompt_interactive(params)

    assert result == dict(gen.DEFAULTS)


def test_prompt_interactive_overrides(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "X")
    params = dict(gen.DEFAULTS)

    result = gen.prompt_interactive(params)

    assert all(result[key] == "X" for key in gen.DEFAULTS)


def test_prompt_interactive_eof_keeps_defaults(monkeypatch):
    def _raise(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise)
    params = dict(gen.DEFAULTS)

    result = gen.prompt_interactive(params)

    assert result == dict(gen.DEFAULTS)


# --------------------------------------------------------------------------
# coerce_and_derive
# --------------------------------------------------------------------------
def test_coerce_derives_defaults():
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"

    result = gen.coerce_and_derive(params)

    assert result["setup_id"] == "soliplex-conf"
    assert result["server_name"] == "soliplex.localhost"
    assert result["tls_subject"].endswith("CN=soliplex.localhost")
    assert result["backend_auth_flag"] == "--no-auth-mode "
    assert result["nginx_http"] == 9000
    assert result["frontend_release_path"] == "latest"


def test_coerce_int_error():
    params = dict(gen.DEFAULTS)
    params["chunk_size"] = "not-an-int"

    with pytest.raises(gen.GenError, match="chunk_size must be an integer"):
        gen.coerce_and_derive(params)


def test_coerce_keeps_supplied_values_and_auth_mode():
    params = dict(gen.DEFAULTS)
    params["setup_id"] = "given-id"
    params["server_name"] = "given.example"
    params["tls_subject"] = "/CN=given"
    params["auth_mode"] = "auth"

    result = gen.coerce_and_derive(params)

    assert result["setup_id"] == "given-id"
    assert result["server_name"] == "given.example"
    assert result["tls_subject"] == "/CN=given"
    assert result["backend_auth_flag"] == ""


def test_coerce_derives_package_name_from_project_name():
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"
    params["project_name"] = "My-Cool-App"

    result = gen.coerce_and_derive(params)

    assert result["package_name"] == "my_cool_app"


def test_coerce_derives_frontend_release_path_for_pinned_version():
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"
    params["frontend_version"] = "v0.60.0"

    result = gen.coerce_and_derive(params)

    assert result["frontend_release_path"] == "tags/v0.60.0"


def test_coerce_derives_frontend_release_path_with_build_metadata():
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"
    params["frontend_version"] = "v0.87.1+56"

    result = gen.coerce_and_derive(params)

    assert result["frontend_release_path"] == "tags/v0.87.1+56"


def test_coerce_derives_uid_gid_from_host(monkeypatch):
    monkeypatch.setattr(gen.os, "getuid", lambda: 1234)
    monkeypatch.setattr(gen.os, "getgid", lambda: 5678)
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"

    result = gen.coerce_and_derive(params)

    assert result["puid"] == 1234
    assert result["pgid"] == 5678


def test_coerce_uid_gid_root_falls_back_to_1000(monkeypatch, capsys):
    monkeypatch.setattr(gen.os, "getuid", lambda: 0)
    monkeypatch.setattr(gen.os, "getgid", lambda: 0)
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"

    result = gen.coerce_and_derive(params)

    assert result["puid"] == 1000
    assert result["pgid"] == 1000
    assert "root" in capsys.readouterr().err


def test_coerce_keeps_explicit_uid_gid(monkeypatch):
    # Explicit values must win; os.getuid/getgid must not even be consulted.
    _no_call = mock.Mock(side_effect=AssertionError)
    monkeypatch.setattr(gen.os, "getuid", _no_call)
    monkeypatch.setattr(gen.os, "getgid", _no_call)
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"
    params["puid"] = "1500"
    params["pgid"] = "1600"

    result = gen.coerce_and_derive(params)

    assert result["puid"] == 1500
    assert result["pgid"] == 1600


def test_coerce_uid_not_int(monkeypatch):
    monkeypatch.setattr(gen.os, "getgid", lambda: 1000)
    params = dict(gen.DEFAULTS)
    params["ollama_base_url"] = "http://x"
    params["puid"] = "not-an-int"

    with pytest.raises(gen.GenError, match="puid must be an integer"):
        gen.coerce_and_derive(params)


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------
def test_validate_accepts_valid_params():
    assert gen.validate(_valid_params()) is None


def test_validate_requires_ollama():
    params = _valid_params()
    params["ollama_base_url"] = "  "

    with pytest.raises(gen.GenError, match="ollama_base_url is required"):
        gen.validate(params)


def test_validate_port_out_of_range():
    params = _valid_params()
    params["nginx_http"] = 0

    with pytest.raises(gen.GenError, match="out of range"):
        gen.validate(params)


def test_validate_duplicate_port():
    params = _valid_params()
    params["nginx_http"] = params["nginx_https"]

    with pytest.raises(gen.GenError, match="used by both"):
        gen.validate(params)


def test_validate_bad_sql_identifier():
    params = _valid_params()
    params["agui_db"] = "1bad"

    with pytest.raises(gen.GenError, match="valid SQL identifier"):
        gen.validate(params)


# "1invalid" is not an identifier (leading digit); "class" is a keyword. The
# two cases exercise both operands of the package-name guard.
@pytest.mark.parametrize("package_name", ["1invalid", "class"])
def test_validate_bad_package_name(package_name):
    params = _valid_params()
    params["package_name"] = package_name

    with pytest.raises(gen.GenError, match="not a valid Python identifier"):
        gen.validate(params)


@pytest.mark.parametrize("frontend_version", ["", "v 1", 'has"quote'])
def test_validate_bad_frontend_version(frontend_version):
    params = _valid_params()
    params["frontend_version"] = frontend_version

    with pytest.raises(gen.GenError, match="must be 'latest' or"):
        gen.validate(params)


# Recent soliplex/frontend release tags carry semver build metadata, e.g.
# "v0.87.1+56"; the '+' must pass validation (see issue #47).
@pytest.mark.parametrize(
    "frontend_version", ["latest", "v0.87.1+56", "v0.60.0"]
)
def test_validate_accepts_frontend_version(frontend_version):
    params = _valid_params()
    params["frontend_version"] = frontend_version

    assert gen.validate(params) is None


def test_validate_dbs_must_differ():
    params = _valid_params()
    params["authz_db"] = params["agui_db"]

    with pytest.raises(gen.GenError, match="must differ"):
        gen.validate(params)


# 0 rejects root; a negative and an out-of-range value exercise both bounds of
# the guard, and 'puid'/'pgid' cover both keys.
@pytest.mark.parametrize(
    "key, value",
    [
        ("puid", 0),
        ("puid", -1),
        ("pgid", 2**31),
    ],
)
def test_validate_bad_uid(key, value):
    params = _valid_params()
    params[key] = value

    with pytest.raises(gen.GenError, match="must be a positive integer"):
        gen.validate(params)


def test_validate_bad_auth_mode():
    params = _valid_params()
    params["auth_mode"] = "maybe"

    with pytest.raises(gen.GenError, match="auth_mode must be"):
        gen.validate(params)


def test_validate_empty_constraint():
    params = _valid_params()
    params["soliplex_backend_constraint"] = "   "

    with pytest.raises(gen.GenError, match="must not be empty"):
        gen.validate(params)


@pytest.mark.parametrize("docs_dir", ["/abs/path", "a/../b"])
def test_validate_bad_docs_dir(docs_dir):
    params = _valid_params()
    params["docs_dir"] = docs_dir

    with pytest.raises(gen.GenError, match="relative path inside the project"):
        gen.validate(params)


# --------------------------------------------------------------------------
# render_tree (real Mako)
# --------------------------------------------------------------------------
def test_render_tree_renders_copies_and_chmod(tmp_path):
    template = tmp_path / "template"
    (template / "sub").mkdir(parents=True)
    (template / "doc.txt.mako").write_text(
        "hi ${project_name}\n", encoding="utf-8"
    )
    (template / "plain.txt").write_text("verbatim\n", encoding="utf-8")
    (template / "sub" / "run.sh.mako").write_text(
        "#!/bin/sh\necho ${project_name}\n", encoding="utf-8"
    )
    out = tmp_path / "out"

    gen.render_tree(template, out, {"project_name": "demo"})

    assert (out / "doc.txt").read_text() == "hi demo\n"
    assert not (out / "doc.txt.mako").exists()
    assert (out / "plain.txt").read_text() == "verbatim\n"
    assert (out / "sub" / "run.sh").read_text() == "#!/bin/sh\necho demo\n"
    assert stat.S_IMODE((out / "sub" / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((out / "doc.txt").stat().st_mode) == 0o644


def test_render_tree_bad_template_raises(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "bad.txt.mako").write_text(
        "${undefined_var}\n", encoding="utf-8"
    )
    out = tmp_path / "out"

    with pytest.raises(gen.GenError, match="failed to render"):
        gen.render_tree(template, out, {"project_name": "demo"})


def test_render_tree_substitutes_package_dir(tmp_path):
    template = tmp_path / "template"
    (template / "src" / "__package__").mkdir(parents=True)
    (template / "src" / "__package__" / "sample.py.mako").write_text(
        "pkg = ${package_name}\n", encoding="utf-8"
    )
    out = tmp_path / "out"

    gen.render_tree(template, out, {"package_name": "my_pkg"})

    assert (out / "src" / "my_pkg" / "sample.py").read_text() == (
        "pkg = my_pkg\n"
    )
    assert not (out / "src" / "__package__").exists()


# --------------------------------------------------------------------------
# include_gitea (opt-in gitea service)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ig_kw, expected",
    [
        ({}, False),
        ({"include_gitea": False}, False),
        ({"include_gitea": "0"}, False),
        ({"include_gitea": "false"}, False),
        ({"include_gitea": "no"}, False),
        ({"include_gitea": "n"}, False),
        ({"include_gitea": "off"}, False),
        ({"include_gitea": True}, True),
        ({"include_gitea": "1"}, True),
        ({"include_gitea": "true"}, True),
        ({"include_gitea": "yes"}, True),
        ({"include_gitea": "y"}, True),
        ({"include_gitea": "on"}, True),
    ],
)
def test_coerce_and_derive_w_valid_include_gitea_converts(ig_kw, expected):
    params = dict(gen.DEFAULTS) | ig_kw

    result = gen.coerce_and_derive(params)

    assert result["include_gitea"] is expected


def test_coerce_and_derive_w_invalid_include_gitea_raises():
    params = dict(gen.DEFAULTS)
    params["include_gitea"] = "maybe"

    with pytest.raises(gen.GenError, match="include_gitea must be a boolean"):
        gen.coerce_and_derive(params)


def test_render_tree_omits_init_gitea_when_disabled(tmp_path):
    template = tmp_path / "template"
    (template / "scripts").mkdir(parents=True)
    (template / "scripts" / "init-gitea.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    out = tmp_path / "out"

    gen.render_tree(
        template, out, {"project_name": "demo", "include_gitea": False}
    )

    assert not (out / "scripts" / "init-gitea.sh").exists()


def test_render_tree_keeps_init_gitea_when_enabled(tmp_path):
    shebang = "#!/bin/sh\n"
    template = tmp_path / "template"
    (template / "scripts").mkdir(parents=True)
    (template / "scripts" / "init-gitea.sh").write_text(
        shebang, encoding="utf-8"
    )
    out = tmp_path / "out"

    gen.render_tree(
        template, out, {"project_name": "demo", "include_gitea": True}
    )

    assert (out / "scripts" / "init-gitea.sh").read_text() == shebang


# --------------------------------------------------------------------------
# ensure_runtime_dirs
# --------------------------------------------------------------------------
def test_ensure_runtime_dirs(tmp_path):
    (tmp_path / "rag" / "docs").mkdir(parents=True)
    (tmp_path / "rag" / "docs" / "existing.txt").write_text(
        "x", encoding="utf-8"
    )

    gen.ensure_runtime_dirs(tmp_path, "rag/docs")

    assert (tmp_path / "backend" / "uploads" / "rooms" / ".gitkeep").is_file()
    assert (
        tmp_path / "backend" / "uploads" / "threads" / ".gitkeep"
    ).is_file()
    assert not (tmp_path / "rag" / "docs" / ".gitkeep").exists()


# --------------------------------------------------------------------------
# write_env
# --------------------------------------------------------------------------
def test_write_env(tmp_path):
    params = {
        "ollama_base_url": "http://o:11434",
        "ingester_token": "tok",
        "puid": 1234,
        "pgid": 5678,
    }

    gen.write_env(tmp_path, params)

    text = (tmp_path / ".env").read_text()
    assert "OLLAMA_BASE_URL=http://o:11434" in text
    assert "INGESTER_TOKEN=tok" in text
    assert "PUID=1234" in text
    assert "PGID=5678" in text


# --------------------------------------------------------------------------
# maybe_run_secrets
# --------------------------------------------------------------------------
def test_maybe_run_secrets_disabled(run):
    result = gen.maybe_run_secrets(pathlib.Path("/nowhere"), run=False)

    assert result is False
    run.assert_not_called()


@pytest.mark.parametrize(
    "present, expected_which",
    [
        # bash missing -> probe stops at bash (short-circuit on the `or`).
        ({"bash": None, "openssl": "/usr/bin/openssl"}, [mock.call("bash")]),
        # bash present, openssl missing -> both are probed, in order.
        (
            {"bash": "/bin/bash", "openssl": None},
            [mock.call("bash"), mock.call("openssl")],
        ),
    ],
)
def test_maybe_run_secrets_missing_tools(
    which, run, capsys, present, expected_which
):
    which.side_effect = lambda name: present[name]

    result = gen.maybe_run_secrets(pathlib.Path("/nowhere"), run=True)

    assert result is False
    assert "skipped generate-secrets.sh" in capsys.readouterr().out
    assert which.call_args_list == expected_which
    run.assert_not_called()


def test_maybe_run_secrets_runs(which, run, tmp_path):
    which.return_value = "/usr/bin/tool"

    result = gen.maybe_run_secrets(tmp_path, run=True)

    assert result is True
    assert which.call_args_list == [mock.call("bash"), mock.call("openssl")]
    run.assert_called_once_with(
        ["bash", str(tmp_path / "scripts" / "generate-secrets.sh")],
        cwd=tmp_path,
        check=True,
    )


# --------------------------------------------------------------------------
# maybe_git_init
# --------------------------------------------------------------------------
def test_maybe_git_init_disabled(run):
    result = gen.maybe_git_init(
        pathlib.Path("/nowhere"), do_git=False, disable_gpg_sign=False
    )

    assert result is False
    run.assert_not_called()


def test_maybe_git_init_no_git(which, run, capsys):
    which.return_value = None

    result = gen.maybe_git_init(
        pathlib.Path("/nowhere"), do_git=True, disable_gpg_sign=False
    )

    assert result is False
    assert "skipped git init" in capsys.readouterr().out
    which.assert_called_once_with("git")
    run.assert_not_called()


def test_maybe_git_init_commits(which, run, monkeypatch, tmp_path):
    which.return_value = "/usr/bin/git"
    # The pre-existing value proves the code forces GIT_TERMINAL_PROMPT to "0".
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    expected_env = {**gen.os.environ, "GIT_TERMINAL_PROMPT": "0"}

    result = gen.maybe_git_init(tmp_path, do_git=True, disable_gpg_sign=False)

    assert result is True
    which.assert_called_once_with("git")
    init_call, add_call, commit_call = run.call_args_list
    assert init_call == mock.call(
        ["git", "init", "-q"], cwd=tmp_path, check=True, env=expected_env
    )
    assert add_call == mock.call(
        ["git", "add", "-A"], cwd=tmp_path, check=True, env=expected_env
    )
    assert commit_call == mock.call(
        [
            "git",
            "-c",
            "user.name=soliplex-template",
            "-c",
            "user.email=noreply@soliplex.invalid",
            "commit",
            "-q",
            "-m",
            "Initial Soliplex project scaffolded by soliplex-template",
        ],
        cwd=tmp_path,
        check=True,
        env=expected_env,
    )


def test_maybe_git_init_disable_gpg_sign(which, run, monkeypatch, tmp_path):
    which.return_value = "/usr/bin/git"
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    expected_env = {**gen.os.environ, "GIT_TERMINAL_PROMPT": "0"}

    gen.maybe_git_init(tmp_path, do_git=True, disable_gpg_sign=True)

    which.assert_called_once_with("git")
    init_call, add_call, commit_call = run.call_args_list
    assert init_call == mock.call(
        ["git", "init", "-q"], cwd=tmp_path, check=True, env=expected_env
    )
    assert add_call == mock.call(
        ["git", "add", "-A"], cwd=tmp_path, check=True, env=expected_env
    )
    assert commit_call == mock.call(
        [
            "git",
            "-c",
            "user.name=soliplex-template",
            "-c",
            "user.email=noreply@soliplex.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "Initial Soliplex project scaffolded by soliplex-template",
        ],
        cwd=tmp_path,
        check=True,
        env=expected_env,
    )


# --------------------------------------------------------------------------
# parse_args
# --------------------------------------------------------------------------
def test_parse_args_defaults():
    args = gen.parse_args([])

    assert args.out is None
    assert args.interactive is False


def test_parse_args_flags():
    args = gen.parse_args(
        [
            "--out",
            "x",
            "--interactive",
            "--force",
            "--no-generate-secrets",
            "--no-git",
            "--disable-gpg-sign",
            "--print-defaults",
        ]
    )

    assert args.out == "x"
    assert args.interactive
    assert args.force
    assert args.generate_secrets is False
    assert args.no_git
    assert args.disable_gpg_sign
    assert args.print_defaults


def test_parse_args_generate_secrets_defaults_on():
    args = gen.parse_args(["--out", "x"])

    assert args.generate_secrets is True


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def test_main_print_defaults(capsys):
    rc = gen.main(["--print-defaults"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == gen.DEFAULTS


def test_main_requires_out():
    with pytest.raises(gen.GenError, match="--out is required"):
        gen.main([])


def test_main_missing_template(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gen,
        "__file__",
        str(tmp_path / "skill" / "scripts" / "generate_soliplex_project.py"),
    )

    with pytest.raises(gen.GenError, match="embedded template not found"):
        gen.main(["--out", str(tmp_path / "proj")])


def test_main_out_not_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gen,
        "__file__",
        str(tmp_path / "skill" / "scripts" / "generate_soliplex_project.py"),
    )
    (tmp_path / "skill" / "assets" / "template").mkdir(parents=True)
    out = tmp_path / "proj"
    out.mkdir()
    (out / "stale").write_text("x", encoding="utf-8")

    with pytest.raises(gen.GenError, match="not empty"):
        gen.main(["--out", str(out)])


# The parameter dict main() threads through its (mocked) helpers. load_params
# returns it and coerce_and_derive passes it straight through, so call-arg
# assertions can compare against PARAMS directly.
PARAMS = {
    "project_name": "demo",
    "nginx_http": 9000,
    "nginx_https": 9443,
    "ingester_port": 8765,
    "docling_port": 5001,
    "postgres_port": 5432,
    "chat_model": "m",
    "title_model": "m",
    "rag_qa_model": "m",
    "ollama_base_url": "http://o",
    "auth_mode": "no-auth",
    "include_gitea": False,
    "docs_dir": "rag/docs",
}


@pytest.fixture
def skill_path(tmp_path):
    return tmp_path / "skill"


@pytest.fixture
def skill_scripts_path(skill_path):
    return skill_path / "scripts"


@pytest.fixture
def generate_script_path(monkeypatch, skill_scripts_path):
    result = skill_scripts_path / "generate_soliplex_project.py"
    monkeypatch.setattr(gen, "__file__", str(result))
    return result


@pytest.fixture
def assets_path(skill_path):
    return skill_path / "assets"


@pytest.fixture
def template_root_path(assets_path):
    result = assets_path / "template"
    result.mkdir(parents=True)
    return result


@pytest.fixture
def out_path(tmp_path):
    return tmp_path / "proj"


@pytest.fixture
def load_params(monkeypatch):
    load_params = mock.Mock(return_value=PARAMS)
    monkeypatch.setattr(gen, "load_params", load_params)
    return load_params


@pytest.fixture
def coerce_and_derive(monkeypatch):
    coerce_and_derive = mock.Mock(side_effect=lambda params: params)
    monkeypatch.setattr(gen, "coerce_and_derive", coerce_and_derive)
    return coerce_and_derive


@pytest.fixture
def validate(monkeypatch):
    validate = mock.Mock()
    monkeypatch.setattr(gen, "validate", validate)
    return validate


@pytest.fixture
def render_tree(monkeypatch):
    render_tree = mock.Mock()
    monkeypatch.setattr(gen, "render_tree", render_tree)
    return render_tree


@pytest.fixture
def ensure_runtime_dirs(monkeypatch):
    ensure_runtime_dirs = mock.Mock()
    monkeypatch.setattr(gen, "ensure_runtime_dirs", ensure_runtime_dirs)
    return ensure_runtime_dirs


@pytest.fixture
def write_env(monkeypatch):
    write_env = mock.Mock()
    monkeypatch.setattr(gen, "write_env", write_env)
    return write_env


@pytest.fixture
def maybe_run_secrets(monkeypatch):
    maybe_run_secrets = mock.Mock(return_value=False)
    monkeypatch.setattr(gen, "maybe_run_secrets", maybe_run_secrets)
    return maybe_run_secrets


@pytest.fixture
def maybe_git_init(monkeypatch):
    maybe_git_init = mock.Mock(return_value=False)
    monkeypatch.setattr(gen, "maybe_git_init", maybe_git_init)
    return maybe_git_init


def test_main_happy_no_secrets_no_git(
    generate_script_path,
    out_path,
    template_root_path,
    load_params,
    coerce_and_derive,
    validate,
    render_tree,
    ensure_runtime_dirs,
    write_env,
    maybe_run_secrets,
    maybe_git_init,
    capsys,
):
    rc = gen.main(["--out", str(out_path), "--no-generate-secrets"])

    captured = capsys.readouterr().out
    assert rc == 0
    assert "generate-secrets.sh" in captured
    assert "not initialized" in captured
    load_params.assert_called_once()
    coerce_and_derive.assert_called_once_with(PARAMS)
    validate.assert_called_once_with(PARAMS)
    expected_ctx = {
        **PARAMS,
        "soliplex_template_manifest": gen.render_manifest(
            PARAMS, gen.read_skill_metadata(gen.SKILL_DIR)
        ),
    }
    render_tree.assert_called_once_with(
        template_root_path.resolve(), out_path.resolve(), expected_ctx
    )
    ensure_runtime_dirs.assert_called_once_with(out_path.resolve(), "rag/docs")
    write_env.assert_called_once_with(out_path.resolve(), PARAMS)
    maybe_run_secrets.assert_called_once_with(out_path.resolve(), False)
    maybe_git_init.assert_called_once_with(out_path.resolve(), True, False)


def test_main_happy_with_secrets_and_git(
    generate_script_path,
    out_path,
    template_root_path,
    load_params,
    coerce_and_derive,
    validate,
    render_tree,
    ensure_runtime_dirs,
    write_env,
    maybe_run_secrets,
    maybe_git_init,
    capsys,
):
    maybe_run_secrets.return_value = True
    maybe_git_init.return_value = True

    rc = gen.main(["--out", str(out_path.resolve())])

    captured = capsys.readouterr().out
    assert rc == 0
    assert "initial commit created" in captured
    assert "generate-secrets.sh" not in captured
    # secrets generation now defaults on, so main() passes True with no flag.
    maybe_run_secrets.assert_called_once_with(out_path.resolve(), True)
    maybe_git_init.assert_called_once_with(out_path.resolve(), True, False)


# --------------------------------------------------------------------------
# generation manifest (soliplex-template#73)
# --------------------------------------------------------------------------
def test_read_skill_metadata_stamped(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\n"
        "name: soliplex-template\n"
        "description: generation skill\n"
        "metadata:\n"
        '  version: "0.8.0"\n'
        '  source_commit: "deadbee"\n'
        '  generated: "2026-06-08"\n'
        "---\n\n# heading\n",
        encoding="utf-8",
    )

    meta = gen.read_skill_metadata(tmp_path)

    assert meta == {
        "version": "0.8.0",
        "source_commit": "deadbee",
        "generated": "2026-06-08",
    }


def test_read_skill_metadata_unstamped(tmp_path):
    # A valid (name + description) but unstamped SKILL.md -> blank fields,
    # so old skill builds keep generating.
    (tmp_path / "SKILL.md").write_text(
        "---\nname: soliplex-template\ndescription: generation skill\n---\n",
        encoding="utf-8",
    )

    meta = gen.read_skill_metadata(tmp_path)

    assert meta == {"version": "", "source_commit": "", "generated": ""}


def test_sanitize_param_strips_url_userinfo_without_port():
    result = gen._sanitize_param(
        "ollama_base_url", "http://user:pass@ollama.invalid"
    )

    assert result == "http://ollama.invalid"


def test_sanitize_param_redacts_secret():
    result = gen._sanitize_param("ingester_token", "hunter2")

    assert result == "<redacted>"


def test_sanitize_param_strips_url_userinfo():
    result = gen._sanitize_param(
        "ollama_base_url", "http://user:pass@ollama.invalid:11434"
    )

    assert result == "http://ollama.invalid:11434"


def test_sanitize_param_passthrough():
    result = gen._sanitize_param("project_name", "soliplex")

    assert result == "soliplex"


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, "true"),
        (False, "false"),
        (9443, "9443"),
        ("plain", '"plain"'),
        ('has "quote"', '"has \\"quote\\""'),
    ],
)
def test_toml_scalar(value, expected):
    assert gen._toml_scalar(value) == expected


def test_render_manifest_structure():
    params = {"project_name": "demo", "nginx_https": 9443, "x": True}
    meta = {
        "version": "0.8.0",
        "source_commit": "deadbee",
        "generated": "2026-06-08",
    }

    out = gen.render_manifest(params, meta)

    assert "[tool.soliplex-template]" in out
    assert 'skill_name = "soliplex-template"' in out
    assert 'skill_version = "0.8.0"' in out
    assert 'skill_source_commit = "deadbee"' in out
    assert 'skill_generated = "2026-06-08"' in out
    assert "[tool.soliplex-template.params]" in out
    assert 'project_name = "demo"' in out
    assert "nginx_https = 9443" in out
    assert "x = true" in out


def test_render_manifest_blank_skill_fields_and_redaction():
    params = {"ingester_token": "secret"}
    meta = {"version": "", "source_commit": "", "generated": ""}

    out = gen.render_manifest(params, meta)

    assert 'skill_version = ""' in out
    assert 'skill_source_commit = ""' in out
    assert 'skill_generated = ""' in out
    assert 'ingester_token = "<redacted>"' in out
