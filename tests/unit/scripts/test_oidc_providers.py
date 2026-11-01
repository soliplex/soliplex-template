"""Unit tests for the bundled ``skill/scripts/oidc_providers.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``
(and registered in ``sys.modules``, which its dataclasses need). Tests write
a stack's OIDC config under ``tmp_path``.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import importlib.util
import pathlib
import runpy
import stat
import sys
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "oidc_providers.py"
)
_spec = importlib.util.spec_from_file_location("oidc_providers", _MODULE_PATH)
oidc_providers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = oidc_providers
_spec.loader.exec_module(oidc_providers)

PEM = "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----"

HEADER = """\
#
# OIDC Auth Systems
#
oidc_client_pem_path: "./cacert.pem"

"""

EMPTY = HEADER + "auth_systems: []\n"

# Two entries, a comment inside the first, and a key after the list.
TWO = (
    HEADER
    + """\
auth_systems:

  - id: "alpha"
    # the first one
    title: "Alpha"
    token_validation_pem: |
        -----BEGIN PUBLIC KEY-----
        AAAA
        -----END PUBLIC KEY-----

  - id: "beta"
    title: "Beta"
    accepted_azp_list:
      - beta-client

allowed_frontend_origins: []
"""
)

AUTHELIA_LINES = """\
  - id: authelia
    title: Authelia
    server_url: https://example.com/authelia
    client_id: soliplex
    client_secret: ''
    scope: openid email profile
    token_validation_pem: |
      -----BEGIN PUBLIC KEY-----
      MIIB
      -----END PUBLIC KEY-----
"""


def _provider(provider_id="authelia", **kwargs):
    return oidc_providers.Provider(
        id=provider_id,
        title=kwargs.pop("title", "Authelia"),
        server_url="https://example.com/authelia",
        client_id="soliplex",
        token_validation_pem=kwargs.pop("token_validation_pem", PEM),
        **kwargs,
    )


def _stack(tmp_path, config=EMPTY, signin=None) -> pathlib.Path:
    path = tmp_path / oidc_providers.CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(config)
    if signin is not None:
        fragment = tmp_path / oidc_providers.SIGNIN_FRAGMENT_PATH
        fragment.parent.mkdir(parents=True)
        fragment.write_text(f'set $soliplex_signin_provider "{signin}";\n')
    return tmp_path


def _config(stack) -> str:
    return (stack / oidc_providers.CONFIG_PATH).read_text()


def _fragment(stack) -> pathlib.Path:
    return stack / oidc_providers.SIGNIN_FRAGMENT_PATH


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------
def test_provider_as_entry_normalizes_pem_whitespace():
    provider = _provider(token_validation_pem=f"\n  {PEM}\n\n")

    entry = provider.as_entry()

    assert entry == {
        "id": "authelia",
        "title": "Authelia",
        "server_url": "https://example.com/authelia",
        "client_id": "soliplex",
        "client_secret": "",
        "scope": "openid email profile",
        "token_validation_pem": PEM + "\n",
    }


# --------------------------------------------------------------------------
# load_providers
# --------------------------------------------------------------------------
def test_load_providers_in_file_order(tmp_path):
    stack = _stack(tmp_path, TWO)

    entries = oidc_providers.load_providers(stack)

    assert [e["id"] for e in entries] == ["alpha", "beta"]


def test_load_providers_null_list_is_empty(tmp_path):
    stack = _stack(tmp_path, HEADER + "auth_systems:\n")

    entries = oidc_providers.load_providers(stack)

    assert entries == []


def test_load_providers_config_missing(tmp_path):
    with pytest.raises(oidc_providers.ConfigNotFound) as caught:
        oidc_providers.load_providers(tmp_path)

    assert caught.value.path == tmp_path / oidc_providers.CONFIG_PATH


@pytest.mark.parametrize(
    "config, reason",
    [
        ("auth_systems: [\n", "not valid YAML"),
        ("- a\n- b\n", "no top-level 'auth_systems' key"),
        ("oidc_client_pem_path: x\n", "no top-level 'auth_systems' key"),
        ("auth_systems:\n  id: a\n", "is not a list of entries"),
        ("auth_systems:\n  - title: a\n", "is not a list of entries"),
        ("auth_systems:\n  - id: 7\n", "is not a list of entries"),
    ],
)
def test_load_providers_bad_config(tmp_path, config, reason):
    stack = _stack(tmp_path, config)

    with pytest.raises(oidc_providers.BadConfig) as caught:
        oidc_providers.load_providers(stack)

    assert reason in caught.value.reason


# --------------------------------------------------------------------------
# signin_provider / set_signin_provider
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fragment, expected",
    [
        (None, None),
        ('set $soliplex_signin_provider "alpha";\n', "alpha"),
        ("# no set line\n", None),
    ],
)
def test_signin_provider(tmp_path, fragment, expected):
    stack = _stack(tmp_path)
    if fragment is not None:
        _fragment(stack).parent.mkdir(parents=True)
        _fragment(stack).write_text(fragment)

    found = oidc_providers.signin_provider(stack)

    assert found == expected


def test_set_signin_provider_writes_fragment(tmp_path):
    stack = _stack(tmp_path, TWO, signin="alpha")

    oidc_providers.set_signin_provider(stack, "beta")

    assert oidc_providers.signin_provider(stack) == "beta"


@pytest.mark.parametrize(
    "provider_id, error",
    [
        ("gamma", oidc_providers.ProviderNotFound),
        ('x"; return 200', oidc_providers.BadProviderId),
    ],
)
def test_set_signin_provider_refuses(tmp_path, provider_id, error):
    stack = _stack(tmp_path, TWO, signin="alpha")

    with pytest.raises(error):
        oidc_providers.set_signin_provider(stack, provider_id)

    assert oidc_providers.signin_provider(stack) == "alpha"


# --------------------------------------------------------------------------
# add_provider
# --------------------------------------------------------------------------
def test_add_provider_first_to_flow_empty_config(tmp_path):
    stack = _stack(tmp_path)

    result = oidc_providers.add_provider(stack, _provider())

    assert result == oidc_providers.AddResult(first=True, signin="authelia")
    assert _config(stack) == HEADER + "auth_systems:\n\n" + AUTHELIA_LINES
    assert oidc_providers.signin_provider(stack) == "authelia"
    assert "docker compose restart nginx" in _fragment(stack).read_text()


def test_add_provider_appends_and_keeps_the_rest(tmp_path):
    stack = _stack(tmp_path, TWO, signin="alpha")
    before, after = TWO.split("\nallowed_frontend_origins")

    result = oidc_providers.add_provider(stack, _provider())

    assert result == oidc_providers.AddResult(first=False, signin="alpha")
    assert _config(stack) == (
        before.rstrip("\n")
        + "\n\n"
        + AUTHELIA_LINES
        + "\nallowed_frontend_origins"
        + after
    )
    assert oidc_providers.signin_provider(stack) == "alpha"


def test_add_provider_at_column_zero(tmp_path):
    stack = _stack(tmp_path, "auth_systems:\n- id: alpha\n")

    oidc_providers.add_provider(stack, _provider())

    expected = "\n".join(line[2:] for line in AUTHELIA_LINES.splitlines())
    assert _config(stack) == f"auth_systems:\n- id: alpha\n\n{expected}\n"


def test_add_provider_adds_missing_trailing_newline(tmp_path):
    stack = _stack(tmp_path, "auth_systems: []")

    oidc_providers.add_provider(stack, _provider())

    assert _config(stack) == "auth_systems:\n\n" + AUTHELIA_LINES


def test_add_provider_selects_signin_when_asked(tmp_path):
    stack = _stack(tmp_path, TWO, signin="alpha")

    result = oidc_providers.add_provider(
        stack, _provider(), use_for_signin=True
    )

    assert result.signin == "authelia"
    assert oidc_providers.signin_provider(stack) == "authelia"


def test_add_provider_selects_signin_when_none_set(tmp_path):
    stack = _stack(tmp_path, TWO)

    result = oidc_providers.add_provider(stack, _provider())

    assert result == oidc_providers.AddResult(first=False, signin="authelia")
    assert oidc_providers.signin_provider(stack) == "authelia"


def test_add_provider_keeps_file_mode(tmp_path):
    stack = _stack(tmp_path)
    (stack / oidc_providers.CONFIG_PATH).chmod(0o640)

    oidc_providers.add_provider(stack, _provider())

    mode = (stack / oidc_providers.CONFIG_PATH).stat().st_mode
    assert stat.S_IMODE(mode) == 0o640


def test_add_provider_exists(tmp_path):
    stack = _stack(tmp_path, TWO)

    with pytest.raises(oidc_providers.ProviderExists):
        oidc_providers.add_provider(stack, _provider("alpha"))

    assert _config(stack) == TWO


@pytest.mark.parametrize(
    "provider_id",
    ["", "Upper", "-leading", "has space", 'quote"', "dot.ted"],
)
def test_add_provider_bad_id(tmp_path, provider_id):
    stack = _stack(tmp_path)

    with pytest.raises(oidc_providers.BadProviderId):
        oidc_providers.add_provider(stack, _provider(provider_id))

    assert _config(stack) == EMPTY


@pytest.mark.parametrize(
    "config",
    [
        # a non-empty flow list: no plain 'auth_systems:' line to edit
        "auth_systems: [{id: alpha}]\n",
        # a flow entry inside a block list: entries can't be found
        "auth_systems:\n  - {id: alpha}\n  - id: beta\n    x: [\n     1]\n"
        "  -\n    id: gamma\n",
    ],
)
def test_add_provider_unrecognized_layout(tmp_path, config):
    stack = _stack(tmp_path, config)

    with pytest.raises(oidc_providers.BadConfig):
        oidc_providers.add_provider(stack, _provider())

    assert _config(stack) == config


@pytest.mark.parametrize(
    "rendered",
    [
        ["  - id: someone-else"],  # parses, but not as the entry written
        ["  - id: [unclosed"],  # does not parse
    ],
)
def test_add_provider_edit_mismatch(tmp_path, rendered):
    stack = _stack(tmp_path)

    with (
        mock.patch.object(
            oidc_providers, "_render_entry", return_value=rendered
        ),
        pytest.raises(oidc_providers.EditMismatch),
    ):
        oidc_providers.add_provider(stack, _provider())

    assert _config(stack) == EMPTY
    assert not _fragment(stack).exists()


def test_add_provider_failed_write_leaves_no_temp_file(tmp_path):
    stack = _stack(tmp_path)

    with (
        mock.patch.object(
            oidc_providers.os, "replace", side_effect=OSError("disk full")
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        oidc_providers.add_provider(stack, _provider())

    assert _config(stack) == EMPTY
    assert [
        p.name for p in (stack / oidc_providers.CONFIG_PATH).parent.iterdir()
    ] == ["config.yaml"]


# --------------------------------------------------------------------------
# remove_provider
# --------------------------------------------------------------------------
def test_remove_provider_keeps_the_rest(tmp_path):
    stack = _stack(tmp_path, TWO, signin="beta")

    result = oidc_providers.remove_provider(stack, "alpha")

    assert result == oidc_providers.RemoveResult(
        remaining=["beta"], signin="beta"
    )
    assert _config(stack) == (
        HEADER
        + """\
auth_systems:

  - id: "beta"
    title: "Beta"
    accepted_azp_list:
      - beta-client

allowed_frontend_origins: []
"""
    )
    assert oidc_providers.signin_provider(stack) == "beta"


def test_remove_provider_without_blank_separator(tmp_path):
    stack = _stack(tmp_path, "auth_systems:\n- id: alpha\n- id: beta\n")

    oidc_providers.remove_provider(stack, "beta")

    assert _config(stack) == "auth_systems:\n- id: alpha\n"


def test_remove_provider_last(tmp_path):
    stack = _stack(tmp_path, HEADER + "auth_systems:\n\n" + AUTHELIA_LINES)
    oidc_providers.set_signin_provider(stack, "authelia")

    result = oidc_providers.remove_provider(stack, "authelia")

    assert result == oidc_providers.RemoveResult(remaining=[], signin=None)
    assert _config(stack) == EMPTY
    assert not _fragment(stack).exists()


def test_remove_provider_signin_needs_successor(tmp_path):
    stack = _stack(tmp_path, TWO, signin="alpha")

    with pytest.raises(oidc_providers.SigninSuccessorRequired) as caught:
        oidc_providers.remove_provider(stack, "alpha")

    assert caught.value.remaining == ["beta"]
    assert _config(stack) == TWO
    assert oidc_providers.signin_provider(stack) == "alpha"


@pytest.mark.parametrize("signin", ["alpha", "beta", None])
def test_remove_provider_with_successor(tmp_path, signin):
    stack = _stack(tmp_path, TWO, signin=signin)
    oidc_providers.add_provider(stack, _provider())

    result = oidc_providers.remove_provider(
        stack, "alpha", signin_successor="authelia"
    )

    assert result == oidc_providers.RemoveResult(
        remaining=["beta", "authelia"], signin="authelia"
    )
    assert oidc_providers.signin_provider(stack) == "authelia"


def test_remove_provider_without_fragment(tmp_path):
    stack = _stack(tmp_path, TWO)

    result = oidc_providers.remove_provider(stack, "beta")

    assert result == oidc_providers.RemoveResult(
        remaining=["alpha"], signin=None
    )
    assert not _fragment(stack).exists()


@pytest.mark.parametrize(
    "provider_id, successor, error",
    [
        ("gamma", None, oidc_providers.ProviderNotFound),
        ("Alpha", None, oidc_providers.BadProviderId),
        ("alpha", "gamma", oidc_providers.ProviderNotFound),
        ("alpha", "alpha", oidc_providers.ProviderNotFound),
        ("alpha", "Beta", oidc_providers.BadProviderId),
    ],
)
def test_remove_provider_refuses(tmp_path, provider_id, successor, error):
    stack = _stack(tmp_path, TWO, signin="alpha")

    with pytest.raises(error):
        oidc_providers.remove_provider(
            stack, provider_id, signin_successor=successor
        )

    assert _config(stack) == TWO
    assert oidc_providers.signin_provider(stack) == "alpha"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def _main(stack, *argv):
    return oidc_providers.main(["--project-dir", str(stack), *argv])


def _pem_file(tmp_path) -> pathlib.Path:
    path = tmp_path / "key.pem"
    path.write_text(PEM + "\n")
    return path


def _add_argv(tmp_path, provider_id="authelia", *extra):
    return [
        "add",
        "--id",
        provider_id,
        "--title",
        "Authelia",
        "--server-url",
        "https://example.com/authelia",
        "--client-id",
        "soliplex",
        "--pem-file",
        str(_pem_file(tmp_path)),
        *extra,
    ]


def test_main_list_empty(tmp_path, capsys):
    stack = _stack(tmp_path)

    code = _main(stack, "list")

    assert code == 0
    assert "runs without authentication" in capsys.readouterr().out


def test_main_list_marks_signin(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="beta")

    code = _main(stack, "list")

    out = capsys.readouterr().out
    assert code == 0
    assert "  alpha  Alpha  \n" in out
    assert "* beta  Beta  \n" in out


def test_main_add_first(tmp_path, capsys):
    stack = _stack(tmp_path)

    code = _main(stack, *_add_argv(tmp_path))

    out = capsys.readouterr().out
    assert code == 0
    assert _config(stack) == HEADER + "auth_systems:\n\n" + AUTHELIA_LINES
    assert oidc_providers.RESTART_NGINX in out
    assert "authentication is now ON" in out
    assert oidc_providers.ADMIN_USERS_ADD in out


def test_main_add_another(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, *_add_argv(tmp_path, "gamma", "--scope", "openid"))

    out = capsys.readouterr().out
    assert code == 0
    assert oidc_providers.load_providers(stack)[-1]["scope"] == "openid"
    assert oidc_providers.RESTART_NGINX not in out
    assert "authentication is now ON" not in out


def test_main_add_signin(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, *_add_argv(tmp_path, "gamma", "--signin"))

    assert code == 0
    assert oidc_providers.signin_provider(stack) == "gamma"
    assert oidc_providers.RESTART_NGINX in capsys.readouterr().out


def test_main_remove_last(tmp_path, capsys):
    stack = _stack(tmp_path, HEADER + "auth_systems:\n\n" + AUTHELIA_LINES)
    oidc_providers.set_signin_provider(stack, "authelia")

    code = _main(stack, "remove", "authelia")

    out = capsys.readouterr().out
    assert code == 0
    assert _config(stack) == EMPTY
    assert oidc_providers.RESTART_NGINX in out
    assert "WITHOUT authentication" in out
    assert oidc_providers.STACK_INIT in out


def test_main_remove_other(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, "remove", "beta")

    out = capsys.readouterr().out
    assert code == 0
    assert oidc_providers.RESTART_NGINX not in out
    assert "WITHOUT authentication" not in out


def test_main_remove_with_successor(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, "remove", "alpha", "--signin-successor", "beta")

    assert code == 0
    assert oidc_providers.signin_provider(stack) == "beta"
    assert oidc_providers.RESTART_NGINX in capsys.readouterr().out


def test_main_signin(tmp_path, capsys):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, "signin", "beta")

    assert code == 0
    assert oidc_providers.signin_provider(stack) == "beta"
    assert oidc_providers.RESTART_NGINX in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv, message",
    [
        (["remove", "gamma"], "no provider 'gamma'"),
        (["signin", "gamma"], "no provider 'gamma'"),
        (
            [
                "add",
                "--id",
                "x",
                "--title",
                "X",
                "--server-url",
                "https://x",
                "--client-id",
                "x",
                "--pem-file",
                "/nonexistent/key.pem",
            ],
            "/nonexistent/key.pem",
        ),
    ],
)
def test_main_reports_errors(tmp_path, capsys, argv, message):
    stack = _stack(tmp_path, TWO, signin="alpha")

    code = _main(stack, *argv)

    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error: ")
    assert message in err
    assert _config(stack) == TWO


def test_script_entry_point(tmp_path, capsys):
    stack = _stack(tmp_path)
    argv = ["oidc_providers.py", "--project-dir", str(stack), "list"]

    with (
        mock.patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as caught,
    ):
        runpy.run_path(str(_MODULE_PATH), run_name="__main__")

    assert caught.value.code == 0
    assert "runs without authentication" in capsys.readouterr().out
