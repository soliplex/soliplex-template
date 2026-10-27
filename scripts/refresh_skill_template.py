#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mako"]
# ///
"""Regenerate skills/soliplex-template/assets/template/ from the live repo
exemplars.

The embedded template shipped inside the soliplex-template skill is a
parameterized copy of this repo's stack. When the repo's exemplar files change
(docker-compose.yml, installation.yaml, nginx.conf, …), run this to re-derive
the template so the generator can be exercised against current exemplars:

    uv run scripts/refresh_skill_template.py

What it does:
  1. wipes skills/soliplex-template/assets/template/
  2. copies every tracked repo file (minus the excludes below) in verbatim
  3. rewrites the parameterized files as <name>.mako (Mako ${param} + <%text>
     escaping for literal ${...})
  4. writes the authored templates (README.md.mako, the two
     pyproject.toml.mako, the src/__package__/ project tree, …)
  5. render-checks every .mako with Mako

Only *tracked* files are picked up (it uses ``git ls-files``); commit new stack
files before refreshing. Transforms assert on the exemplar text, so if an
upstream edit moves the ground out from under a rule the refresh fails loudly
rather than emitting a broken template.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "skills" / "soliplex-template" / "assets" / "template"

# Tracked paths NOT copied into the template (git pathspecs, repo-relative).
EXCLUDE_PATHSPECS = [
    ":!TODO.md",
    ":!.claude",
    # The repo's own ignore rules (e.g. /dist/): the stack's copy is derived
    # from the checked-in .gitignore.mako instead, and shipping the plain
    # file too would race it for the same rendered path.
    ":!.gitignore",
    ":!.github",  # this repo's CI, not a project file
    ":!.pre-commit-config.yaml",  # repo pre-commit config, not a project file
    ":!skills/soliplex-template",  # the skill source itself (no recursion)
    ":!dist",  # build artifact
    ":!tests",  # this repo's test suite, not project files
    ":!scripts/build_skill.py",  # skill-build tooling, not project files
    ":!scripts/refresh_skill_template.py",
    # The repo's own published package; a generated project depends on it from
    # PyPI (via the bundled scripts' PEP 723 metadata), not a vendored copy.
    ":!src/soliplex_template",
    ":!pyproject.toml",  # repo tooling; project gets pyproject.toml.mako
    ":!uv.lock",  # repo tooling lockfile
    ":!README.md",  # replaced by the authored README.md.mako
    ":!docs",  # repo docs site; the project gets its own .mako docs
    ":!zensical.toml",  # repo docs config; project gets zensical.toml.mako
    # repo's backend/README.md points at the upstream Pages site; the project
    # gets an authored pointer to its own local docs/ instead.
    ":!backend/README.md",
]


class RefreshError(Exception):
    """Error refreshing template"""


class RenderFailures(RefreshError):
    def __init__(self, count):
        self.count = count
        super().__init__(f"{count} template(s) failed the render check")


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise RefreshError(msg)


# User-facing docs: derived from authoritative Markdown under docs/users/.
#
# The shipped docs/*.md.mako are NOT authored as string literals here; they are
# The shipped docs/*.md.mako are derived from plain Markdown living under
# the repo's docs/users/ tree --
# one source of truth: the page is edited once, and the same fence-and-
# parameter conventions apply everywhere. The authoritative .md reads on the
# repo's own docs site (concrete defaults; conditional blocks fenced in HTML
# comments that render to nothing), and t_user_doc() turns it into the
# parameterized, conditional Mako the generator consumes.
#
# docs/ is excluded from the verbatim copy (see EXCLUDE_PATHSPECS), so this
# step reads docs/users/ straight from the repo. Repo-only doc trees -- the
# getting-started page (docs/getting-started/, how to *create* a stack) and the
# contributing docs (docs/contributing/) -- live outside docs/users/, so they
# are never derived and never shipped.
USER_DOCS_SRC = "docs/users"


def t_user_doc(text):
    """Derive a shipped ``docs/*.md.mako`` from an authoritative user doc.

    Steps, in order:
      0. drop ``<!-- site-only -->`` / ``<!-- endsite-only -->`` blocks --
         content that belongs only on this repo's docs site (e.g. a banner
         noting these pages describe a *generated* project), removed from the
         docs that ship into a generated project;
      1. escape literal ``${...}`` that is *not* a generator parameter (e.g.
         the socat ``${OLLAMA_RELAY_BIND:-172.17.0.1}`` expansion shown to
         the reader) so Mako passes it through; ``${param}`` spellings on the
         page survive for the generator to bake;
      3. escape Markdown H2+ headings, since Mako reads a leading ``##`` as a
         line comment (the H1 ``#`` is safe);
      4. turn the HTML-comment conditional fences (``<!-- if:gitea -->`` /
         ``<!-- endif -->``) into Mako ``% if include_gitea:`` / ``% endif``;
         an ``ifp:X`` fence conditions on a bare parameter instead
         (``<!-- ifp:ingester_port -->`` → ``% if ingester_port:``), for
         opt-in port params whose presence means the service is published.
    """
    text = re.sub(
        r"(?ms)^[ \t]*<!-- site-only -->[ \t]*\n"
        r".*?^[ \t]*<!-- endsite-only -->[ \t]*\n\n?",
        "",
        text,
    )
    known = frozenset(PROBE)
    text = re.sub(
        r"\$\{([^}]*)\}",
        lambda m: (
            m.group(0)
            if m.group(1) in known
            else f"<%text>{m.group(0)}</%text>"
        ),
        text,
    )
    text = re.sub(r"(?m)^(#{2,} .*)$", r"<%text>\1</%text>", text)
    text = re.sub(
        r"(?m)^[ \t]*<!-- if:(\w+) -->[ \t]*$", r"% if include_\1:", text
    )
    text = re.sub(r"(?m)^[ \t]*<!-- ifp:(\w+) -->[ \t]*$", r"% if \1:", text)
    text = re.sub(r"(?m)^[ \t]*<!-- endif -->[ \t]*$", r"% endif", text)
    return text


# --------------------------------------------------------------------------
# Authored templates (no repo exemplar): written verbatim.
# --------------------------------------------------------------------------
AUTHORED = {
    "README.md.mako": """\
# ${project_name}

A Soliplex Docker Compose stack (nginx + Soliplex backend + Flutter frontend +
haiku-ingester + Postgres, plus docling-serve and a TUI), scaffolded from the
`soliplex-template` project generator.

<%text>## First-time setup</%text>

```bash
uv run scripts/generate_secrets.py   # populates .secrets/*.gen (gitignored)
```

Set `OLLAMA_BASE_URL` in `.env` if it is not already correct.

<%text>## Run the stack</%text>

```bash
docker compose up            # foreground
docker compose up -d         # detached
docker compose logs -f backend
docker compose down          # stop (keeps the postgres_data volume)
docker compose down -v       # stop AND wipe the postgres volume
```

<%text>## Ports</%text>

| Service          | Host port |
|------------------|-----------|
| nginx (HTTP)     | ${nginx_http} |
| nginx (HTTPS)    | ${nginx_https} |
% if ingester_port:
| haiku-ingester   | ${ingester_port} (opt-in) |
% endif
% if docling_port:
| docling-serve    | ${docling_port} (opt-in) |
% endif
% if postgres_port:
| postgres         | ${postgres_port} (opt-in) |
% endif

Only nginx is published by default. Every other service binds only the
compose network; set its
`*_port` parameter (ingester_port, docling_port, postgres_port,
backend_port, gitea_http_port, gitea_ssh_port) to publish one.

Open the app at <http://localhost:${nginx_http}/> (or
<https://${server_name}:${nginx_https}/> for TLS).

<%text>## Custom Python package</%text>

Every entry under `src/` is a self-contained project directory. This project's
own is `src/${package_name}/` -- an installable Python library with a demo
tool and FastAPI router already wired up:

```bash
cd src/${package_name}
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
```

Clone any other repo you want to hack on as a sibling (`src/<other>/`) and the
same two commands work there.

See [Custom Python package](docs/custom-package.md) for how the package is put
on the backend's import path and referenced by dotted name from the Soliplex
config.

<%text>## Documentation</%text>

Full documentation for this project lives under `docs/`, built with
[Zensical](https://zensical.org):

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/
```
""",
    # The stack root is a tooling environment, not a distribution: the
    # importable code is its own project under src/__package__/ (below).
    "pyproject.toml.mako": """\
[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# Host-side dependencies for driving *the stack* -- e.g. running
# `soliplex-cli` against the Postgres backing store. The container images
# carry their own soliplex release (and system libpq); this file is for
# host-side use, so it takes the self-contained 'psycopg[binary]' driver:
#   uv sync                 # create/refresh the dev environment
dependencies = [
    "soliplex[postgres-binary] ${soliplex_dev_constraint}",
]

# Nothing at the stack root is importable, so there is nothing to build here.
# This project's own library lives in its own project directory,
# src/${package_name}/ -- run `uv sync` / `uv run pytest` from there.
[tool.uv]
package = false

[dependency-groups]
dev = [
    # Builds the documentation site under docs/ (`uv run zensical build`).
    "zensical",
]

${soliplex_template_manifest}\
""",
    # Every entry under src/ is a project directory in the same shape: a
    # pyproject.toml, an src/ package, and its own tests. This is ours; a
    # checkout cloned alongside it (the soliplex repo, a third-party
    # dependency you want to hack on) is a sibling, not a special case.
    "src/__package__/pyproject.toml.mako": """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# What the code under src/${package_name}/ imports. Host-side use:
#   cd src/${package_name}
#   uv sync                 # create/refresh the dev environment
#   uv run pytest           # run this project's tests
#   uv pip install -e .     # or a plain editable install
dependencies = [
    "soliplex ${soliplex_dev_constraint}",
]

[dependency-groups]
dev = [
    "pytest",
]

# src/ layout: the importable package is src/${package_name}/ *within this
# project directory*. The Soliplex backend puts that directory on PYTHONPATH
# as /app/src/${package_name}/src (see the stack's docker-compose.yml); the
# build + test config below points at the same layout for host-side use.
[tool.hatch.build.targets.wheel]
packages = ["src/${package_name}"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]
""",
    "src/__package__/src/__package__/tools.py.mako": '''\
"""Custom agent tools for the ``${package_name}`` Soliplex install.

A Soliplex "tool" is just a dotted name resolving to a plain callable (see a
room's ``tools:`` list). Reference :func:`greeting` from a room config as
``tool_name: "${package_name}.tools.greeting"``.
"""


def greeting(name: str) -> str:
    """Return a friendly greeting for ``name``.

    A minimal example tool: a plain, type-annotated function with a
    docstring (the LLM uses the docstring as the tool's description).
    """
    return f"Hello, {name}! This greeting came from your own package's tool."
''',
    "src/__package__/src/__package__/views.py.mako": '''\
"""A custom FastAPI router for the ``${package_name}`` Soliplex install.

Soliplex registers extra routers by dotted name via the installation-level
``app_router_operations`` (see ``backend/environment/installation.yaml``).
This module exposes ``router``, referenced there as
``${package_name}.views.router``.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/custom/ping")
def ping() -> dict[str, str]:
    """A trivial endpoint contributed by this project's own package."""
    return {"ping": "pong"}
''',
    "src/__package__/tests/unit/test_tools.py.mako": '''\
"""Tests for :mod:`${package_name}.tools`."""

from ${package_name} import tools


def test_greeting_includes_name():
    result = tools.greeting("Ada")

    assert "Ada" in result
''',
    "src/__package__/tests/unit/test_views.py.mako": '''\
"""Tests for :mod:`${package_name}.views`."""

from ${package_name} import views


def test_router_exposes_ping_route():
    paths = {route.path for route in views.router.routes}

    assert "/custom/ping" in paths


def test_ping_returns_pong():
    result = views.ping()

    assert result == {"ping": "pong"}
''',
    "backend/environment/rooms/custom/room_config.yaml.mako": """\
# A demonstration room that wires in a tool from this project's own
# '${package_name}' package, defined in
# src/${package_name}/src/${package_name}/tools.py. The dotted 'tool_name'
# below is importable because that project's package directory is on the
# backend's PYTHONPATH (see docker-compose.yml). Delete this room once you
# have your own.
id: "custom"
name: "Custom Tool Demo"
description: "Demonstrates a tool provided by this project's own package."

agent:
  template_id: "default_chat"
  system_prompt: |
    You are a helpful assistant.

    When the user asks to be greeted, call the 'greeting' tool provided by
    this project's package.

tools:
  - tool_name: "${package_name}.tools.greeting"

allow_mcp: false
""",
    # ----------------------------------------------------------------------
    # Documentation site config (Zensical). The repo's own zensical.toml is
    # excluded from the verbatim copy (see EXCLUDE_PATHSPECS); this is the
    # project's parameterized equivalent. The doc *pages* are not authored
    # here -- they are derived from docs/users/ by t_user_doc() (see above).
    # ----------------------------------------------------------------------
    "zensical.toml.mako": '''\
# Zensical configuration for this project's documentation site.
# Build with `uv run zensical build` (output under site/, gitignored) or
# preview with `uv run zensical serve`. Reference: https://zensical.org/docs/

[project]
site_name = "${project_name}"
site_description = """
A Soliplex Docker Compose stack scaffolded from soliplex-template.
"""
copyright = """
Copyright &copy; The ${project_name} authors
"""

nav = [
    { "Home" = "index.md" },
    { "Getting started" = [
        "installation.md",
    ] },
    { "Architecture" = [
        "architecture/services.md",
        "architecture/configuration.md",
        "architecture/backend.md",
    ] },
    { "Operations" = [
        "operations/secrets.md",
        "operations/rag.md",
        "operations/ingester.md",
        "operations/migrations.md",
        "operations/ollama.md",
    ] },
    { "Custom Python package" = "custom-package.md" },
]

[project.theme]
language = "en"
features = [
    "announce.dismiss",
    "content.code.annotate",
    "content.code.copy",
    "content.code.select",
    "content.tabs.link",
    "navigation.footer",
    "navigation.indexes",
    "navigation.instant",
    "navigation.instant.prefetch",
    "navigation.path",
    "navigation.sections",
    "navigation.top",
    "navigation.tracking",
    "search.highlight",
]

[[project.theme.palette]]
scheme = "default"
toggle.icon = "lucide/sun"
toggle.name = "Switch to dark mode"

[[project.theme.palette]]
scheme = "slate"
toggle.icon = "lucide/moon"
toggle.name = "Switch to light mode"

[project.markdown_extensions.abbr]
[project.markdown_extensions.admonition]
[project.markdown_extensions.attr_list]
[project.markdown_extensions.def_list]
[project.markdown_extensions.footnotes]
[project.markdown_extensions.md_in_html]
[project.markdown_extensions.toc]
permalink = true
[project.markdown_extensions.pymdownx.betterem]
[project.markdown_extensions.pymdownx.caret]
[project.markdown_extensions.pymdownx.details]
[project.markdown_extensions.pymdownx.emoji]
emoji_generator = "zensical.extensions.emoji.to_svg"
emoji_index = "zensical.extensions.emoji.twemoji"
[project.markdown_extensions.pymdownx.highlight]
anchor_linenums = true
line_spans = "__span"
pygments_lang_class = true
[project.markdown_extensions.pymdownx.inlinehilite]
[project.markdown_extensions.pymdownx.keys]
[project.markdown_extensions.pymdownx.magiclink]
[project.markdown_extensions.pymdownx.mark]
[project.markdown_extensions.pymdownx.smartsymbols]
[project.markdown_extensions.pymdownx.superfences]
[[project.markdown_extensions.pymdownx.superfences.custom_fences]]
name = "mermaid"
class = "mermaid"
format = "pymdownx.superfences.fence_code_format"
[project.markdown_extensions.pymdownx.tabbed]
alternate_style = true
combine_header_slug = true
[project.markdown_extensions.pymdownx.tasklist]
custom_checkbox = true
[project.markdown_extensions.pymdownx.tilde]
''',
    "backend/README.md.mako": """\
# Backend service

The backend image is built from `backend/Dockerfile`, and its runtime
configuration lives under `backend/environment/`.

Full documentation lives in this project's docs site under `docs/` (built with
Zensical — see the project `README.md`):

- Backend image & dependencies —
  [docs/architecture/backend.md](../docs/architecture/backend.md)
- Backend configuration —
  [docs/architecture/configuration.md](../docs/architecture/configuration.md)
""",
}

# Probe parameters for the post-refresh render check (values are arbitrary).
PROBE = dict(
    project_name="probe",
    package_name="probe_pkg",
    setup_id="probe",
    nginx_http=1,
    nginx_https=2,
    ingester_port=3,
    docling_port=4,
    postgres_port=5,
    backend_port=6,
    gitea_http_port=7,
    gitea_ssh_port=8,
    server_name="probe",
    backend_auth_flag="",
    tls_subject="probe",
    chat_model="m",
    chat_model_alt="m",
    title_model="m",
    rag_embed_model="m",
    rag_embed_dim=1,
    rag_qa_model="m",
    chunk_size=1,
    agui_db="db",
    authz_db="db2",
    soliplex_image_tag="t",
    soliplex_dev_constraint="c",
    haiku_rag_version="v",
    docling_serve_variant="cpu",
    docling_serve_version="v",
    docling_serve_image="img",
    frontend_version="t",
    docs_dir="d",
    include_gitea=True,
    include_tui=True,
    enable_sandbox=True,
    soliplex_template_manifest="[tool.soliplex-template]\n",
)


def tracked_files() -> list[str]:
    """The working tree's files, tracked or not (minus ignored ones).

    Rendering the working tree rather than the index lets a refresh pick up
    work not yet staged: new files, and deletions (which ``--cached`` would
    still list until they are staged)."""
    out = subprocess.check_output(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ".",
            *EXCLUDE_PATHSPECS,
        ],
        cwd=REPO,
        text=True,
    )
    return sorted({p for p in out.split("\0") if p and (REPO / p).is_file()})


def render_check(root: pathlib.Path) -> int:
    from mako import exceptions
    from mako import template

    bad = 0
    for mako in sorted(root.rglob("*.mako")):
        try:
            template.Template(
                filename=str(mako), strict_undefined=True
            ).render(**PROBE)
        except Exception:
            bad += 1
            print(f"render FAILED: {mako.name}", file=sys.stderr)
            print(exceptions.text_error_template().render(), file=sys.stderr)
    return bad


def _build_into(dest_root: pathlib.Path, files: list[str]) -> tuple[int, int]:
    """Assemble template into ``dest_root``.

    Returns (derived, authored, checked): the shippable-docs derived count,
    the authored-template count, and the count of tracked files that are
    themselves checked-in ``*.mako`` (the parameterized stack files).
    """
    # copy verbatim
    dest_map = {}
    for rel in files:
        src = REPO / rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)  # preserves mode (executable scripts, etc.)
        dest_map[rel] = dest

    # derive shipped docs/*.md.mako from authoritative docs/users/*.md, read
    # straight from the repo (docs/ is excluded from the verbatim copy). The
    # users/ path segment is stripped, so a project gets docs/<...>.md.mako.
    derived = 0
    docs_src = REPO / USER_DOCS_SRC
    if docs_src.is_dir():
        for md in sorted(docs_src.rglob("*.md")):
            rel = md.relative_to(docs_src)
            mako_text = t_user_doc(md.read_text())
            dest = dest_root / "docs" / rel
            dest = dest.with_name(dest.name + ".mako")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(mako_text)
            derived += 1

    # authored templates (paths may nest, e.g. src/__package__/tools.py.mako)
    for name, content in AUTHORED.items():
        dest = dest_root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    checked = sum(1 for rel in files if dest_map[rel].name.endswith(".mako"))
    return derived, len(AUTHORED), checked


def _assemble(staging: pathlib.Path, files: list[str]) -> tuple[int, int, int]:
    """Build the template into ``staging`` and render-check it."""
    derived, authored, checked = _build_into(staging, files)
    bad = render_check(staging)
    if bad:
        raise RenderFailures(bad)
    return derived, authored, checked


def main() -> int:
    require((REPO / ".git").exists(), "must run inside the repo")
    files = tracked_files()
    require(bool(files), "git ls-files returned nothing")

    # Build into a staging dir and swap into place only on success, so a
    # mid-run failure leaves the existing template intact rather than corrupt.
    staging = TEMPLATE.parent / f"{TEMPLATE.name}.new"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        derived, authored, checked = _assemble(staging, files)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    staging.rename(TEMPLATE)  # atomic on the same filesystem

    total_mako = sum(1 for _ in TEMPLATE.rglob("*.mako"))
    print(
        f"refreshed {TEMPLATE.relative_to(REPO)}: "
        f"{len(files)} copied, {checked} checked-in, "
        f"{derived} docs-derived + {authored} authored = "
        f"{total_mako} .mako templates, render-check OK"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except RefreshError as exc:
        print(f"refresh_skill_template: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
