"""Shared steps of the room provisioner's end-to-end tests.

A test stack is generated from the template, its ``soliplex-dev`` and
``soliplex-provisioner`` images are built with ``soliplex-template``
installed from a wheel of this checkout,
and only the services the provisioner talks to are brought up (``--no-deps``:
an empty room's database needs no document conversion, so ``docling-serve``
stays down).

Wheels reach the images through the *generated* stack's copies of the
Dockerfiles, patched here; the template's own Dockerfiles are not changed.

Setting ``SOLIPLEX_FUNCTEST_SOLIPLEX_REPO`` (a soliplex git repository) and
``SOLIPLEX_FUNCTEST_SOLIPLEX_REF`` (a ref in it) installs that soliplex over
the backend image's own (``--no-deps``), built from a ``git archive`` of the
ref -- e.g. to test against an unreleased soliplex feature.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import warnings

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "skills" / "soliplex-template" / "scripts"
GENERATOR = SCRIPTS / "generate_soliplex_project.py"
PROVISION_ROOM = SCRIPTS / "provision_room.py"
SOLIPLEX_CLI = "/app/.venv/bin/soliplex-cli"

SOLIPLEX_REPO_ENV = "SOLIPLEX_FUNCTEST_SOLIPLEX_REPO"
SOLIPLEX_REF_ENV = "SOLIPLEX_FUNCTEST_SOLIPLEX_REF"

# soliplex-dev/Dockerfile: the template install step's first line, and the
# same line with the build context bind-mounted for a local wheel.
_DEV_INSTALL = "RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \\\n"
_DEV_INSTALL_WITH_CONTEXT = (
    "RUN --mount=type=bind,target=/context \\\n"
    "    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \\\n"
)

# backend/Dockerfile: the line the soliplex override is installed before
# (the user setup, while the build still runs as root).
_BACKEND_USER_SETUP = "ARG PUID=1000\n"
_BACKEND_SOLIPLEX_OVERRIDE = (
    "RUN --mount=type=bind,source=soliplex-wheel,target=/soliplex-wheel \\\n"
    "    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \\\n"
    "    uv pip install --python /app/.venv/bin/python --no-deps "
    "--reinstall /soliplex-wheel/*.whl\n"
    "\n"
)


def run(*argv, cwd=None, env=None, stdin=None):
    return subprocess.run(
        [str(arg) for arg in argv],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
    )


def compose(out, *args, stdin=None):
    return run(
        "docker", "compose", "--project-directory", out, *args, stdin=stdin
    )


def check(result, what):
    assert result.returncode == 0, (
        f"{what} failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result.stdout


def require_tools():
    """Skip (with a warning) unless docker, its daemon, git and uv work."""
    missing = [t for t in ("docker", "git", "uv") if shutil.which(t) is None]
    if missing:
        warnings.warn(
            f"missing {missing}; skipping the provisioner functional test",
            stacklevel=2,
        )
        pytest.skip(f"needs {', '.join(missing)}")
    if run("docker", "info").returncode != 0:
        warnings.warn(
            "docker daemon unreachable; skipping the provisioner test",
            stacklevel=2,
        )
        pytest.skip("docker daemon not reachable")


def generate(work: pathlib.Path, params: dict) -> pathlib.Path:
    out = work / "project"
    params_file = work / "params.json"
    params_file.write_text(json.dumps(params), encoding="utf-8")
    check(
        run(
            sys.executable,
            GENERATOR,
            "--out",
            out,
            "--params",
            params_file,
            "--disable-gpg-sign",
        ),
        "generation",
    )
    return out


def _soliplex_override(work: pathlib.Path) -> pathlib.Path | None:
    """A soliplex wheel built from the configured ref, or None."""
    repo = os.environ.get(SOLIPLEX_REPO_ENV)
    ref = os.environ.get(SOLIPLEX_REF_ENV)
    if not (repo and ref):
        return None
    archive = work / "soliplex.tar"
    check(
        run("git", "-C", repo, "archive", "--output", archive, ref),
        f"git archive {ref}",
    )
    source = work / "soliplex-src"
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter="data")
    wheels = work / "soliplex-wheel"
    check(
        run("uv", "build", "--wheel", "--out-dir", wheels, cwd=source),
        "building the soliplex wheel",
    )
    return wheels


def build_images(out: pathlib.Path, work: pathlib.Path) -> None:
    """Build ``backend`` (any soliplex override), ``soliplex-dev`` and
    ``soliplex-provisioner`` (which shares the latter's build).
    """
    wheels = _soliplex_override(work)
    if wheels is not None:
        shutil.copytree(wheels, out / "backend" / "soliplex-wheel")
        dockerfile = out / "backend" / "Dockerfile"
        text = dockerfile.read_text()
        assert text.count(_BACKEND_USER_SETUP) == 1
        dockerfile.write_text(
            text.replace(
                _BACKEND_USER_SETUP,
                _BACKEND_SOLIPLEX_OVERRIDE + _BACKEND_USER_SETUP,
            )
        )
    check(compose(out, "build", "backend"), "building backend")

    context = out / "soliplex-dev"
    check(
        run("uv", "build", "--wheel", "--out-dir", context, cwd=REPO_ROOT),
        "building the soliplex-template wheel",
    )
    [wheel] = context.glob("soliplex_template-*.whl")
    dockerfile = context / "Dockerfile"
    text = dockerfile.read_text()
    assert text.count(_DEV_INSTALL) == 2
    dockerfile.write_text(
        text.replace(_DEV_INSTALL, _DEV_INSTALL_WITH_CONTEXT, 1)
    )
    requirement = (
        f"soliplex-template[provisioner] @ file:///context/{wheel.name}"
    )
    check(
        compose(
            out,
            "build",
            "--build-arg",
            f"SOLIPLEX_TEMPLATE={requirement}",
            "soliplex-dev",
            "soliplex-provisioner",
        ),
        "building soliplex-dev / soliplex-provisioner",
    )


def up(out: pathlib.Path) -> None:
    """Bring up postgres, then the backend and the ingester."""
    check(compose(out, "up", "-d", "--wait", "postgres"), "postgres up")
    check(
        compose(
            out,
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "300",
            "backend",
            "haiku-ingester",
        ),
        "backend / haiku-ingester up",
    )


def up_provisioner(out: pathlib.Path) -> None:
    """Bring up the provisioner service (after :func:`up`)."""
    check(
        compose(
            out,
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "soliplex-provisioner",
        ),
        "soliplex-provisioner up",
    )


# Run inside the provisioner service's container: one HTTP call, described
# on stdin, answered as '{"status": ..., "body": ...}' on stdout.
_SERVICE_CALL = """\
import json, sys, urllib.error, urllib.request
call = json.load(sys.stdin)
data = None if call["body"] is None else json.dumps(call["body"]).encode()
request = urllib.request.Request(
    "http://localhost:8090" + call["path"],
    data=data,
    method=call["method"],
    headers={
        "Authorization": "Bearer " + call["token"],
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        status, text = response.status, response.read().decode()
except urllib.error.HTTPError as error:
    status, text = error.code, error.read().decode()
json.dump({"status": status, "body": json.loads(text)}, sys.stdout)
"""


def call_service(out, method, path, token, body=None) -> tuple[int, dict]:
    """Call the provisioner service as ``token``'s user: (status, body)."""
    call = {"method": method, "path": path, "token": token, "body": body}
    answer = json.loads(
        check(
            compose(
                out,
                "exec",
                "-T",
                "soliplex-provisioner",
                "python",
                "-c",
                _SERVICE_CALL,
                stdin=json.dumps(call),
            ),
            f"{method} {path}",
        )
    )
    return answer["status"], answer["body"]


def soliplex_cli(out, *args, stdin=None):
    """Run ``soliplex-cli`` in a one-off ``soliplex-dev`` container."""
    return compose(
        out,
        "run",
        "--rm",
        "--no-TTY",
        "--quiet-build",
        "soliplex-dev",
        SOLIPLEX_CLI,
        *args,
        stdin=stdin,
    )


def provision(out, *args):
    """Run the skill's ``provision_room.py``; return the completed process."""
    return run(sys.executable, PROVISION_ROOM, "--project-dir", out, *args)


def committed_id(clone) -> str:
    return check(run("git", "-C", clone, "rev-parse", "HEAD"), "git").strip()
