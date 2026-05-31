#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["mako"]
# ///
"""Scaffold a new Soliplex Docker Compose project from the embedded template.

Run with uv (provisions Mako automatically):

    uv run generate_soliplex_project.py --out ../my-stack --params params.json

or, without uv:

    pip install mako && \
        python3 generate_soliplex_project.py --out ../my-stack --interactive

The embedded template lives at ``../assets/template`` relative to this script.
Files ending in ``.mako`` are rendered through Mako with the resolved
parameters; every other file is copied byte-for-byte.
"""

from __future__ import annotations

import argparse
import json
import keyword
import os
import pathlib
import re
import shutil
import subprocess
import sys

from mako import exceptions as mako_exceptions
from mako import template

# --------------------------------------------------------------------------
# Parameters
#
# Every key here is a user-facing parameter with its default.
# ``ollama_base_url`` has no usable default (None) and must be supplied.
# ``setup_id`` / ``tls_subject`` default to None and are derived from other
# answers when left unset.
# See references/PARAMETERS.md for the authoritative descriptions.
# --------------------------------------------------------------------------
DEFAULTS: dict[str, object] = {
    # Project
    "project_name": "soliplex",
    "setup_id": None,  # derived from project_name if unset
    # Host ports
    "nginx_http": 9000,
    "nginx_https": 9443,
    "ingester_port": 8765,
    "docling_port": 5001,
    "postgres_port": 5432,
    # Server / TLS
    "server_name": "localhost",
    "tls_subject": None,  # derived from server_name if unset
    # Ollama (required)
    "ollama_base_url": None,
    # Models
    "chat_model": "gpt-oss:latest",
    "chat_model_alt": "gpt-oss:20b",
    "title_model": "gpt-oss:latest",
    "rag_embed_model": "qwen3-embedding:4b",
    "rag_embed_dim": 2560,
    "rag_qa_model": "gpt-oss:latest",
    "rag_research_model": "gpt-oss:latest",
    "chunk_size": 256,
    # Postgres (role == database name for each)
    "agui_db": "soliplex_agui",
    "authz_db": "soliplex_authz",
    # Version pins
    "soliplex_backend_constraint": ">= 0.68, < 0.69",
    "soliplex_tui_constraint": ">= 0.60.6, < 0.61",
    # Frontend: "latest" (newest soliplex/frontend release, the historical
    # behavior) or a specific soliplex/frontend release tag to pin.
    "frontend_version": "latest",
    # Auth: "no-auth" | "auth"
    "auth_mode": "no-auth",
    # Ingester
    "docs_dir": "rag/docs",
    "ingester_token": "secret",
}

PORT_KEYS = (
    "nginx_http",
    "nginx_https",
    "ingester_port",
    "docling_port",
    "postgres_port",
)
INT_KEYS = PORT_KEYS + ("rag_embed_dim", "chunk_size")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# "latest" or a soliplex/frontend release tag (letters, digits, '.', '_', '-').
FRONTEND_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class GenError(Exception):
    """A user-facing generation error (printed without a traceback).

    Message construction lives in these classmethod factories so call sites
    read ``raise GenError.<reason>(...)`` with no inline message string.
    """

    @classmethod
    def params_unreadable(cls, path, exc):
        return cls(f"cannot read --params file {path}: {exc}")

    @classmethod
    def params_not_json(cls, exc):
        return cls(f"--params is not valid JSON: {exc}")

    @classmethod
    def params_not_object(cls):
        return cls("--params JSON must be an object/dict")

    @classmethod
    def unknown_params(cls, names):
        joined = ", ".join(sorted(names))
        return cls(f"unknown parameter(s) in --params: {joined}")

    @classmethod
    def not_int(cls, key, value):
        return cls(f"{key} must be an integer, got {value!r}")

    @classmethod
    def ollama_required(cls):
        return cls("ollama_base_url is required (e.g. http://host:11434)")

    @classmethod
    def port_out_of_range(cls, key, port):
        return cls(f"{key}={port} is out of range 1..65535")

    @classmethod
    def port_collision(cls, port, first, second):
        return cls(f"port {port} used by both {first} and {second}")

    @classmethod
    def bad_identifier(cls, key, value):
        return cls(f"{key}={value!r} must be a valid SQL identifier")

    @classmethod
    def bad_frontend_version(cls, value):
        return cls(
            f"frontend_version={value!r} must be 'latest' or a "
            "soliplex/frontend release tag (letters, digits, '.', '_', '-')"
        )

    @classmethod
    def bad_package_name(cls, project_name, package_name):
        return cls(
            f"project_name={project_name!r} yields package name "
            f"{package_name!r}, which is not a valid Python identifier "
            "(use letters, digits, '-'/'_'; must not start with a digit "
            "or be a Python keyword)"
        )

    @classmethod
    def dbs_must_differ(cls):
        return cls("agui_db and authz_db must differ")

    @classmethod
    def bad_auth_mode(cls, value):
        return cls(f"auth_mode must be 'no-auth' or 'auth', got {value!r}")

    @classmethod
    def empty_constraint(cls, key):
        return cls(f"{key} must not be empty")

    @classmethod
    def bad_docs_dir(cls, value):
        return cls(
            "docs_dir must be a relative path inside the project, "
            f"got {value!r}"
        )

    @classmethod
    def render_failed(cls, rel, detail):
        return cls(f"failed to render {rel}:\n{detail}")

    @classmethod
    def out_required(cls):
        return cls("--out is required")

    @classmethod
    def template_not_found(cls, path):
        return cls(f"embedded template not found at {path}")

    @classmethod
    def out_not_empty(cls, out):
        return cls(
            f"--out {out} exists and is not empty (use --force to override)"
        )


# --------------------------------------------------------------------------
# Parameter resolution
# --------------------------------------------------------------------------
def load_params(args: argparse.Namespace) -> dict[str, object]:
    params: dict[str, object] = dict(DEFAULTS)

    if args.params:
        path = pathlib.Path(args.params)
        try:
            raw = path.read_text()
        except OSError as exc:
            raise GenError.params_unreadable(path, exc) from exc
        try:
            supplied = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenError.params_not_json(exc) from exc
        if not isinstance(supplied, dict):
            raise GenError.params_not_object()
        unknown = set(supplied) - set(DEFAULTS)
        if unknown:
            raise GenError.unknown_params(unknown)
        params.update(supplied)

    if args.interactive:
        params = prompt_interactive(params)

    return params


def prompt_interactive(params: dict[str, object]) -> dict[str, object]:
    print("Enter values (blank = keep the shown default).\n")
    for key in DEFAULTS:
        current = params.get(key)
        shown = "" if current is None else current
        label = f"  {key} [{shown}]: "
        try:
            answer = input(label).strip()
        except EOFError:
            answer = ""
        if answer:
            params[key] = answer
    return params


def coerce_and_derive(params: dict[str, object]) -> dict[str, object]:
    # Integers
    for key in INT_KEYS:
        try:
            params[key] = int(params[key])
        except (TypeError, ValueError) as exc:
            raise GenError.not_int(key, params[key]) from exc

    # Import name for the synthesized src/ package: lower-case the project
    # name and turn hyphens into underscores. ``project_name`` itself (which
    # may keep hyphens) stays the distribution / compose name.
    params["package_name"] = (
        str(params["project_name"]).lower().replace("-", "_")
    )

    # Derived defaults
    if not params.get("setup_id"):
        params["setup_id"] = f"{params['project_name']}-conf"
    if not params.get("tls_subject"):
        params["tls_subject"] = (
            f"/C=US/ST=State/L=City/O=Soliplex/CN={params['server_name']}"
        )

    # GitHub releases API path used by nginx/Dockerfile: "latest" stays on the
    # /releases/latest endpoint; a pinned tag selects /releases/tags/<tag>.
    frontend_version = str(params["frontend_version"])
    params["frontend_release_path"] = (
        "latest"
        if frontend_version == "latest"
        else f"tags/{frontend_version}"
    )

    # Auth flag consumed by docker-compose.yml.mako (trailing space matters).
    auth_mode = str(params["auth_mode"])
    params["backend_auth_flag"] = (
        "--no-auth-mode " if auth_mode == "no-auth" else ""
    )

    return params


def validate(params: dict[str, object]) -> None:
    if not str(params.get("ollama_base_url") or "").strip():
        raise GenError.ollama_required()

    package = str(params["package_name"])
    if not package.isidentifier() or keyword.iskeyword(package):
        raise GenError.bad_package_name(params["project_name"], package)

    seen: dict[int, str] = {}
    for key in PORT_KEYS:
        port = params[key]
        if not (1 <= port <= 65535):
            raise GenError.port_out_of_range(key, port)
        if port in seen:
            raise GenError.port_collision(port, seen[port], key)
        seen[port] = key

    for key in ("agui_db", "authz_db"):
        val = str(params[key])
        if not IDENT_RE.match(val):
            raise GenError.bad_identifier(key, val)
    if params["agui_db"] == params["authz_db"]:
        raise GenError.dbs_must_differ()

    if params["auth_mode"] not in ("no-auth", "auth"):
        raise GenError.bad_auth_mode(params["auth_mode"])

    for key in ("soliplex_backend_constraint", "soliplex_tui_constraint"):
        if not str(params[key]).strip():
            raise GenError.empty_constraint(key)

    frontend_version = str(params["frontend_version"])
    if not FRONTEND_VERSION_RE.match(frontend_version):
        raise GenError.bad_frontend_version(frontend_version)

    docs = str(params["docs_dir"])
    if docs.startswith("/") or ".." in pathlib.Path(docs).parts:
        raise GenError.bad_docs_dir(docs)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def render_tree(
    template_root: pathlib.Path, out: pathlib.Path, ctx: dict[str, object]
) -> None:
    package_name = ctx.get("package_name")
    for src in sorted(template_root.rglob("*")):
        rel = src.relative_to(template_root)
        # The package tree ships under a literal ``__package__`` directory; map
        # that segment onto the resolved import name so it lands at
        # ``src/<package_name>/...``.
        if package_name:
            rel = pathlib.Path(
                *[
                    package_name if part == "__package__" else part
                    for part in rel.parts
                ]
            )
        if src.is_dir():
            (out / rel).mkdir(parents=True, exist_ok=True)
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".mako":
            dest = dest.with_suffix("")  # strip .mako
            try:
                text = template.Template(
                    filename=str(src), strict_undefined=True
                ).render(**ctx)
            except Exception as exc:
                detail = mako_exceptions.text_error_template().render()
                raise GenError.render_failed(rel, detail) from exc
            dest.write_text(text)
            # Rendered shell scripts need the executable bit.
            mode = 0o755 if dest.suffix == ".sh" else 0o644
            dest.chmod(mode)
        else:
            shutil.copy2(
                src, dest
            )  # preserves mode (executable scripts, etc.)


def ensure_runtime_dirs(out: pathlib.Path, docs_dir: str) -> None:
    # Gitignored runtime dirs without a tracked placeholder in the template.
    runtime = ["backend/uploads/rooms", "backend/uploads/threads", docs_dir]
    for rel in runtime:
        d = out / rel
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not any(d.iterdir()):
            keep.touch()


def write_env(out: pathlib.Path, params: dict[str, object]) -> None:
    lines = [
        "# Generated by the soliplex project generator. Edit as needed.",
        f"OLLAMA_BASE_URL={params['ollama_base_url']}",
        f"INGESTER_TOKEN={params['ingester_token']}",
        "",
    ]
    (out / ".env").write_text("\n".join(lines))


def maybe_run_secrets(out: pathlib.Path, run: bool) -> bool:
    script = out / "scripts" / "generate-secrets.sh"
    if not run:
        return False
    if shutil.which("bash") is None or shutil.which("openssl") is None:
        print("  (skipped generate-secrets.sh: bash/openssl not available)")
        return False
    subprocess.run(["bash", str(script)], cwd=out, check=True)
    return True


def maybe_git_init(
    out: pathlib.Path, do_git: bool, disable_gpg_sign: bool
) -> bool:
    if not do_git:
        return False
    if shutil.which("git") is None:
        print("  (skipped git init: git not available)")
        return False
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init", "-q"], cwd=out, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=out, check=True, env=env)
    commit = [
        "git",
        "-c",
        "user.name=soliplex-template",
        "-c",
        "user.email=noreply@soliplex.invalid",
    ]
    # By default we respect the host's commit-signing config;
    # --disable-gpg-sign opts out (useful in non-interactive / CI
    # environments where a signing prompt would hang).
    if disable_gpg_sign:
        commit += ["-c", "commit.gpgsign=false"]
    commit += [
        "commit",
        "-q",
        "-m",
        "Initial Soliplex project scaffolded by soliplex-template",
    ]
    subprocess.run(commit, cwd=out, check=True, env=env)
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scaffold a Soliplex Docker Compose project."
    )
    p.add_argument("--out", help="target directory for the new project")
    p.add_argument("--params", help="JSON file of parameter overrides")
    p.add_argument(
        "--interactive",
        action="store_true",
        help="prompt for parameters on stdin",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="allow writing into a non-empty --out",
    )
    p.add_argument(
        "--generate-secrets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run scripts/generate-secrets.sh after scaffolding "
        "(default: enabled; use --no-generate-secrets to skip)",
    )
    p.add_argument(
        "--no-git",
        action="store_true",
        help="do not git init / commit the result",
    )
    p.add_argument(
        "--disable-gpg-sign",
        action="store_true",
        help="pass commit.gpgsign=false for the initial commit "
        "(default: respect the host git config)",
    )
    p.add_argument(
        "--print-defaults",
        action="store_true",
        help="print the default parameters as JSON and exit",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.print_defaults:
        print(json.dumps(DEFAULTS, indent=2))
        return 0

    if not args.out:
        raise GenError.out_required()

    template_root = (
        pathlib.Path(__file__).resolve().parent.parent / "assets" / "template"
    )
    if not template_root.is_dir():
        raise GenError.template_not_found(template_root)

    out = pathlib.Path(args.out).resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        raise GenError.out_not_empty(out)

    params = load_params(args)
    params = coerce_and_derive(params)
    validate(params)

    out.mkdir(parents=True, exist_ok=True)
    render_tree(template_root, out, params)
    ensure_runtime_dirs(out, str(params["docs_dir"]))
    write_env(out, params)
    ran_secrets = maybe_run_secrets(out, args.generate_secrets)
    did_git = maybe_git_init(out, not args.no_git, args.disable_gpg_sign)

    print(f"\n✓ Scaffolded {params['project_name']} at {out}\n")
    print(
        "  ports:    "
        f"nginx {params['nginx_http']}/{params['nginx_https']}, "
        f"ingester {params['ingester_port']}, "
        f"docling {params['docling_port']}, "
        f"postgres {params['postgres_port']}"
    )
    print(
        f"  models:   chat={params['chat_model']} "
        f"title={params['title_model']} "
        f"rag-qa={params['rag_qa_model']}"
    )
    print(f"  ollama:   {params['ollama_base_url']}")
    print(f"  auth:     {params['auth_mode']}")
    git_status = "initial commit created" if did_git else "not initialized"
    print(f"  git:      {git_status}")
    print("\nNext steps:")
    n = 1
    if not ran_secrets:
        print(f"  {n}. cd {out} && ./scripts/generate-secrets.sh")
        n += 1
    print(f"  {n}. cd {out} && docker compose up")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except GenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc})", file=sys.stderr)
        sys.exit(2)
