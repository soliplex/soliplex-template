"""Unit tests for the bundled ``skill/scripts/generate.py`` scaffolder.

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
    / "skill"
    / "scripts"
    / "generate.py"
)
_spec = importlib.util.spec_from_file_location("generate", _MODULE_PATH)
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
        run_secrets=False,
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
    assert result["tls_subject"].endswith("CN=localhost")
    assert result["backend_auth_flag"] == "--no-auth-mode "
    assert result["nginx_http"] == 9000


def test_coerce_int_error():
    params = dict(gen.DEFAULTS)
    params["chunk_size"] = "not-an-int"

    with pytest.raises(gen.GenError, match="chunk_size must be an integer"):
        gen.coerce_and_derive(params)


def test_coerce_keeps_supplied_values_and_auth_mode():
    params = dict(gen.DEFAULTS)
    params["setup_id"] = "given-id"
    params["tls_subject"] = "/CN=given"
    params["auth_mode"] = "auth"

    result = gen.coerce_and_derive(params)

    assert result["setup_id"] == "given-id"
    assert result["tls_subject"] == "/CN=given"
    assert result["backend_auth_flag"] == ""


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


def test_validate_dbs_must_differ():
    params = _valid_params()
    params["authz_db"] = params["agui_db"]

    with pytest.raises(gen.GenError, match="must differ"):
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
    params = {"ollama_base_url": "http://o:11434", "ingester_token": "tok"}

    gen.write_env(tmp_path, params)

    text = (tmp_path / ".env").read_text()
    assert "OLLAMA_BASE_URL=http://o:11434" in text
    assert "INGESTER_TOKEN=tok" in text


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
            "--run-secrets",
            "--no-git",
            "--disable-gpg-sign",
            "--print-defaults",
        ]
    )

    assert args.out == "x"
    assert args.interactive and args.force and args.run_secrets
    assert args.no_git and args.disable_gpg_sign and args.print_defaults


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
        gen, "__file__", str(tmp_path / "skill" / "scripts" / "generate.py")
    )

    with pytest.raises(gen.GenError, match="embedded template not found"):
        gen.main(["--out", str(tmp_path / "proj")])


def test_main_out_not_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gen, "__file__", str(tmp_path / "skill" / "scripts" / "generate.py")
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
    result = skill_scripts_path / "generate.py"
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
    rc = gen.main(["--out", str(out_path)])

    captured = capsys.readouterr().out
    assert rc == 0
    assert "generate-secrets.sh" in captured
    assert "not initialized" in captured
    load_params.assert_called_once()
    coerce_and_derive.assert_called_once_with(PARAMS)
    validate.assert_called_once_with(PARAMS)
    render_tree.assert_called_once_with(
        template_root_path.resolve(), out_path.resolve(), PARAMS
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
    maybe_run_secrets.assert_called_once_with(out_path.resolve(), False)
    maybe_git_init.assert_called_once_with(out_path.resolve(), True, False)
