"""Unit tests for the bundled ``skill/scripts/install_authelia.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``
(and registered in ``sys.modules``, which its dataclasses need). Tests write
a generated stack's files under ``tmp_path`` and mock the subprocess calls
that touch Docker.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
from unittest import mock

import pytest
import yaml

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "install_authelia.py"
)
_spec = importlib.util.spec_from_file_location(
    "install_authelia", _MODULE_PATH
)
install_authelia = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = install_authelia
_spec.loader.exec_module(install_authelia)


def _stack(tmp_path) -> pathlib.Path:
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "docker-compose.yml").write_text(
        "# Usage:\n"
        "# docker compose up\n"
        "name: test-stack\n"
        "\n"
        "services:\n"
        "  backend:\n"
    )
    (stack / "pyproject.toml").write_text(
        "[tool.soliplex-template.params]\n"
        'project_name = "test-stack"\n'
        'server_name = "test-stack.localhost"\n'
        "nginx_https = 9443\n"
        'ollama_base_url = "http://ollama.invalid:11434"\n'
    )
    (stack / ".env").write_text("OLLAMA_BASE_URL=\nPUID=1000\nPGID=1000\n")
    (stack / ".secrets").mkdir()
    (stack / ".secrets" / "postgres_password.gen").write_text("pg-pass")
    (stack / "backend" / "environment" / "oidc").mkdir(parents=True)
    (stack / "backend" / "environment" / "oidc" / "cacert.pem").write_text(
        "# trust list\n"
    )
    (stack / "backend" / "environment" / "oidc" / "config.yaml").write_text(
        "#\n# OIDC Auth Systems\n#\n"
        'oidc_client_pem_path: "./cacert.pem"\n\n'
        "auth_systems: []\n"
    )
    return stack


# --------------------------------------------------------------------------
# resolve_params
# --------------------------------------------------------------------------
def test_resolve_params_reads_manifest_and_env(tmp_path):
    stack = _stack(tmp_path)

    params = install_authelia.resolve_params(stack)

    assert params.project_name == "test-stack"
    assert params.server_name == "test-stack.localhost"
    assert params.nginx_https == 9443
    assert params.puid == 1000
    assert params.pgid == 1000


def test_resolve_params_defaults_server_name(tmp_path):
    stack = _stack(tmp_path)
    (stack / "pyproject.toml").write_text(
        "[tool.soliplex-template.params]\n"
        'project_name = "other"\n'
        "nginx_https = 9443\n"
        'ollama_base_url = "http://ollama.invalid:11434"\n'
    )

    params = install_authelia.resolve_params(stack)

    assert params.server_name == "other.localhost"


def test_resolve_params_rejects_missing_puid(tmp_path):
    stack = _stack(tmp_path)
    (stack / ".env").write_text("PUID=1000\n")

    with pytest.raises(install_authelia.MissingEnvValue):
        install_authelia.resolve_params(stack)


# --------------------------------------------------------------------------
# ensure_authelia_secrets
# --------------------------------------------------------------------------
def test_ensure_authelia_secrets_generates_missing(tmp_path):
    stack = _stack(tmp_path)

    paths = install_authelia.ensure_authelia_secrets(stack)

    for name in install_authelia.AUTHELIA_SECRET_NAMES:
        path = paths[name]
        assert path.is_file()
        assert len(path.read_text()) == 64
        assert path.stat().st_mode & 0o777 == 0o600


def test_ensure_authelia_secrets_preserves_existing(tmp_path):
    stack = _stack(tmp_path)
    existing = stack / ".secrets" / "authelia_session_secret.gen"
    existing.write_text("keep-me")

    install_authelia.ensure_authelia_secrets(stack)

    assert existing.read_text() == "keep-me"


# --------------------------------------------------------------------------
# append_cert_to_cacert
# --------------------------------------------------------------------------
def test_append_cert_to_cacert_adds_block(tmp_path):
    stack = _stack(tmp_path)
    cert = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"

    install_authelia.append_cert_to_cacert(stack, cert)

    text = (stack / "backend/environment/oidc/cacert.pem").read_text()
    assert "# Authelia server certificate" in text
    assert "-----BEGIN CERTIFICATE-----" in text
    assert text.count("# Authelia server certificate\n") == 1
    assert "# Authelia server certificate end" in text


def test_append_cert_to_cacert_replaces_existing_block(tmp_path):
    stack = _stack(tmp_path)
    first = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
    second = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n"
    install_authelia.append_cert_to_cacert(stack, first)

    install_authelia.append_cert_to_cacert(stack, second)

    text = (stack / "backend/environment/oidc/cacert.pem").read_text()
    assert text.count("# Authelia server certificate\n") == 1
    assert "# Authelia server certificate end" in text
    assert "BBBB" in text
    assert "AAAA" not in text


# --------------------------------------------------------------------------
# write_compose_fragment
# --------------------------------------------------------------------------
def test_write_compose_fragment_renders_service(tmp_path):
    stack = _stack(tmp_path)
    params = install_authelia.StackParams(
        project_name="test-stack",
        server_name="test-stack.localhost",
        nginx_https=9443,
        puid=1000,
        pgid=1000,
        ollama_base_url="http://ollama.invalid:11434",
    )

    install_authelia.write_compose_fragment(stack, params)

    text = (stack / "compose.authelia.yaml").read_text()
    assert "services:\n  authelia:" in text
    assert "image: authelia/authelia:4.39.28" in text
    assert "user: 1000:1000" in text
    assert "authelia_tls_key" in text
    assert "authelia_reset_password_jwt_secret" in text
    assert "- test-stack.localhost" in text
    assert "authelia_storage_password:" in text


# --------------------------------------------------------------------------
# write_authelia_config
# --------------------------------------------------------------------------
def _expand_config_template(text):
    """Mimic Authelia's 'template' filter over a configuration.yml."""
    # A placeholder label: a real 'PRIVATE KEY' label trips gitleaks, and
    # the fake body is never fed to a real key parser.
    pem = "\n".join(
        f"          {line}" if i else line
        for i, line in enumerate(
            (
                "|",
                "-----BEGIN TESTKEY-----",
                "MIIB",
                "-----END TESTKEY-----",
            )
        )
    )
    expanded = re.sub(r"\{\{ secret .*? \}\}", pem, text)
    return yaml.safe_load(expanded)


def test_write_authelia_config_renders_oidc_client(tmp_path):
    stack = _stack(tmp_path)
    params = install_authelia.StackParams(
        project_name="test-stack",
        server_name="test-stack.localhost",
        nginx_https=9443,
        puid=1000,
        pgid=1000,
        ollama_base_url="http://ollama.invalid:11434",
    )

    install_authelia.write_authelia_config(stack, params)

    text = (stack / "authelia/configuration.yml").read_text()
    data = _expand_config_template(text)
    oidc = data["identity_providers"]["oidc"]

    client = oidc["clients"][0]
    assert client["client_id"] == "soliplex"
    assert client["public"] is True
    assert client["access_token_signed_response_alg"] == "RS256"
    assert client["consent_mode"] == "implicit"
    assert client["claims_policy"] == "soliplex"
    assert client["authorization_policy"] == "one_factor"
    assert client["redirect_uris"] == [
        "https://test-stack.localhost:9443/api/auth/authelia"
    ]
    # The secret expression has been expanded into the fake PEM.
    assert oidc["jwks"][0]["key"] == (
        "-----BEGIN TESTKEY-----\nMIIB\n-----END TESTKEY-----\n"
    )
    assert data["server"]["address"] == "tcp://:9443/authelia"
    # Random secrets stay env-only: no 'file:' values in the config.
    assert "hmac_secret" not in oidc
    assert "password" not in data["storage"]["postgres"]
    # The random secrets and the TLS material are referenced by path/
    # environment, never inlined in the template.
    assert "    key: '/run/secrets/authelia_tls_key'" in text
    assert "    certificate: '/run/secrets/authelia_tls_certificate'" in text


# --------------------------------------------------------------------------
# write_nginx_fragment
# --------------------------------------------------------------------------
def test_write_nginx_fragment_renders_proxy(tmp_path):
    stack = _stack(tmp_path)

    install_authelia.write_nginx_fragment(stack)

    text = (stack / "nginx/conf.d/authelia.conf").read_text()
    assert "location /authelia/" in text
    assert "proxy_pass https://authelia:9443/authelia/" in text
    assert "X-Forwarded-Proto $scheme" in text


# --------------------------------------------------------------------------
# add_include_to_compose
# --------------------------------------------------------------------------
def test_add_include_to_compose_inserts_after_name(tmp_path):
    stack = _stack(tmp_path)

    install_authelia.add_include_to_compose(stack)

    text = (stack / "docker-compose.yml").read_text()
    assert "include:\n  - compose.authelia.yaml" in text
    assert text.index("name:") < text.index("include:")


def test_add_include_to_compose_is_idempotent(tmp_path):
    stack = _stack(tmp_path)
    install_authelia.add_include_to_compose(stack)

    install_authelia.add_include_to_compose(stack)

    text = (stack / "docker-compose.yml").read_text()
    assert text.count("compose.authelia.yaml") == 1


def test_add_include_to_compose_rejects_missing_name(tmp_path):
    stack = _stack(tmp_path)
    (stack / "docker-compose.yml").write_text("# no name\nservices:\n")

    with pytest.raises(install_authelia.BadComposeFile):
        install_authelia.add_include_to_compose(stack)


# --------------------------------------------------------------------------
# generate_issuer_key
# --------------------------------------------------------------------------
def test_generate_issuer_key_writes_secret_and_cert(tmp_path):
    stack = _stack(tmp_path)

    private, public, cert = install_authelia.generate_issuer_key(stack)

    key_path = stack / ".secrets" / "authelia_oidc_issuer_private_key.gen"
    cert_path = stack / ".secrets" / "authelia_oidc_issuer_certificate.pem"
    assert key_path.is_file()
    assert cert_path.is_file()
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert cert_path.stat().st_mode & 0o777 == 0o600
    assert private == key_path.read_text()
    assert cert == cert_path.read_text()
    assert "BEGIN PUBLIC KEY" in public


def test_generate_issuer_key_preserves_existing_pair(tmp_path):
    stack = _stack(tmp_path)
    install_authelia.generate_issuer_key(stack)
    key_text = (
        stack / ".secrets" / "authelia_oidc_issuer_private_key.gen"
    ).read_text()
    cert_text = (
        stack / ".secrets" / "authelia_oidc_issuer_certificate.pem"
    ).read_text()

    private, public, cert = install_authelia.generate_issuer_key(stack)

    assert private == key_text
    assert cert == cert_text
    assert "BEGIN PUBLIC KEY" in public


# --------------------------------------------------------------------------
# generate_tls_key
# --------------------------------------------------------------------------
def test_generate_tls_key_writes_pair(tmp_path):
    stack = _stack(tmp_path)

    key_path = install_authelia.generate_tls_key(stack, "test-stack.localhost")

    key_file = stack / ".secrets" / "authelia_tls_key.gen"
    cert_file = stack / ".secrets" / "authelia_tls_certificate.pem"
    assert key_path == str(key_file)
    assert key_file.is_file()
    assert cert_file.is_file()
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert cert_file.stat().st_mode & 0o777 == 0o600
    # The pair is self-signed for the stack's server name, so the backend
    # verifying the issuer URL hostname succeeds with it in cacert.pem.
    cert_text = cert_file.read_text()
    assert "test-stack.localhost" in _openssl_x509_subject(cert_text, stack)
    assert (
        cert_text
        == (stack / ".secrets" / "authelia_tls_certificate.pem").read_text()
    )


def test_generate_tls_key_preserves_existing_pair(tmp_path):
    stack = _stack(tmp_path)
    install_authelia.generate_tls_key(stack, "test-stack.localhost")
    key_text = (stack / ".secrets" / "authelia_tls_key.gen").read_text()

    install_authelia.generate_tls_key(stack, "other.localhost")

    assert (stack / ".secrets" / "authelia_tls_key.gen").read_text() == (
        key_text
    )


def _openssl_x509_subject(cert_text, stack):
    proc = install_authelia.run_cmd(
        ["openssl", "x509", "-noout", "-subject", "-nameopt", "oneline"],
        cwd=stack,
        capture=True,
        input_text=cert_text,
    )
    return proc.stdout


# --------------------------------------------------------------------------
# create_authelia_database
# --------------------------------------------------------------------------
def test_create_authelia_database_runs_psql(tmp_path):
    stack = _stack(tmp_path)
    (stack / ".secrets" / "postgres_password.gen").write_text("pg-pass")
    (stack / ".secrets" / "authelia_storage_password.gen").write_text(
        "authelia-pass"
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input", "")))
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(install_authelia.subprocess, "run", fake_run):
        install_authelia.create_authelia_database(stack)

    assert len(calls) == 1
    assert calls[0][0][0:2] == ["docker", "compose"]
    assert "-e" in calls[0][0]
    assert "PGPASSWORD=pg-pass" in calls[0][0]
    assert "-h" in calls[0][0]
    assert "localhost" in calls[0][0]
    assert "CREATE USER soliplex_authelia" in calls[0][1]
    assert "authelia-pass" in calls[0][1]


def test_discover_postgres_user_from_compose(tmp_path):
    stack = _stack(tmp_path)
    (stack / "docker-compose.yml").write_text(
        "# Usage:\n"
        "# docker compose up\n"
        "name: test-stack\n"
        "POSTGRES_USER: admin\n"
    )

    user = install_authelia._discover_postgres_user(stack)

    assert user == "admin"


def test_discover_postgres_user_defaults_without_entry(tmp_path):
    stack = _stack(tmp_path)
    (stack / "docker-compose.yml").write_text(
        "# Usage:\n# docker compose up\nname: test-stack\n"
    )

    user = install_authelia._discover_postgres_user(stack)

    assert user == "postgres"


def test_discover_postgres_user_ignores_empty_value(tmp_path):
    stack = _stack(tmp_path)
    (stack / "docker-compose.yml").write_text(
        '# Usage:\n# docker compose up\nname: test-stack\nPOSTGRES_USER: ""\n'
    )

    user = install_authelia._discover_postgres_user(stack)

    assert user == "postgres"


# --------------------------------------------------------------------------
# add_provider_entry
# --------------------------------------------------------------------------
def test_add_provider_entry_writes_config(tmp_path):
    stack = _stack(tmp_path)
    params = install_authelia.StackParams(
        project_name="test-stack",
        server_name="test-stack.localhost",
        nginx_https=9443,
        puid=1000,
        pgid=1000,
        ollama_base_url="http://ollama.invalid:11434",
    )
    public_key = "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----\n"

    install_authelia.add_provider_entry(stack, params, public_key)

    config = (stack / "backend/environment/oidc/config.yaml").read_text()
    assert "id: authelia" in config
    assert "server_url: https://test-stack.localhost:9443/authelia" in config
    assert "client_id: soliplex" in config
    signin = (stack / "nginx/conf.d/signin-provider.conf").read_text()
    assert 'set $soliplex_signin_provider "authelia"' in signin


def test_add_provider_entry_idempotent(tmp_path):
    stack = _stack(tmp_path)
    params = install_authelia.StackParams(
        project_name="test-stack",
        server_name="test-stack.localhost",
        nginx_https=9443,
        puid=1000,
        pgid=1000,
        ollama_base_url="http://ollama.invalid:11434",
    )
    public_key = "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----\n"

    install_authelia.add_provider_entry(stack, params, public_key)
    install_authelia.add_provider_entry(stack, params, public_key)

    config = (stack / "backend/environment/oidc/config.yaml").read_text()
    assert config.count("id: authelia") == 1


# --------------------------------------------------------------------------
# run_cmd and edge cases
# --------------------------------------------------------------------------
def test_run_cmd_raises_on_failure(tmp_path):
    stack = _stack(tmp_path)
    proc = mock.Mock(returncode=1, stdout="out", stderr="err")

    with (
        mock.patch.object(
            install_authelia.subprocess, "run", return_value=proc
        ),
        pytest.raises(install_authelia.DockerCommandFailed) as caught,
    ):
        install_authelia.run_cmd(["docker", "bad"], cwd=stack)

    assert caught.value.returncode == 1
    assert "err" in str(caught.value)


def test_load_manifest_rejects_missing_params(tmp_path):
    stack = _stack(tmp_path)
    (stack / "pyproject.toml").write_text("[tool.soliplex-template]\n")

    with pytest.raises(install_authelia.ManifestNotFound):
        install_authelia.load_manifest(stack)


def test_create_admin_user_drives_authelia_users_quiet(tmp_path):
    stack = _stack(tmp_path)
    summary = json.dumps(
        {
            "email": "admin@x.local",
            "sub": "sub-123",
            "admin": True,
            "password_file": "/x/.secrets/authelia_user_x.gen",
        }
    )
    proc = mock.Mock(returncode=0, stdout=summary, stderr="")
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        return proc

    with mock.patch.object(install_authelia.subprocess, "run", fake_run):
        sub, password_file = install_authelia.create_admin_user(
            stack, "admin@x.local"
        )

    assert sub == "sub-123"
    assert password_file == "/x/.secrets/authelia_user_x.gen"
    cmd = run_calls[0]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("authelia_users.py")
    assert cmd[-1] == "--quiet"
    assert cmd[-2] == "admin@x.local"
    assert cmd[-4] == "--admin"


# --------------------------------------------------------------------------
# main / CLI
# --------------------------------------------------------------------------
def _fake_issuer_key(s):
    key = "PRIVATE"
    cert = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    (s / ".secrets" / "authelia_oidc_issuer_private_key.gen").write_text(key)
    (s / ".secrets" / "authelia_oidc_issuer_certificate.pem").write_text(cert)
    return key, "PUBLIC", cert


def _fake_tls_key(s, _server_name):
    (s / ".secrets" / "authelia_tls_certificate.pem").write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    )
    (s / ".secrets" / "authelia_tls_key.gen").write_text("TLS-KEY")
    return str(s / ".secrets" / "authelia_tls_key.gen")


def test_main_no_up_writes_configuration(tmp_path):
    stack = _stack(tmp_path)

    with (
        mock.patch.object(
            install_authelia, "generate_issuer_key", _fake_issuer_key
        ),
        mock.patch.object(install_authelia, "generate_tls_key", _fake_tls_key),
        mock.patch.object(install_authelia.sys, "stdout") as fake_stdout,
    ):
        fake_stdout.isatty.return_value = False
        result = install_authelia.main(
            [
                "--project-dir",
                str(stack),
                "--admin-email",
                "admin@x.local",
                "--no-up",
            ]
        )

    assert result == 0
    assert (stack / "compose.authelia.yaml").is_file()
    assert (stack / "authelia/configuration.yml").is_file()
    assert (stack / "nginx/conf.d/authelia.conf").is_file()
    assert (
        "compose.authelia.yaml" in (stack / "docker-compose.yml").read_text()
    )
    assert (
        "# Authelia server certificate"
        in (stack / "backend/environment/oidc/cacert.pem").read_text()
    )


def test_main_full_install_runs_services(tmp_path):
    stack = _stack(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    def fake_create_admin(s, email):
        password_path = s / ".secrets" / "authelia_user_admin_x.local.gen"
        return "sub-123", str(password_path)

    with (
        mock.patch.object(
            install_authelia, "generate_issuer_key", _fake_issuer_key
        ),
        mock.patch.object(install_authelia, "generate_tls_key", _fake_tls_key),
        mock.patch.object(install_authelia.subprocess, "run", fake_run),
        mock.patch.object(
            install_authelia, "create_admin_user", fake_create_admin
        ),
    ):
        result = install_authelia.main(
            ["--project-dir", str(stack), "--admin-email", "admin@x.local"]
        )

    assert result == 0
    assert any(c[0:2] == ["docker", "compose"] for c in calls)


def test_main_rejects_missing_docker_compose(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(install_authelia.StackNotFound):
        install_authelia.main(
            ["--project-dir", str(empty), "--admin-email", "admin@x.local"]
        )


def test_main_rejects_missing_manifest(tmp_path):
    stack = _stack(tmp_path)
    (stack / "pyproject.toml").unlink()

    with pytest.raises(install_authelia.ManifestNotFound):
        install_authelia.main(
            [
                "--project-dir",
                str(stack),
                "--admin-email",
                "admin@x.local",
                "--no-up",
            ]
        )


def test_main_rejects_missing_env(tmp_path):
    stack = _stack(tmp_path)
    (stack / ".env").unlink()

    with pytest.raises(install_authelia.EnvNotFound):
        install_authelia.main(
            [
                "--project-dir",
                str(stack),
                "--admin-email",
                "admin@x.local",
                "--no-up",
            ]
        )


def test_main_rejects_missing_cacert(tmp_path):
    stack = _stack(tmp_path)
    (stack / "backend/environment/oidc/cacert.pem").unlink()

    with (
        mock.patch.object(
            install_authelia, "generate_issuer_key", _fake_issuer_key
        ),
        mock.patch.object(install_authelia, "generate_tls_key", _fake_tls_key),
    ):
        with pytest.raises(install_authelia.MissingCACert):
            install_authelia.main(
                [
                    "--project-dir",
                    str(stack),
                    "--admin-email",
                    "admin@x.local",
                    "--no-up",
                ]
            )


def test_main_rejects_bad_compose_file(tmp_path):
    stack = _stack(tmp_path)
    (stack / "docker-compose.yml").write_text("# no name line\nservices:\n")

    with (
        mock.patch.object(
            install_authelia, "generate_issuer_key", _fake_issuer_key
        ),
        mock.patch.object(install_authelia, "generate_tls_key", _fake_tls_key),
    ):
        with pytest.raises(install_authelia.BadComposeFile):
            install_authelia.main(
                [
                    "--project-dir",
                    str(stack),
                    "--admin-email",
                    "admin@x.local",
                    "--no-up",
                ]
            )
