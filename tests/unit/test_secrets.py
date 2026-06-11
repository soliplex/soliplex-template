"""Unit tests for the generic ``soliplex_template.secrets`` core.

It ships as an installed package, so it is imported directly. Tests are
hermetic: everything is routed through ``tmp_path`` and the ``os`` / ``shutil``
/ ``subprocess`` seams are monkeypatched -- no Docker, no real uid changes.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import stat
from unittest import mock

import pytest

from soliplex_template import secrets as st_secrets

_COMPOSE = (
    "services:\n"
    "  postgres:\n"
    "    image: x\n"
    "secrets:\n"
    "  agui_db_password:\n"
    "    file: ./.secrets/agui_db_password.gen\n"
    "  other_token:\n"
    "    file: .secrets/other_token.gen\n"
    "  not_a_secret:\n"
    "    file: ./config/whatever.conf\n"
)


def _make_stack(tmp_path, *, compose=True, compose_text=_COMPOSE):
    project = tmp_path / "stack"
    project.mkdir(parents=True, exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text(compose_text)
    return project


# --------------------------------------------------------------------------
# generate_password
# --------------------------------------------------------------------------
def test_generate_password_default_length():
    password = st_secrets.generate_password()

    assert len(password) == st_secrets.PASSWORD_LENGTH
    assert set(password) <= set(st_secrets.PASSWORD_ALPHABET)


def test_generate_password_custom_length():
    password = st_secrets.generate_password(8)

    assert len(password) == 8
    assert set(password) <= set(st_secrets.PASSWORD_ALPHABET)


# --------------------------------------------------------------------------
# discover_secret_files
# --------------------------------------------------------------------------
def test_discover_secret_files_keeps_gen_strips_leading_dot_slash():
    found = list(st_secrets.discover_secret_files(_COMPOSE))

    assert found == [
        ".secrets/agui_db_password.gen",
        ".secrets/other_token.gen",
    ]


def test_discover_secret_files_none_when_no_gen():
    found = list(
        st_secrets.discover_secret_files("services:\n  x:\n    image: y\n")
    )

    assert found == []


# --------------------------------------------------------------------------
# read_puid_pgid
# --------------------------------------------------------------------------
def test_read_puid_pgid_defaults_when_no_env(tmp_path):
    puid, pgid = st_secrets.read_puid_pgid(tmp_path / ".env")

    assert (puid, pgid) == (st_secrets.DEFAULT_PUID, st_secrets.DEFAULT_PGID)


def test_read_puid_pgid_reads_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=x\nPUID=1500\nPGID=1600\n")

    puid, pgid = st_secrets.read_puid_pgid(env)

    assert (puid, pgid) == ("1500", "1600")


def test_read_puid_pgid_blank_values_keep_defaults(tmp_path):
    env = tmp_path / ".env"
    env.write_text("PUID=\nPGID=\n")

    puid, pgid = st_secrets.read_puid_pgid(env)

    assert (puid, pgid) == (st_secrets.DEFAULT_PUID, st_secrets.DEFAULT_PGID)


# --------------------------------------------------------------------------
# _maybe_reown
# --------------------------------------------------------------------------
def test_maybe_reown_noop_when_uid_matches(tmp_path, monkeypatch):
    run = mock.Mock()
    monkeypatch.setattr(st_secrets.subprocess, "run", run)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)

    st_secrets._maybe_reown(tmp_path / ".secrets", tmp_path / ".env")

    run.assert_not_called()


def test_maybe_reown_warns_when_docker_missing(tmp_path, monkeypatch, capsys):
    run = mock.Mock()
    monkeypatch.setattr(st_secrets.subprocess, "run", run)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 4321)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 4321)
    monkeypatch.setattr(st_secrets.shutil, "which", lambda name: None)

    st_secrets._maybe_reown(tmp_path / ".secrets", tmp_path / ".env")

    assert "WARNING" in capsys.readouterr().out
    run.assert_not_called()


def test_maybe_reown_chowns_via_docker(tmp_path, monkeypatch):
    run = mock.Mock()
    secrets_dir = tmp_path / ".secrets"
    monkeypatch.setattr(st_secrets.subprocess, "run", run)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 4321)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 4321)
    monkeypatch.setattr(st_secrets.shutil, "which", lambda name: "/bin/docker")

    st_secrets._maybe_reown(secrets_dir, tmp_path / ".env")

    run.assert_called_once_with(
        [
            "/bin/docker",
            "run",
            "--rm",
            "-u",
            "0:0",
            "-v",
            f"{secrets_dir}:/secrets",
            "busybox",
            "chown",
            "-R",
            "1000:1000",
            "/secrets",
        ],
        check=True,
    )


# --------------------------------------------------------------------------
# generate_secrets
# --------------------------------------------------------------------------
def test_generate_secrets_missing_compose_raises(tmp_path):
    project = _make_stack(tmp_path, compose=False)

    with pytest.raises(st_secrets.SecretsError, match="cannot find compose"):
        st_secrets.generate_secrets(project)


def test_generate_secrets_warns_when_none_found(tmp_path, capsys):
    project = _make_stack(tmp_path, compose_text="services:\n  x:\n")

    st_secrets.generate_secrets(project)

    assert "no *.gen files found" in capsys.readouterr().out


def test_generate_secrets_writes_files_0600(tmp_path, monkeypatch):
    project = _make_stack(tmp_path)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    monkeypatch.setattr(st_secrets, "generate_password", lambda: "s3kr3t")

    st_secrets.generate_secrets(project)

    secrets_dir = project / ".secrets"
    written = sorted(p.name for p in secrets_dir.glob("*.gen"))
    assert written == ["agui_db_password.gen", "other_token.gen"]
    for secret in secrets_dir.glob("*.gen"):
        assert secret.read_text() == "s3kr3t"
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
