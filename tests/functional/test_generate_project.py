"""Functional (black-box) test for the Soliplex project generator.

Unlike the unit suite (which mocks the seams), this drives the real CLI
end-to-end: it runs ``skill/scripts/generate_soliplex_project.py`` as a
subprocess with ``--run-secrets`` and real ``git``, then verifies the generated
project renders cleanly, the parameters propagate consistently, the secret
and git side effects actually happened, the rendered ``docker-compose.yml`` is
valid to Docker, and the ``postgres`` service (whose ``init.sh`` + secret
wiring is itself generated) starts and reports healthy.

Every live dependency is gated and **skips with a warning** when absent:
git/bash/openssl for the generation step, and the docker CLI/daemon for the
``docker compose`` tiers.

This tree is opt-in -- it is not in ``testpaths``. Run it with:

    uv run --group dev pytest tests/functional --no-cov
"""

from __future__ import annotations

import json
import pathlib
import shutil
import stat
import subprocess
import sys
import warnings

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "skill" / "scripts" / "generate_soliplex_project.py"

# Non-default parameters: prove substitution propagates and use high host ports
# unlikely to collide with anything already running on the developer's machine.
_PARAMS = {
    "project_name": "soliplex-functest",
    "server_name": "functest.local",
    "ollama_base_url": "http://ollama.invalid:11434",
    "agui_db": "functest_agui",
    "authz_db": "functest_authz",
    "nginx_http": 19000,
    "nginx_https": 19443,
    "ingester_port": 18765,
    "docling_port": 15001,
    "postgres_port": 15432,
    # A docs dir the template does NOT ship, so ensure_runtime_dirs creates it
    # empty and drops a .gitkeep (exercising that branch + the compose mount).
    "docs_dir": "rag/inbox",
}

_GEN_TOOLS = ("git", "bash", "openssl")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _compose(cwd, *args):
    """Run ``docker compose <args>`` in ``cwd``; return its result."""
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _read(out, *parts):
    return out.joinpath(*parts).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Gating fixtures (skip-with-warning)
# --------------------------------------------------------------------------
@pytest.fixture
def docker_required():
    """Skip (with a warning) unless docker CLI + compose plugin are usable."""
    if shutil.which("docker") is None:
        warnings.warn(
            "docker not on PATH; skipping live-Docker functional tests",
            stacklevel=2,
        )
        pytest.skip("docker CLI not available")
    if _compose(_REPO_ROOT, "version").returncode != 0:
        warnings.warn(
            "`docker compose` unusable; skipping live-Docker tests",
            stacklevel=2,
        )
        pytest.skip("docker compose plugin not usable")


@pytest.fixture
def docker_daemon_required(docker_required):
    """Skip (with a warning) unless the docker daemon is reachable."""
    if (
        subprocess.run(
            ["docker", "info"], capture_output=True, text=True
        ).returncode
        != 0
    ):
        warnings.warn(
            "docker daemon unreachable; skipping `up` functional test",
            stacklevel=2,
        )
        pytest.skip("docker daemon not reachable")


# --------------------------------------------------------------------------
# Generate the project once for the module.
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def generated_project(tmp_path_factory):
    """Run the real generator (subprocess) and yield ``(out, params)``."""
    missing = [t for t in _GEN_TOOLS if shutil.which(t) is None]
    if missing:
        warnings.warn(
            f"missing {missing}; skipping generation functional tests",
            stacklevel=2,
        )
        pytest.skip(f"generation needs {', '.join(_GEN_TOOLS)}")

    work = tmp_path_factory.mktemp("functest")
    out = work / "project"
    params_file = work / "params.json"
    params_file.write_text(json.dumps(_PARAMS), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--out",
            str(out),
            "--params",
            str(params_file),
            "--run-secrets",
            "--disable-gpg-sign",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generator failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return out, _PARAMS


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def test_no_leftover_mako_files(generated_project):
    out, _params = generated_project

    leftover = list(out.rglob("*.mako"))

    assert leftover == []


def test_key_artifacts_exist(generated_project):
    out, _params = generated_project

    expected = [
        "docker-compose.yml",
        ".env",
        "pyproject.toml",
        "README.md",
        "backend/environment/installation.yaml",
        "nginx/nginx.conf",
        "postgres/config/init.sh",
        "scripts/generate-secrets.sh",
    ]

    missing = [rel for rel in expected if not (out / rel).is_file()]
    assert missing == []


def test_parameters_substituted(generated_project):
    out, params = generated_project

    compose = _read(out, "docker-compose.yml")
    nginx = _read(out, "nginx/nginx.conf")
    installation = _read(out, "backend/environment/installation.yaml")
    init_sh = _read(out, "postgres/config/init.sh")
    pyproject = _read(out, "pyproject.toml")
    readme = _read(out, "README.md")

    assert f"name: {params['project_name']}" in compose
    assert f"{params['nginx_http']}:9000" in compose
    assert f"{params['postgres_port']}:5432" in compose
    assert f"server_name {params['server_name']};" in nginx
    assert f"{params['project_name']}-conf" in installation
    assert params["agui_db"] in init_sh
    assert params["authz_db"] in init_sh
    assert f'name = "{params["project_name"]}"' in pyproject
    assert params["project_name"] in readme
    assert f"./{params['docs_dir']}:/docs" in compose


def test_cross_file_consistency(generated_project):
    out, params = generated_project

    compose = _read(out, "docker-compose.yml")
    installation = _read(out, "backend/environment/installation.yaml")
    init_sh = _read(out, "postgres/config/init.sh")

    # DB names are driven from one parameter into both files.
    assert params["agui_db"] in installation
    assert params["agui_db"] in init_sh
    assert params["authz_db"] in installation
    assert params["authz_db"] in init_sh
    # Every chosen host port lands in the compose mapping.
    for key in (
        "nginx_http",
        "nginx_https",
        "ingester_port",
        "docling_port",
        "postgres_port",
    ):
        assert f"{params[key]}:" in compose


def test_escaped_literals_survived(generated_project):
    out, _params = generated_project

    compose = _read(out, "docker-compose.yml")
    haiku = _read(out, "haiku.rag/haiku.rag.yaml")

    # <%text>-escaped runtime interpolations must reach the output verbatim.
    assert "${OLLAMA_BASE_URL}" in compose
    assert "${INGESTER_TOKEN:-secret}" in compose
    assert "__INGESTER_TOKEN__" in haiku


def test_runtime_dirs_have_gitkeep(generated_project):
    out, params = generated_project

    keeps = [
        "backend/uploads/rooms/.gitkeep",
        "backend/uploads/threads/.gitkeep",
        f"{params['docs_dir']}/.gitkeep",
    ]

    missing = [rel for rel in keeps if not (out / rel).is_file()]
    assert missing == []


def test_env_file_written(generated_project):
    out, params = generated_project

    env = _read(out, ".env")

    assert f"OLLAMA_BASE_URL={params['ollama_base_url']}" in env
    assert "INGESTER_TOKEN=" in env


# --------------------------------------------------------------------------
# Side effects: secrets + git
# --------------------------------------------------------------------------
def test_run_secrets_wrote_gen_files(generated_project):
    out, _params = generated_project

    gen_files = sorted(p.name for p in (out / ".secrets").glob("*.gen"))

    assert gen_files == [
        "agui_db_password.gen",
        "authz_db_password.gen",
        "postgres_password.gen",
        "url_safe_token_secret.gen",
    ]
    for name in gen_files:
        mode = stat.S_IMODE((out / ".secrets" / name).stat().st_mode)
        assert mode == 0o600, f"{name} has mode {oct(mode)}"


def test_git_initialised_with_single_clean_commit(generated_project):
    out, _params = generated_project

    log = subprocess.run(
        ["git", "-C", str(out), "log", "--oneline"],
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "-C", str(out), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )

    assert (out / ".git").is_dir()
    assert len(log.stdout.strip().splitlines()) == 1
    assert status.stdout.strip() == ""  # .secrets/ and .env are gitignored


# --------------------------------------------------------------------------
# Docker: the rendered compose file is valid, and postgres comes up healthy.
# --------------------------------------------------------------------------
@pytest.mark.needs_docker
def test_docker_compose_config_valid(generated_project, docker_required):
    out, _params = generated_project

    result = _compose(out, "config", "-q")

    assert result.returncode == 0, result.stderr


@pytest.fixture
def postgres_up(generated_project, docker_daemon_required):
    """Bring up only postgres (--wait), yield, and always tear down."""
    out, params = generated_project
    # config is a precondition: never attempt `up` on a file that won't parse.
    cfg = _compose(out, "config", "-q")
    if cfg.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{cfg.stderr}")

    up = _compose(
        out, "up", "-d", "--wait", "--wait-timeout", "180", "postgres"
    )
    try:
        yield out, params, up
    finally:
        _compose(out, "down", "-v")


@pytest.mark.needs_docker
def test_postgres_service_comes_up_healthy(postgres_up):
    out, _params, up = postgres_up

    # `up --wait` returns 0 only once postgres reports healthy.
    assert up.returncode == 0, f"up failed:\n{up.stdout}\n{up.stderr}"

    ps = _compose(out, "ps", "--format", "json", "postgres")
    entries = [
        json.loads(line) for line in ps.stdout.splitlines() if line.strip()
    ]
    postgres = next(e for e in entries if e.get("Service") == "postgres")
    assert postgres.get("State") == "running"
    if postgres.get("Health"):  # healthcheck is defined, so this is present
        assert postgres["Health"] == "healthy"
