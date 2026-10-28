"""Recover a scaffolded stack's ``.env`` from its generation manifest.

Companion to ``soliplex_template.secrets`` behind the generated project's
bundled ``scripts/generate_env.py`` PEP 723 shim: this module ships in the
published ``soliplex-template`` distribution so the shim can
``from soliplex_template.env import generate_env``.

It reads the stack's ``pyproject.toml`` ``[tool.soliplex-template.params]``
manifest (recorded at generation time) and writes ``OLLAMA_BASE_URL``,
``PUID``, and ``PGID`` into ``.env`` -- commented out, so nothing binds the
operator until they uncomment -- but only when ``.env`` does not already
exist (it is gitignored, so this is the fresh-clone recovery path).

Stdlib only -- :mod:`tomllib` to read the manifest.
"""

from __future__ import annotations

import pathlib
import tomllib

# .env is shared with secrets.py; import the name to keep one spelling.
from soliplex_template.secrets import ENV_FILE

# The environment variables a scaffolded stack's .env carries, as
# (manifest key, env key): what the generator writes via write_env().
_ENV_PARAM_KEYS: tuple[tuple[str, str], ...] = (
    ("ollama_base_url", "OLLAMA_BASE_URL"),
    ("puid", "PUID"),
    ("pgid", "PGID"),
)


class EnvError(Exception):
    """A user-facing error (printed without a traceback)."""


class PyProjectNotFound(EnvError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"cannot find pyproject.toml at: {path} "
            "(pass --project-dir pointing at the stack directory)"
        )


class ManifestBroken(EnvError):
    def __init__(self, path, exc):
        self.path, self.exc = path, exc
        super().__init__(f"cannot parse {path}: {exc}")


class ManifestMissing(EnvError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"{path} carries no [tool.soliplex-template.params] "
            "generation manifest (is this a scaffolded stack?)"
        )


class ManifestIncomplete(EnvError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__(
            f"generation manifest lacks key(s): {', '.join(missing)} "
            "(add the key(s) by hand, or re-run the generator)"
        )


def read_env_params(project: pathlib.Path) -> dict[str, str]:
    """Return the ``.env`` assignments the stack's generation manifest records.

    Reads ``[tool.soliplex-template.params]`` from the stack's
    ``pyproject.toml`` and maps ``ollama_base_url`` / ``puid`` / ``pgid``
    onto ``OLLAMA_BASE_URL`` / ``PUID`` / ``PGID``; raises :class:`EnvError`
    when the manifest is absent, unparseable, or missing a key.
    """
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        raise PyProjectNotFound(pyproject)
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ManifestBroken(pyproject, exc) from exc
    manifest = (
        data.get("tool", {}).get("soliplex-template", {}).get("params", {})
    )
    if not manifest:
        raise ManifestMissing(pyproject)
    missing = [key for key, _ in _ENV_PARAM_KEYS if key not in manifest]
    if missing:
        raise ManifestIncomplete(missing)
    return {
        env_key: str(manifest[manifest_key])
        for manifest_key, env_key in _ENV_PARAM_KEYS
    }


def _render_recovered(assignments: dict[str, str]) -> str:
    lines = [
        "# Recreated by scripts/generate_env.py from the generation manifest",
        "# ([tool.soliplex-template.params] in pyproject.toml). Commented",
        "# out: uncomment what applies, then 'docker compose up'.",
        "#",
        "# The backend needs OLLAMA_BASE_URL pointing at a server serving the",
        "# models named in backend/environment/installation.yaml.",
    ]
    lines.extend(f"# {key}={value}" for key, value in assignments.items())
    return "\n".join(lines) + "\n"


def generate_env(project_dir) -> None:
    """Write ``.env`` from the stack's generation manifest, only if missing.

    Recreates the gitignored ``.env`` of a fresh clone from the committed
    ``[tool.soliplex-template.params]`` manifest, at mode ``0600``, with
    every value commented out; reports the no-op when ``.env`` exists.
    Raises :class:`EnvError` when the manifest is absent or incomplete.
    """
    project = pathlib.Path(project_dir).resolve()
    env = project / ENV_FILE
    if env.is_file():
        print(f"nothing to do: {env} exists")
        return
    assignments = read_env_params(project)
    env.write_text(_render_recovered(assignments))
    env.chmod(0o600)
    print(f"wrote {env}: values commented out, uncomment what applies")
