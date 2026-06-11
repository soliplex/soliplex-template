"""Generate the Docker secrets a Soliplex stack needs at first-time setup.

This is the reusable core behind the generated project's bundled
``scripts/generate_secrets.py`` -- a PEP 723 shim that ships in every stack and
delegates here. It ships in the published ``soliplex-template`` distribution so
the skill's project generator (and the shim) can
``from soliplex_template.secrets import generate_secrets``.

It scans the stack's ``docker-compose.yml`` for the ``*.gen`` secret files
declared under ``secrets:``, writes a fresh random password into each at mode
``0600``, and -- because Compose bind-mounts those files preserving the host
owner/mode and the in-container service users read them at that mode -- re-owns
them to the ``PUID:PGID`` the images run as (read from ``.env``; default
``1000:1000``) when the operator's uid/gid differs. Re-owning to an arbitrary
uid needs privilege the operator may lack, so it goes through a throwaway root
``docker`` container; if docker is unavailable it warns and leaves it to the
operator.

Stdlib only -- random via :mod:`secrets` (the stdlib module; the sibling-module
name does not shadow it under absolute imports), no ``openssl``/``bash``.
"""

from __future__ import annotations

import collections.abc
import os
import pathlib
import secrets
import shutil
import string
import subprocess

# Password shape: 32 chars from [A-Za-z0-9] (mirrors the old shell generator's
# `openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32`).
PASSWORD_LENGTH = 32
PASSWORD_ALPHABET = string.ascii_letters + string.digits

# UID/GID the built images run as (and that must own the secret files); read
# from the stack's .env, falling back to these when unset.
DEFAULT_PUID = "1000"
DEFAULT_PGID = "1000"

COMPOSE_FILE = "docker-compose.yml"
SECRETS_DIR = ".secrets"
ENV_FILE = ".env"
GEN_SUFFIX = ".gen"


class SecretsError(Exception):
    """A user-facing error (printed without a traceback)."""

    @classmethod
    def compose_not_found(cls, path):
        return cls(
            f"cannot find compose file at: {path} "
            "(run with --project-dir pointing at the stack directory)"
        )


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """A cryptographically-random ``[A-Za-z0-9]`` password of ``length``."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def discover_secret_files(
    compose_text: str,
) -> collections.abc.Iterator[str]:
    """Yield the ``*.gen`` secret paths declared in a compose file.

    Mirrors the old shell scan: lines of the form ``<indent>file: <path>``
    whose path ends in ``.gen``, with any leading ``./`` stripped.
    """
    for line in compose_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("file:"):
            value = stripped[len("file:") :].strip()
            if value.endswith(GEN_SUFFIX):
                yield value.removeprefix("./")


def read_puid_pgid(env_path: pathlib.Path) -> tuple[str, str]:
    """The ``PUID``/``PGID`` from ``.env`` (last wins), or the defaults."""
    puid, pgid = DEFAULT_PUID, DEFAULT_PGID
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("PUID="):
                value = line[len("PUID=") :].strip()
                if value:
                    puid = value
            elif line.startswith("PGID="):
                value = line[len("PGID=") :].strip()
                if value:
                    pgid = value
    return puid, pgid


def _maybe_reown(secrets_dir: pathlib.Path, env_path: pathlib.Path) -> None:
    puid, pgid = read_puid_pgid(env_path)
    cur_uid, cur_gid = str(os.getuid()), str(os.getgid())
    if (puid, pgid) == (cur_uid, cur_gid):
        return
    docker = shutil.which("docker")
    if docker is None:
        print(
            f"WARNING: secrets are owned by {cur_uid}:{cur_gid}, but services "
            f"run as {puid}:{pgid}."
        )
        print(
            f"Install docker, or chown {secrets_dir}/*{GEN_SUFFIX} to "
            f"{puid}:{pgid} manually, before 'up'."
        )
        return
    print(f"Re-owning secrets to {puid}:{pgid} (container uid/gid)...")
    subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-u",
            "0:0",
            "-v",
            f"{secrets_dir}:/secrets",
            "busybox",
            "chown",
            "-R",
            f"{puid}:{pgid}",
            "/secrets",
        ],
        check=True,
    )


def _print_summary(
    secrets_dir: pathlib.Path,
    generated: list[tuple[str, str]],
    project: pathlib.Path,
) -> None:
    print()
    print(f"=== Successfully generated {len(generated)} secret(s) ===")
    print(f"Secret files created in: {secrets_dir}")
    print()
    print("=== Generated passwords ===")
    for name, password in generated:
        print(f"  {name.removesuffix(GEN_SUFFIX)}: {password}")
    print()
    print("IMPORTANT: save these securely; they will not be displayed again.")
    print()
    print("=== Next steps ===")
    print(f"  cd {project}")
    print("  docker compose build postgres")
    print("  docker compose up -d")


def generate_secrets(project_dir) -> None:
    """Generate every ``*.gen`` secret declared in the stack's compose file.

    Writes a fresh ``0600`` password file per declared secret and re-owns them
    to ``PUID:PGID`` when the operator's uid/gid differs; the written files are
    the result. Raises :class:`SecretsError` if the compose file is missing.
    """
    project = pathlib.Path(project_dir).resolve()
    compose = project / COMPOSE_FILE
    if not compose.is_file():
        raise SecretsError.compose_not_found(compose)

    secrets_dir = project / SECRETS_DIR
    secrets_dir.mkdir(parents=True, exist_ok=True)

    generated: list[tuple[str, str]] = []
    for rel in discover_secret_files(compose.read_text()):
        secret_file = project / rel
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        password = generate_password()
        secret_file.write_text(password)
        secret_file.chmod(0o600)
        generated.append((secret_file.name, password))
        print(f"✓ Generated: {secret_file.name}")

    if not generated:
        print(f"WARNING: no *{GEN_SUFFIX} files found in {COMPOSE_FILE}")
        return

    _maybe_reown(secrets_dir, project / ENV_FILE)
    _print_summary(secrets_dir, generated, project)
