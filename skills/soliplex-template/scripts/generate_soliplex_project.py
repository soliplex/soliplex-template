#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "mako",
#   "packaging",
#   "skills-ref==0.1.1",
#   "soliplex-template>=0.12",
# ]
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
import urllib.parse

import skills_ref
from mako import exceptions as mako_exceptions
from mako import template
from packaging import specifiers

from soliplex_template.secrets import generate_secrets

# This skill's own directory (two levels up from scripts/); its SKILL.md is the
# source of the generation-manifest skill identity.
SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent

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
    # Project. Deliberately NOT "soliplex": that is the name of the Soliplex
    # distribution itself, so a stack called "soliplex" puts its own project
    # directory at src/soliplex/ -- exactly where a clone of the upstream repo
    # goes, and its package at src/soliplex/src/soliplex/, where the installed
    # distribution's own modules live. "-dojo" also reads as a place to
    # practice.
    "project_name": "soliplex-dojo",
    "setup_id": None,  # derived from project_name if unset
    # Host ports. nginx's publish; the rest publish only when set
    # (None = unpublished).
    "nginx_http": 9000,
    "nginx_https": 9443,
    "ingester_port": None,
    "docling_port": None,
    "postgres_port": None,
    "backend_port": None,
    "gitea_http_port": None,
    "gitea_ssh_port": None,
    # Server / TLS
    "server_name": None,  # derived from project_name if unset
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
    "chunk_size": 256,
    # Postgres (role == database name for each)
    "agui_db": "soliplex_agui",
    "authz_db": "soliplex_authz",
    # Container UID/GID alignment. Default (None) is derived from the host
    # operator running the generator (os.getuid()/os.getgid()), so host-owned
    # bind mounts and 0600 Docker secrets are readable by the in-container
    # service users. Set explicitly to run services as a fixed account on a
    # deploy host whose login uid differs from the runtime uid.
    "puid": None,  # derived from os.getuid() if unset
    "pgid": None,  # derived from os.getgid() if unset
    # Version pins
    # Tag of the published soliplex images the backend
    # (ghcr.io/soliplex/soliplex-postgres) and TUI
    # (ghcr.io/soliplex/soliplex-tui) build on: "latest", a moving
    # "<major>.<minor>" (follows patch releases on 'docker compose build
    # --pull'), or an exact "<major>.<minor>.<patch>".
    "soliplex_image_tag": "0.89",
    # The 'soliplex' requirement in the stack's host-side pyproject.toml files
    # (dev mode, and src/<package>/). Must admit the image tag's release.
    "soliplex_dev_constraint": ">= 0.89, < 0.90",
    # haiku-ingester image tag (ghcr.io/ggozad/haiku.rag-slim). Not a free
    # choice: the soliplex release constrains 'haiku.rag-slim' as a
    # dependency, and the ingester writes the LanceDB the backend reads, so
    # this has to satisfy the range the 'soliplex_image_tag' release implies.
    "haiku_rag_version": "0.104.0",
    # The docling-serve image the converter service runs: the variant the
    # host can use (the 'cpu' variant converts on any host; 'base' and
    # the 'cuNNN' variants want a GPU) and the docling-serve version,
    # which has to pair with the pinned 'haiku.rag-slim' release
    # (see docling-serve/README.md).  The composed image is baked into
    # the generated 'docker-compose.yml' (committed with the stack's
    # initial commit);  upstream tags no moving tag on the CUDA
    # variants ('latest' is only valid for 'cpu' / 'base').
    "docling_serve_variant": "cpu",
    "docling_serve_version": "v1.38.0",
    # Tag of the published web-client image (ghcr.io/soliplex/frontend-web)
    # nginx serves: a moving "<major>.<minor>" (follows patch releases on
    # 'docker compose build --pull'), an exact "<major>.<minor>.<patch>" or
    # "<major>.<minor>.<patch>-<build>", or "latest".
    "frontend_version": "0.110",
    # Auth: "no-auth" | "auth"
    "auth_mode": "no-auth",
    # Gitea service (opt-in): nginx-proxied at /gitea/ on HTTPS,
    # provisioned by scripts/init_gitea.py. Web use needs no published
    # port; gitea_http_port / gitea_ssh_port serve direct HTTP and
    # git-over-SSH access.
    "include_gitea": False,
    # TUI service (opt-in): include the textual-serve-backed `tui` service
    # (web TUI at /tui/) plus the tui/ build context. Omitted entirely when
    # false. Its image shares soliplex_image_tag with the backend's.
    "include_tui": False,
    # Sandbox (opt-in): let the 'bubble-sandbox' skill run code, by lifting
    # the backend container's seccomp / AppArmor / '/proc' restrictions that
    # 'bwrap' trips over. When false the compose block ships commented out,
    # and add_room.py skips room templates that configure the skill.
    "enable_sandbox": False,
    # Ingester
    "docs_dir": "rag/docs",
}

# nginx's ports always publish; the optional port params publish only
# when set.
NGINX_PORT_KEYS = (
    "nginx_http",
    "nginx_https",
)
OPTIONAL_PORT_KEYS = (
    "ingester_port",
    "docling_port",
    "postgres_port",
    "backend_port",
    "gitea_http_port",
    "gitea_ssh_port",
)
PORT_KEYS = NGINX_PORT_KEYS + OPTIONAL_PORT_KEYS
INT_KEYS = (
    tuple(k for k in NGINX_PORT_KEYS)
    + tuple(k for k in OPTIONAL_PORT_KEYS)
    + ("rag_embed_dim", "chunk_size")
)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Import names the generated package must not claim. "soliplex" is the
# Soliplex distribution's own: the generated package has no __init__.py, so it
# would merge with the installed one as a namespace portion -- and, being first
# on PYTHONPATH, its demo tools.py/views.py would shadow the real
# soliplex.tools/soliplex.views subpackages, breaking every import beneath
# them. src/soliplex/ is also where src_projects.py clones a dev-mode checkout.
RESERVED_PACKAGE_NAMES = frozenset({"soliplex"})
# Tags the frontend-web image workflow publishes: "latest", a moving
# "<major>.<minor>", or an exact "<major>.<minor>.<patch>" with an optional
# "-<build>" (the release tag's "+<build>", which a Docker tag cannot carry).
FRONTEND_VERSION_RE = re.compile(r"^(latest|\d+\.\d+(\.\d+(-\d+)?)?)$")
# A soliplex/frontend *release* tag ("v0.110.0+96"), as the parameter took
# before nginx built from the image: rejected, with the image tag to use.
_FRONTEND_RELEASE_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)(?:\+(\d+))?$")
# Tags the soliplex image workflow publishes: "latest", a moving
# "<major>.<minor>", or an exact "<major>.<minor>.<patch>".
IMAGE_TAG_RE = re.compile(r"^(latest|\d+\.\d+(\.\d+)?)$")
# docling-serve images: 'cpu' / 'base' also carry a moving 'latest'; the
# CUDA variants are version-tag-only upstream (explicit 'v<m.m.p>').
DOCLING_SERVE_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
DOCLING_SERVE_VARIANT_SUFFIXES = {
    "cpu": "-cpu",
    "base": "",
    "cu128": "-cu128",
    "cu130": "-cu130",
}
DOCLING_SERVE_IMAGE_BASE = "quay.io/docling-project/docling-serve"
# How many patch releases of a moving "<major>.<minor>" tag to check against
# soliplex_dev_constraint: far more than any series has had.
_SERIES_PATCHES = 100
# Parameters an older params.json may still carry, with what replaced them.
RENAMED_PARAMS = {
    "soliplex_backend_constraint": (
        "soliplex_image_tag, soliplex_dev_constraint"
    ),
    "soliplex_tui_constraint": "soliplex_image_tag",
    "ingester_token": (
        "not a parameter anymore: the bearer token is generated into "
        ".secrets/ingester_bearer.gen"
    ),
}


class GenError(Exception):
    """A user-facing generation error (printed without a traceback)."""


class ParamsUnreadable(GenError):
    def __init__(self, path, exc):
        self.path = path
        self.exc = exc
        super().__init__(f"cannot read --params file {path}: {exc}")


class ParamsNotJSON(GenError):
    def __init__(self, exc):
        self.exc = exc
        super().__init__(f"--params is not valid JSON: {exc}")


class ParamsNotObject(GenError):
    def __init__(self):
        super().__init__("--params JSON must be an object/dict")


class UnknownParams(GenError):
    def __init__(self, names):
        self.names = names
        joined = ", ".join(
            f"{name} (now {RENAMED_PARAMS[name]})"
            if name in RENAMED_PARAMS
            else name
            for name in sorted(names)
        )
        super().__init__(f"unknown parameter(s) in --params: {joined}")


class NotAnInteger(GenError):
    def __init__(self, key, value):
        self.key = key
        self.value = value
        super().__init__(f"{key} must be an integer, got {value!r}")


class OllamaRequired(GenError):
    def __init__(self):
        super().__init__(
            "ollama_base_url is required (e.g. http://host:11434)"
        )


class PortOutOfRange(GenError):
    def __init__(self, key, port):
        self.key = key
        self.port = port
        super().__init__(f"{key}={port} is out of range 1..65535")


class PortCollision(GenError):
    def __init__(self, port, first, second):
        self.port = port
        self.first = first
        self.second = second
        super().__init__(f"port {port} used by both {first} and {second}")


class BadIdentifier(GenError):
    def __init__(self, key, value):
        self.key = key
        self.value = value
        super().__init__(f"{key}={value!r} must be a valid SQL identifier")


class BadUID(GenError):
    def __init__(self, key, value):
        self.key = key
        self.value = value
        super().__init__(
            f"{key}={value!r} must be a positive integer below 2**31 "
            "(0 is rejected: containers must not run as root)"
        )


class BadFrontendVersion(GenError):
    def __init__(self, value):
        self.value = value
        message = (
            f"frontend_version={value!r} must be 'latest' or a "
            "ghcr.io/soliplex/frontend-web image tag: '<major>.<minor>', "
            "'<major>.<minor>.<patch>', or '<major>.<minor>.<patch>-<build>'"
        )
        release = _FRONTEND_RELEASE_TAG_RE.match(str(value))
        if release:
            version, build = release.groups()
            tag = f"{version}-{build}" if build else version
            message += (
                f". That is a release tag: the image's tag drops the 'v' and "
                f"spells '+' as '-' ({tag!r}), and images exist only from "
                "0.110 on"
            )
        super().__init__(message)


class BadPackageName(GenError):
    def __init__(self, project_name, package_name):
        self.project_name = project_name
        self.package_name = package_name
        super().__init__(
            f"project_name={project_name!r} yields package name "
            f"{package_name!r}, which is not a valid Python identifier "
            "(use letters, digits, '-'/'_'; must not start with a digit "
            "or be a Python keyword)"
        )


class ReservedPackageName(GenError):
    def __init__(self, project_name, package_name):
        self.project_name = project_name
        self.package_name = package_name
        super().__init__(
            f"project_name={project_name!r} yields package name "
            f"{package_name!r}, which is the Soliplex distribution's own "
            "import name. The generated package would shadow "
            "'soliplex.tools' and 'soliplex.views', and src/soliplex/ is "
            "where a dev-mode checkout of Soliplex is cloned. Choose another "
            "name (the default is 'soliplex-dojo')."
        )


class DatabasesMusDiffer(GenError):
    def __init__(self):
        super().__init__("agui_db and authz_db must differ")


class BadAuthMode(GenError):
    def __init__(self, value):
        self.value = value
        super().__init__(
            f"auth_mode must be 'no-auth' or 'auth', got {value!r}"
        )


class BadBoolean(GenError):
    def __init__(self, key, value):
        self.key = key
        self.value = value
        super().__init__(f"{key} must be a boolean, got {value!r}")


class EmptyConstraint(GenError):
    def __init__(self, key):
        self.key = key
        super().__init__(f"{key} must not be empty")


class BadImageTag(GenError):
    def __init__(self, value):
        self.value = value
        super().__init__(
            f"soliplex_image_tag={value!r} must be 'latest', "
            "'<major>.<minor>', or '<major>.<minor>.<patch>'"
        )


class ImageTagOutsideConstraint(GenError):
    def __init__(self, tag, constraint):
        self.tag = tag
        self.constraint = constraint
        super().__init__(
            f"soliplex_image_tag={tag!r} is outside "
            f"soliplex_dev_constraint={constraint!r}: the host-side "
            "projects would pin a different soliplex release than the "
            "images run"
        )


class BadDoclingVariant(GenError):
    def __init__(self, value):
        self.value = value
        super().__init__(
            f"docling_serve_variant={value!r} must be one of "
            f"{', '.join(sorted(DOCLING_SERVE_VARIANT_SUFFIXES))}"
        )


class BadDoclingVersion(GenError):
    def __init__(self, value, variant):
        self.value = value
        self.variant = variant
        super().__init__(
            f"docling_serve_version={value!r} is not usable with "
            f"docling_serve_variant={variant!r}:  'cpu' and 'base' "
            "carry a moving 'latest';  the CUDA variants are "
            "version-tag-only upstream"
        )


class BadDocsDir(GenError):
    def __init__(self, value):
        self.value = value
        super().__init__(
            "docs_dir must be a relative path inside the project, "
            f"got {value!r}"
        )


class RenderFailed(GenError):
    def __init__(self, rel, detail):
        self.rel = rel
        self.detail = detail
        super().__init__(f"failed to render {rel}:\n{detail}")


class OutIsRequired(GenError):
    def __init__(self):
        super().__init__("--out or --parent is required")


class OutAndParentConflict(GenError):
    def __init__(self):
        super().__init__(
            "--out and --parent are mutually exclusive: --out names the "
            "project directory itself, --parent the directory to create it in"
        )


class TemplateNotFound(GenError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"embedded template not found at {path}; if running from a "
            "soliplex-template checkout, run "
            "scripts/refresh_skill_template.py to generate it "
            "(it is a gitignored build artifact)"
        )


class OutNotEmpty(GenError):
    def __init__(self, out):
        self.out = out
        super().__init__(
            f"{out} exists and is not empty (use --force to override)"
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
            raise ParamsUnreadable(path, exc) from exc
        try:
            supplied = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ParamsNotJSON(exc) from exc
        if not isinstance(supplied, dict):
            raise ParamsNotObject()
        unknown = set(supplied) - set(DEFAULTS)
        if unknown:
            raise UnknownParams(unknown)
        params.update(supplied)

    if args.interactive:
        params = prompt_interactive(params)

    return params


def _print_report(params: dict[str, object], out) -> None:
    """Print the post-generation summary (ports/models/auth/flags)."""

    def _port(key: str) -> str:
        """Format an optional port ('unpublished' when unset)."""
        val = params.get(key)
        return str(val) if val else "unpublished"

    print(f"\n✓ Scaffolded {params['project_name']} at {out}\n")

    print(
        "  ports:    "
        f"nginx {params['nginx_http']}/{params['nginx_https']}, "
        f"ingester {_port('ingester_port')}, "
        f"docling {_port('docling_port')}, "
        f"postgres {_port('postgres_port')}, "
        f"backend {_port('backend_port')}"
    )
    if params.get("include_gitea"):
        print(
            f"            gitea {_port('gitea_http_port')}"
            f"/{_port('gitea_ssh_port')}" + " (HTTP/SSH)"
        )
    print(
        f"  models:   chat={params['chat_model']} "
        f"title={params['title_model']} "
        f"rag-qa={params['rag_qa_model']}"
    )
    print(f"  ollama:   {params['ollama_base_url']}")
    print(f"  auth:     {params['auth_mode']}")
    print(f"  gitea:    {'yes' if params.get('include_gitea') else 'no'}")
    print(f"  tui:      {'yes' if params.get('include_tui') else 'no'}")
    print(f"  sandbox:  {'yes' if params.get('enable_sandbox') else 'no'}")


def prompt_interactive(params: dict[str, object]) -> dict[str, object]:
    print("Enter values (blank = keep the shown default).\n")
    for key in DEFAULTS:
        current = params.get(key)
        if key in OPTIONAL_PORT_KEYS and current is None:
            label = f"  {key} [unpublished -- blank skips]: "
        else:
            shown = "" if current is None else current
            label = f"  {key} [{shown}]: "
        try:
            answer = input(label).strip()
        except EOFError:
            answer = ""
        if answer:
            params[key] = answer
    return params


_TRUE_STRINGS = {"1", "true", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "no", "n", "off", ""}


def _as_bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise BadBoolean(key, value)


def coerce_and_derive(params: dict[str, object]) -> dict[str, object]:
    # Integers. Optional port params may be absent (None = unpublished);
    # a blank string from the interactive interview means the same.
    for key in INT_KEYS:
        val = params[key]
        if key in OPTIONAL_PORT_KEYS and val in (None, ""):
            params[key] = None
            continue
        try:
            params[key] = int(val)
        except (TypeError, ValueError) as exc:
            raise NotAnInteger(key, val) from exc

    # Import name for the synthesized src/ package: lower-case the project
    # name and turn hyphens into underscores. ``project_name`` itself (which
    # may keep hyphens) stays the distribution / compose name.
    params["package_name"] = (
        str(params["project_name"]).lower().replace("-", "_")
    )

    # Derived defaults
    if not params.get("setup_id"):
        params["setup_id"] = f"{params['project_name']}-conf"
    # Per-stack public host: default to '<project>.localhost' so each generated
    # stack gets its own browser origin. Must precede tls_subject (CN below).
    if not params.get("server_name"):
        params["server_name"] = f"{params['project_name']}.localhost"
    if not params.get("tls_subject"):
        params["tls_subject"] = (
            f"/C=US/ST=State/L=City/O=Soliplex/CN={params['server_name']}"
        )

    # Auth flag consumed by docker-compose.yml.mako (trailing space matters).
    auth_mode = str(params["auth_mode"])
    params["backend_auth_flag"] = (
        "--no-auth-mode " if auth_mode == "no-auth" else ""
    )

    # The docling-serve image ref: variant suffix + version, shape-checked
    # in validate().  Baked into the generated 'docker-compose.yml'.
    params["docling_serve_image"] = (
        f"{DOCLING_SERVE_IMAGE_BASE}"
        f"{DOCLING_SERVE_VARIANT_SUFFIXES[str(params['docling_serve_variant'])]}"
        f":{params['docling_serve_version']}"
    )

    # Opt-in Gitea service (consumed by the conditional blocks in the
    # compose / nginx / init.sh templates).
    params["include_gitea"] = _as_bool(
        params.get("include_gitea"), "include_gitea"
    )

    # Opt-in TUI service (consumed by the conditional blocks in the compose
    # template and the authored docs; gates the tui/ build context in
    # render_tree).
    params["include_tui"] = _as_bool(params.get("include_tui"), "include_tui")

    # Opt-in sandbox (consumed by the conditional 'security_opt' blocks in the
    # compose template).
    params["enable_sandbox"] = _as_bool(
        params.get("enable_sandbox"), "enable_sandbox"
    )

    # Container UID/GID. Unset (None) -> the host operator's uid/gid so the
    # secret files this run writes are owned by, and the bind mounts are
    # writable by, the in-container service users. An explicit value is coerced
    # to int here and range-checked in validate(). os.getuid()/os.getgid() are
    # POSIX-only; generation already requires bash/openssl/docker, so a POSIX
    # host is assumed.
    for key, detect in (("puid", os.getuid), ("pgid", os.getgid)):
        value = params.get(key)
        if value is None:
            detected = detect()
            if detected == 0:
                # Don't silently configure the stack to run as root; fall back
                # to the conventional service uid the images historically used.
                print(
                    f"warning: {key} would default to 0 (root); using 1000 "
                    "instead",
                    file=sys.stderr,
                )
                detected = 1000
            params[key] = detected
        else:
            try:
                params[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise NotAnInteger(key, value) from exc

    # macOS aligns on the login account instead of its primary GID:  the
    # login GID there ('staff', 20) collides with a group every base
    # image carries ('dialout', 20), failing the postgres build's
    # groupmod.  The login UID has no such collision in the image and
    # owns the bind mounts anyway.
    if sys.platform == "darwin" and params["pgid"] == 20:
        print(
            "warning: macOS login GID 20 ('staff') collides with the image's"
            " 'dialout' group;  aligning the stack on the login UID",
            file=sys.stderr,
        )
        params["pgid"] = params["puid"]

    return params


def image_tag_satisfies(tag: str, constraint: str) -> bool:
    """Can the release ``tag`` names satisfy ``constraint``?

    ``latest`` names no fixed release, and a constraint ``packaging`` cannot
    parse is left for the resolver to report, so both pass. An exact
    ``X.Y.Z`` must be in the range; a moving ``X.Y`` passes when any of its
    patch releases is."""
    if tag == "latest":
        return True
    try:
        spec = specifiers.SpecifierSet(constraint)
    except specifiers.InvalidSpecifier:
        return True
    if tag.count(".") == 2:
        return tag in spec
    return any(f"{tag}.{patch}" in spec for patch in range(_SERIES_PATCHES))


def validate(params: dict[str, object]) -> None:
    if not str(params.get("ollama_base_url") or "").strip():
        raise OllamaRequired()

    package = str(params["package_name"])
    if not package.isidentifier() or keyword.iskeyword(package):
        raise BadPackageName(params["project_name"], package)
    if package in RESERVED_PACKAGE_NAMES:
        raise ReservedPackageName(params["project_name"], package)

    seen: dict[int, str] = {}
    for key in PORT_KEYS:
        port = params.get(key)
        if key in OPTIONAL_PORT_KEYS and port is None:
            continue
        if not (1 <= port <= 65535):
            raise PortOutOfRange(key, port)
        if port in seen:
            raise PortCollision(port, seen[port], key)
        seen[port] = key

    for key in ("agui_db", "authz_db"):
        val = str(params[key])
        if not IDENT_RE.match(val):
            raise BadIdentifier(key, val)
    if params["agui_db"] == params["authz_db"]:
        raise DatabasesMusDiffer()

    for key in ("puid", "pgid"):
        val = params[key]
        if not isinstance(val, int) or not (0 < val < 2**31):
            raise BadUID(key, val)

    if params["auth_mode"] not in ("no-auth", "auth"):
        raise BadAuthMode(params["auth_mode"])

    constraint = str(params["soliplex_dev_constraint"])
    if not constraint.strip():
        raise EmptyConstraint("soliplex_dev_constraint")

    tag = str(params["soliplex_image_tag"])
    if not IMAGE_TAG_RE.match(tag):
        raise BadImageTag(tag)
    if not image_tag_satisfies(tag, constraint):
        raise ImageTagOutsideConstraint(tag, constraint)

    frontend_version = str(params["frontend_version"])
    if not FRONTEND_VERSION_RE.match(frontend_version):
        raise BadFrontendVersion(frontend_version)

    docling_variant = str(params["docling_serve_variant"])
    if docling_variant not in DOCLING_SERVE_VARIANT_SUFFIXES:
        raise BadDoclingVariant(docling_variant)
    docling_version = str(params["docling_serve_version"])
    if docling_variant in ("cpu", "base"):
        if not (
            DOCLING_SERVE_VERSION_RE.match(docling_version)
            or docling_version == "latest"
        ):
            raise BadDoclingVersion(docling_version, docling_variant)
    elif not DOCLING_SERVE_VERSION_RE.match(docling_version):
        # CUDA images are version-tag-only upstream:  no moving tag.
        raise BadDoclingVersion(docling_version, docling_variant)

    docs = str(params["docs_dir"])
    if docs.startswith("/") or ".." in pathlib.Path(docs).parts:
        raise BadDocsDir(docs)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def read_skill_metadata(skill_dir: pathlib.Path) -> dict[str, str]:
    """Return ``version`` / ``source_commit`` / ``generated`` for a skill.

    Reads the generating skill's own (already-validated) SKILL.md frontmatter
    via ``skills_ref.read_properties``; fields not yet stamped into the build
    come back blank. The skill must be valid for generation to run at all, so
    a parse/validation failure here would be a genuine bug, not handled.
    """
    # ``metadata`` is None when the frontmatter has no ``metadata:`` block
    # (e.g. an unstamped build); treat that as empty so fields read back blank.
    metadata = skills_ref.read_properties(skill_dir).metadata or {}
    return {
        key: metadata.get(key, "")
        for key in ("version", "source_commit", "generated")
    }


def _sanitize_param(key: str, value: object) -> object:
    """Scrub a param value before it is recorded in the manifest.

    An ``ollama_base_url`` with userinfo has its credentials stripped (the
    manifest lands in the committed pyproject.toml; the rest of the value is
    needed to re-probe the API server). Other values pass through; there are
    no secret parameters since #244 -- the ingester control plane's bearer
    lives in the generated ``.secrets/ingester_bearer.gen``, never in params.
    """
    if key == "ollama_base_url" and isinstance(value, str):
        parts = urllib.parse.urlsplit(value)
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            return urllib.parse.urlunsplit(parts._replace(netloc=host))
    return value


def _toml_scalar(value: object) -> str:
    """Format a Python scalar as a TOML value (bool/int/string)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_manifest(params: dict[str, object], meta: dict[str, str]) -> str:
    """Build the ``[tool.soliplex-template]`` manifest TOML for pyproject.toml.

    Records the generating skill's identity (blank for unstamped fields) and
    the full resolved parameter set (secret-free since #244: values that are
    secrets live in .secrets/*.gen files, not params), so a later update run
    can introspect how the project was scaffolded.
    """
    lines = [
        "[tool.soliplex-template]",
        "# Generation manifest (soliplex-template#73): how this project was",
        "# scaffolded. Hints for a later `soliplex-template` update run; safe",
        "# to inspect, do not hand-edit.",
        'skill_name = "soliplex-template"',
        f'skill_version = "{meta["version"]}"',
        f'skill_source_commit = "{meta["source_commit"]}"',
        f'skill_generated = "{meta["generated"]}"',
        "",
        "[tool.soliplex-template.params]",
    ]
    for key, value in params.items():
        if key in OPTIONAL_PORT_KEYS and value is None:
            # Absent optional port = unpublished: record nothing, so a
            # later update run reading the manifest does not mistake a
            # literal "None" for a port.
            continue
        lines.append(f"{key} = {_toml_scalar(_sanitize_param(key, value))}")
    return "\n".join(lines) + "\n"


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
        # The Gitea provisioning script only makes sense alongside the gitea
        # service; skip it when gitea was not requested.
        if not ctx.get("include_gitea") and rel == pathlib.Path(
            "scripts/init_gitea.py"
        ):
            continue
        # The tui/ build context only makes sense alongside the tui service;
        # skip the whole subtree when the TUI was not requested.
        if not ctx.get("include_tui") and rel.parts and rel.parts[0] == "tui":
            continue
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
                raise RenderFailed(rel, detail) from exc
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
    runtime = ["uploads/rooms", "uploads/threads", docs_dir]
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
        # UID/GID the containers build and run as (and that owns the generated
        # .secrets/*.gen files). Compose reads these; changing them requires a
        # rebuild of the built images. See scripts/generate_secrets.py.
        f"PUID={params['puid']}",
        f"PGID={params['pgid']}",
        "",
    ]
    # 0600 like the generated .secrets/*.gen files: the environment can name
    # an OLLAMA_BASE_URL that embeds credentials (or API keys), so read it
    # only from the owning account. The ingester control plane's bearer is
    # NOT here -- it lives in .secrets/ingester_bearer.gen (#244).
    (out / ".env").write_text("\n".join(lines))
    (out / ".env").chmod(0o600)


def maybe_run_secrets(out: pathlib.Path, run: bool) -> bool:
    if not run:
        return False
    # Call the library directly rather than the bundled shim: the shim resolves
    # 'soliplex-template' from PyPI via 'uv run', but the same logic lives in
    # the package we already import, so this works from the editable checkout
    # (and is what keeps the functional test green before a release publishes).
    generate_secrets(out)
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
    p.add_argument(
        "--parent",
        help="directory to create the project in; the project directory is "
        "named for project_name (alternative to --out)",
    )
    p.add_argument("--params", help="JSON file of parameter overrides")
    p.add_argument(
        "--interactive",
        action="store_true",
        help="prompt for parameters on stdin",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="allow writing into a non-empty target directory",
    )
    p.add_argument(
        "--generate-secrets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="generate the stack's Docker secrets after scaffolding "
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


def require_empty_target(out: pathlib.Path, force: bool) -> None:
    """Refuse to scaffold over a directory that already holds something."""
    if out.exists() and any(out.iterdir()) and not force:
        raise OutNotEmpty(out)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.print_defaults:
        print(json.dumps(DEFAULTS, indent=2))
        return 0

    if args.out and args.parent:
        raise OutAndParentConflict()

    if not args.out and not args.parent:
        raise OutIsRequired()

    template_root = (
        pathlib.Path(__file__).resolve().parent.parent / "assets" / "template"
    )
    if not template_root.is_dir():
        raise TemplateNotFound(template_root)

    # With --out the target is known up front, so check it before the
    # (possibly interactive) interview rather than after. With --parent the
    # directory is named for project_name, so the same check has to wait for
    # parameters to resolve -- and validate() has by then guaranteed that
    # project_name yields a valid Python identifier, so it cannot carry a
    # path separator into the join below.
    out = None
    if args.out:
        out = pathlib.Path(args.out).resolve()
        require_empty_target(out, args.force)

    params = load_params(args)
    params = coerce_and_derive(params)
    validate(params)

    if out is None:
        out = (
            pathlib.Path(args.parent) / str(params["project_name"])
        ).resolve()
        require_empty_target(out, args.force)

    out.mkdir(parents=True, exist_ok=True)
    manifest = render_manifest(params, read_skill_metadata(SKILL_DIR))
    render_tree(
        template_root,
        out,
        {**params, "soliplex_template_manifest": manifest},
    )
    ensure_runtime_dirs(out, str(params["docs_dir"]))
    write_env(out, params)
    ran_secrets = maybe_run_secrets(out, args.generate_secrets)
    did_git = maybe_git_init(out, not args.no_git, args.disable_gpg_sign)

    _print_report(params, out)
    git_status = "initial commit created" if did_git else "not initialized"
    print(f"  git:      {git_status}")
    # Build images explicitly before starting so their output stays legible
    # instead of being hidden inside `up`. There is no Flutter build in the
    # stack: the nginx image copies the prebuilt, cache-busted web client
    # from the published ghcr.io/soliplex/frontend-web image.
    print("\nNext steps:")
    steps = []
    if not ran_secrets:
        steps.append("uv run scripts/generate_secrets.py")
    steps.append("docker compose build")
    steps.append("docker compose up -d   # start the stack in the background")
    steps.append("docker compose ps      # wait until services are healthy")
    print(f"  1. cd {out}")
    for i, step in enumerate(steps, start=2):
        print(f"  {i}. {step}")
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
