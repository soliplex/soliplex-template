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


def _make_stack(
    tmp_path, *, compose=True, compose_text=_COMPOSE, init_sh=None
):
    project = tmp_path / "stack"
    project.mkdir(parents=True, exist_ok=True)
    if compose:
        (project / "docker-compose.yml").write_text(compose_text)
    if init_sh is not None:
        config_dir = project / "postgres" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "init.sh").write_text(init_sh)
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

    with pytest.raises(st_secrets.ComposeNotFound):
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


# --------------------------------------------------------------------------
# discover_db_pass_vars / discover_initdb_dir / discover_init_roles /
# discover_secret_roles / discover_postgres_user
# --------------------------------------------------------------------------
_COMPOSE_ROLES = (
    "services:\n"
    "  postgres:\n"
    "    environment:\n"
    "      AGUI_DB_PASS_FILE: /run/secrets/agui_db_password\n"
    "      AUTHZ_DB_PASS_FILE: /run/secrets/authz_db_password\n"
    "      INGESTER_DB_PASS_FILE: /run/secrets/ingester_db_password\n"
    "      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password\n"
    "      POSTGRES_USER: postgres\n"
    "    volumes:\n"
    '      - type: "bind"\n'
    '        source: "./postgres/config"\n'
    '        target: "/docker-entrypoint-initdb.d"\n'
    "secrets:\n"
    "  agui_db_password:\n"
    "    file: ./.secrets/agui_db_password.gen\n"
    "  authz_db_password:\n"
    "    file: ./.secrets/authz_db_password.gen\n"
    "  ingester_db_password:\n"
    "    file: ./.secrets/ingester_db_password.gen\n"
    "  postgres_password:\n"
    "    file: ./.secrets/postgres_password.gen\n"
    "  token_secret:\n"
    "    file: ./.secrets/token_secret.gen\n"
)

_INIT_SH = (
    'AGUI_DB_PASS=$(cat "$AGUI_DB_PASS_FILE")\n'
    'AUTHZ_DB_PASS=$(cat "$AUTHZ_DB_PASS_FILE")\n'
    'INGESTER_DB_PASS=$(cat "$INGESTER_DB_PASS_FILE")\n'
    'psql --username "$POSTGRES_USER" <<-EOSQL\n'
    "    CREATE USER soliplex_agui WITH PASSWORD '$AGUI_DB_PASS';\n"
    "    CREATE USER soliplex_authz WITH PASSWORD '$AUTHZ_DB_PASS';\n"
    "    CREATE USER soliplex_ingester WITH PASSWORD '$INGESTER_DB_PASS';\n"
    "EOSQL\n"
)


def test_discover_db_pass_vars_maps_compose_wiring():
    wired = st_secrets.discover_db_pass_vars(_COMPOSE_ROLES)

    assert wired == {
        "agui_db_password.gen": "AGUI_DB_PASS",
        "authz_db_password.gen": "AUTHZ_DB_PASS",
        "ingester_db_password.gen": "INGESTER_DB_PASS",
        "postgres_password.gen": "POSTGRES_PASSWORD",
    }


def test_discover_db_pass_vars_empty_without_pass_file_lines():
    wired = st_secrets.discover_db_pass_vars(_COMPOSE)

    assert wired == {}


def test_discover_initdb_dir_returns_bind_source():
    init_dir = st_secrets.discover_initdb_dir(_COMPOSE_ROLES)

    assert init_dir == "postgres/config"


def test_discover_initdb_dir_none_without_initdb_mount():
    init_dir = st_secrets.discover_initdb_dir(_COMPOSE)

    assert init_dir is None


def test_discover_init_roles_maps_create_user_lines():
    roles = st_secrets.discover_init_roles(_INIT_SH)

    assert roles == {
        "AGUI_DB_PASS": "soliplex_agui",
        "AUTHZ_DB_PASS": "soliplex_authz",
        "INGESTER_DB_PASS": "soliplex_ingester",
    }


def test_discover_postgres_user_empty_value_falls_back():
    compose = "services:\n    environment:\n      POSTGRES_USER:\n"

    db_user = st_secrets.discover_postgres_user(compose)

    assert db_user == "postgres"


def test_discover_secret_roles_joins_compose_and_init_scripts(tmp_path):
    project = _make_stack(
        tmp_path, compose_text=_COMPOSE_ROLES, init_sh=_INIT_SH
    )

    roles = st_secrets.discover_secret_roles(_COMPOSE_ROLES, project)

    assert roles == {
        "agui_db_password.gen": "soliplex_agui",
        "authz_db_password.gen": "soliplex_authz",
        "ingester_db_password.gen": "soliplex_ingester",
        "postgres_password.gen": "postgres",
    }


def test_discover_secret_roles_superuser_without_init_scripts(tmp_path):
    project = _make_stack(tmp_path, compose_text=_COMPOSE_ROLES)

    roles = st_secrets.discover_secret_roles(_COMPOSE_ROLES, project)

    assert roles == {"postgres_password.gen": "postgres"}


_INIT_SH_UNMATCHED = "CREATE USER other_role WITH PASSWORD '$OTHER_PASS';\n"

_COMPOSE_WIRED_UNMATCHED = (
    "services:\n"
    "  postgres:\n"
    "    environment:\n"
    "      INGESTER_DB_PASS_FILE: /run/secrets/ingester_db_password\n"
    "      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password\n"
    "      POSTGRES_USER: postgres\n"
    "    volumes:\n"
    '      - type: "bind"\n'
    '        source: "./postgres/config"\n'
    '        target: "/docker-entrypoint-initdb.d"\n'
    "secrets:\n"
    "  ingester_db_password:\n"
    "    file: ./.secrets/ingester_db_password.gen\n"
    "  postgres_password:\n"
    "    file: ./.secrets/postgres_password.gen\n"
)


def test_discover_secret_roles_ignores_unwired_init_roles(tmp_path):
    project = _make_stack(
        tmp_path,
        compose_text=_COMPOSE_WIRED_UNMATCHED,
        init_sh=_INIT_SH_UNMATCHED,
    )

    roles = st_secrets.discover_secret_roles(_COMPOSE_WIRED_UNMATCHED, project)

    assert roles == {"postgres_password.gen": "postgres"}


def test_discover_postgres_user_reads_env_value():
    db_user = st_secrets.discover_postgres_user(_COMPOSE_ROLES)

    assert db_user == "postgres"


def test_discover_postgres_user_defaults_when_absent():
    db_user = st_secrets.discover_postgres_user(_COMPOSE)

    assert db_user == "postgres"


# --------------------------------------------------------------------------
# generate_secrets: missing-only default
# --------------------------------------------------------------------------
def test_generate_secrets_default_leaves_existing_untouched(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    secrets_dir = project / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "agui_db_password.gen").write_text("OLD")
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    monkeypatch.setattr(st_secrets, "generate_password", lambda: "NEW")

    st_secrets.generate_secrets(project)

    assert (secrets_dir / "agui_db_password.gen").read_text() == "OLD"
    assert (secrets_dir / "other_token.gen").read_text() == "NEW"
    out = capsys.readouterr().out
    assert "= Existing: agui_db_password.gen (left unchanged)" in out
    assert "Generated: other_token.gen" in out
    assert "Left 1 existing secret(s) unchanged" in out
    mode = stat.S_IMODE((secrets_dir / "other_token.gen").stat().st_mode)
    assert mode == 0o600


def test_generate_secrets_default_noops_when_all_exist(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    secrets_dir = project / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "agui_db_password.gen").write_text("OLD1")
    (secrets_dir / "other_token.gen").write_text("OLD2")
    run = mock.Mock()
    monkeypatch.setattr(st_secrets.subprocess, "run", run)
    monkeypatch.setattr(st_secrets.shutil, "which", lambda name: "/bin/docker")
    generate = mock.Mock()
    monkeypatch.setattr(st_secrets, "generate_password", generate)

    st_secrets.generate_secrets(project)

    assert (secrets_dir / "agui_db_password.gen").read_text() == "OLD1"
    assert (secrets_dir / "other_token.gen").read_text() == "OLD2"
    out = capsys.readouterr().out
    assert "already exist" in out
    assert "--rotate" in out
    generate.assert_not_called()
    run.assert_not_called()


# --------------------------------------------------------------------------
# generate_secrets: rotation
# --------------------------------------------------------------------------
def test_generate_secrets_rotate_names_writes_only_named_in_order(
    tmp_path, monkeypatch
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE_ROLES)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    passwords = iter(["p1", "p2"])
    monkeypatch.setattr(
        st_secrets, "generate_password", lambda: next(passwords)
    )

    st_secrets.generate_secrets(
        project,
        rotate=["token_secret.gen", "postgres_password"],
    )

    secrets_dir = project / ".secrets"
    assert (secrets_dir / "token_secret.gen").read_text() == "p1"
    assert (secrets_dir / "postgres_password.gen").read_text() == "p2"
    assert not (secrets_dir / "agui_db_password.gen").exists()


def test_generate_secrets_rotate_dedupes_repeated_names(tmp_path, monkeypatch):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    passwords = iter(["p1"])
    monkeypatch.setattr(
        st_secrets, "generate_password", lambda: next(passwords)
    )

    st_secrets.generate_secrets(
        project, rotate=["other_token", "other_token.gen"]
    )

    assert (project / ".secrets/other_token.gen").read_text() == "p1"


def test_generate_secrets_rotate_unknown_name_raises_writes_nothing(
    tmp_path, monkeypatch
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE_ROLES)
    monkeypatch.setattr(st_secrets, "generate_password", mock.Mock())

    with pytest.raises(st_secrets.UnknownSecrets) as excinfo:
        st_secrets.generate_secrets(project, rotate=["bogus"])

    assert "bogus" in str(excinfo.value)
    assert "agui_db_password" in str(excinfo.value)
    assert not (project / ".secrets").exists()


def test_generate_secrets_rotate_all_writes_every_declared(
    tmp_path, monkeypatch
):
    project = _make_stack(
        tmp_path, compose_text=_COMPOSE_ROLES, init_sh=_INIT_SH
    )
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    passwords = iter(["p1", "p2", "p3", "p4", "p5"])
    monkeypatch.setattr(
        st_secrets, "generate_password", lambda: next(passwords)
    )

    st_secrets.generate_secrets(project, rotate=st_secrets.ROTATE_ALL)

    secrets_dir = project / ".secrets"
    assert (secrets_dir / "agui_db_password.gen").read_text() == "p1"
    assert (secrets_dir / "authz_db_password.gen").read_text() == "p2"
    assert (secrets_dir / "ingester_db_password.gen").read_text() == "p3"
    assert (secrets_dir / "postgres_password.gen").read_text() == "p4"
    assert (secrets_dir / "token_secret.gen").read_text() == "p5"


def test_generate_secrets_rotate_prints_alter_role_commands(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(
        tmp_path, compose_text=_COMPOSE_ROLES, init_sh=_INIT_SH
    )
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    passwords = iter(["p1", "p2"])
    monkeypatch.setattr(
        st_secrets, "generate_password", lambda: next(passwords)
    )

    st_secrets.generate_secrets(
        project,
        rotate=["postgres_password", "ingester_db_password.gen"],
    )

    out = capsys.readouterr().out
    assert "PostgreSQL note:" in out
    assert out.index("ALTER ROLE postgres PASSWORD 'p1'") < out.index(
        "ALTER ROLE soliplex_ingester PASSWORD 'p2'"
    )
    assert (
        "docker compose exec -T postgres psql -U postgres "
        "-c \"ALTER ROLE postgres PASSWORD 'p1';\""
    ) in out
    assert "docker compose down -v" in out


def test_generate_secrets_rotate_unmapped_roles_get_command_hints(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE_ROLES)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    monkeypatch.setattr(st_secrets, "generate_password", lambda: "NEW")

    st_secrets.generate_secrets(project, rotate=["agui_db_password"])

    out = capsys.readouterr().out
    assert "PostgreSQL note:" in out
    assert "# agui_db_password.gen: role not derivable" in out
    assert "ALTER ROLE" not in out
    assert "docker compose down -v" in out


def test_generate_secrets_rotate_without_db_secrets_skips_alter_note(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 1000)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 1000)
    monkeypatch.setattr(st_secrets, "generate_password", lambda: "NEW")

    st_secrets.generate_secrets(project, rotate=["other_token"])

    out = capsys.readouterr().out
    assert "PostgreSQL note:" not in out
    assert "Rotated: other_token.gen" in out


def test_generate_secrets_rotate_reowns_written_files(tmp_path, monkeypatch):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    run = mock.Mock()
    monkeypatch.setattr(st_secrets.subprocess, "run", run)
    monkeypatch.setattr(st_secrets.os, "getuid", lambda: 4321)
    monkeypatch.setattr(st_secrets.os, "getgid", lambda: 4321)
    monkeypatch.setattr(st_secrets.shutil, "which", lambda name: "/bin/docker")

    st_secrets.generate_secrets(project, rotate=["other_token"])

    run.assert_called_once()


# --------------------------------------------------------------------------
# generate_secrets: dry runs
# --------------------------------------------------------------------------
def test_generate_secrets_dry_run_default_writes_nothing(tmp_path, capsys):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)

    st_secrets.generate_secrets(project, dry_run=True)

    assert not (project / ".secrets").exists()
    out = capsys.readouterr().out
    assert "would generate: agui_db_password.gen" in out
    assert "would generate: other_token.gen" in out
    assert "=== Dry run: nothing written ===" in out


def test_generate_secrets_dry_run_default_reports_left_ones(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE)
    secrets_dir = project / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "agui_db_password.gen").write_text("OLD")

    st_secrets.generate_secrets(project, dry_run=True)

    out = capsys.readouterr().out
    assert "would generate: other_token.gen" in out
    assert "would leave: agui_db_password.gen (exists)" in out
    assert (secrets_dir / "agui_db_password.gen").read_text() == "OLD"


def test_generate_secrets_dry_run_rotate_flags_db_secrets(
    tmp_path, monkeypatch, capsys
):
    project = _make_stack(tmp_path, compose_text=_COMPOSE_ROLES)
    monkeypatch.setattr(st_secrets, "generate_password", mock.Mock())

    st_secrets.generate_secrets(
        project, rotate=st_secrets.ROTATE_ALL, dry_run=True
    )

    out = capsys.readouterr().out
    assert (
        "would rotate: agui_db_password.gen  (will need 'ALTER ROLE')" in out
    )
    assert "would rotate: token_secret.gen\n" in out
    assert "ALTER ROLE agui PASSWORD" not in out
    assert not (project / ".secrets").exists()
