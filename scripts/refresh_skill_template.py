#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mako"]
# ///
"""Regenerate skill/assets/template/ from the live repo exemplars.

The embedded template shipped inside the soliplex-template skill is a
parameterized copy of this repo's stack. When the repo's exemplar files change
(docker-compose.yml, installation.yaml, nginx.conf, …), run this to re-derive
the template so the generator can be exercised against current exemplars:

    uv run scripts/refresh_skill_template.py

What it does:
  1. wipes skill/assets/template/
  2. copies every tracked repo file (minus the excludes below) in verbatim
  3. rewrites the parameterized files as <name>.mako (Mako ${param} + <%text>
     escaping for literal ${...})
  4. writes the two authored templates (README.md.mako, pyproject.toml.mako)
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
TEMPLATE = REPO / "skill" / "assets" / "template"

# Tracked paths NOT copied into the template (git pathspecs, repo-relative).
EXCLUDE_PATHSPECS = [
    ":!TODO.md",
    ":!.claude",
    ":!.github",  # this repo's CI, not a project file
    ":!.pre-commit-config.yaml",  # repo pre-commit config, not a project file
    ":!skill",  # the skill source itself (no recursion)
    ":!dist",  # build artifact
    ":!tests",  # this repo's test suite, not project files
    ":!scripts/build_skill.py",  # skill-build tooling, not project files
    ":!scripts/refresh_skill_template.py",
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
    @classmethod
    def wrap(cls, rel, exc):
        return cls(f"{rel}: {exc}")

    @classmethod
    def render_failures(cls, count):
        return cls(f"{count} template(s) failed the render check")


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise RefreshError(msg)


def esc(text: str, *literals: str) -> str:
    """Wrap literal ``${...}`` runs in ``<%text>`` so Mako passes them
    through untouched."""
    for lit in literals:
        require(lit in text, f"expected literal {lit!r} to escape")
        text = text.replace(lit, f"<%text>{lit}</%text>")
    return text


def repl(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        require(old in text, f"expected to find {old!r}")
        text = text.replace(old, new)
    return text


def opt_replace(text: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` only when present (no-op otherwise).

    Used for the opt-in gitea fragments: the always-on exemplar carries them,
    but the transforms stay tolerant of minimal inputs that don't."""
    return text.replace(old, new) if old in text else text


def wrap_gitea(text: str, fragment: str) -> str:
    """Wrap an opt-in gitea ``fragment`` in a Mako ``include_gitea``
    conditional when present. The ``% if`` / ``% endif`` control lines are
    consumed by Mako, so the rendered output is byte-identical to the
    exemplar when include_gitea is true."""
    return opt_replace(
        text, fragment, f"% if include_gitea:\n{fragment}% endif\n"
    )


def repl_opt(text: str, pairs: list[tuple[str, str]]) -> str:
    """Like ``repl()`` but silently skip pairs whose ``old`` is absent
    (for the opt-in gitea fragments)."""
    for old, new in pairs:
        text = opt_replace(text, old, new)
    return text


# --------------------------------------------------------------------------
# Per-file transforms: repo-relative path -> fn(text) -> mako text
# Output path is the key + ".mako".
# --------------------------------------------------------------------------
def t_compose(text: str) -> str:
    text = esc(
        text,
        "${OLLAMA_BASE_URL}",
        "${INGESTER_TOKEN:-secret}",
        "${AUTO_CREATE_DATABASE:-1}",
        "${OPENAI_API_KEY}",
        "${ANTHROPIC_API_KEY}",
        "${VOYAGE_API_KEY}",
        "${CO_API_KEY}",
        # Container UID/GID alignment: Compose-level interpolations (read from
        # .env) for build.args, the service `user:` overrides, and the postgres
        # tmpfs ownership. They are docker syntax, not generator parameters, so
        # they must reach the rendered compose file verbatim.
        "${PUID:-1000}",
        "${PGID:-1000}",
    )
    text = repl(
        text,
        [
            ("name: soliplex-template", "name: ${project_name}"),
            ('- "9000:9000"', '- "${nginx_http}:9000"'),
            ('- "9443:9443"', '- "${nginx_https}:9443"'),
            (
                "--public-url https://soliplex.localhost:9443/tui",
                "--public-url https://${server_name}:${nginx_https}/tui",
            ),
            (
                "soliplex-cli serve --no-auth-mode --reload=config",
                "soliplex-cli serve ${backend_auth_flag}--reload=config",
            ),
            ('- "8765:8765"', '- "${ingester_port}:8765"'),
            ('- "5001:5001"', '- "${docling_port}:5001"'),
            ('- "5432:5432"', '- "${postgres_port}:5432"'),
            ("- ./rag/docs:/docs", "- ./${docs_dir}:/docs"),
            # Put this project's own src/ package on the backend's import
            # path (read-only bind mount + PYTHONPATH) so Soliplex can resolve
            # dotted names like '${package_name}.tools.greeting'. The anchors
            # below are unique to the backend service (the ingester uses the
            # short './rag/db:/data' volume form and a different environment).
            (
                "      - type: bind\n"
                '        source: "rag/db/"\n'
                '        target: "/db"\n',
                "      - type: bind\n"
                '        source: "rag/db/"\n'
                '        target: "/db"\n'
                "\n"
                "      - type: bind\n"
                '        source: "./src"\n'
                '        target: "/app/src"\n'
                "        read_only: true\n",
            ),
            (
                "      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>\n"
                "\n"
                "    volumes:\n",
                "      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>\n"
                "      # Put this project's own src/ package on the\n"
                "      # backend's import path so Soliplex can resolve\n"
                "      # dotted tool / router names (see './src' below).\n"
                "      PYTHONPATH: /app/src\n"
                "\n"
                "    volumes:\n",
            ),
        ],
    )
    # Wrap the opt-in gitea fragments in Mako conditionals so generated
    # projects include them only when include_gitea is true. Tolerant of
    # absence (gitea is optional); the always-on exemplar carries them all.
    # Escape the gitea ROOT_URL default-expansion (docker syntax, not a
    # generator parameter) when present:
    text = opt_replace(
        text,
        "${GITEA_ROOT_URL:-https://localhost:9443/gitea/}",
        "<%text>${GITEA_ROOT_URL:-https://localhost:9443/gitea/}</%text>",
    )
    # nginx depends_on the gitea service:
    text = opt_replace(
        text,
        "      - gitea\n",
        "% if include_gitea:\n      - gitea\n% endif\n",
    )
    # postgres: gitea DB password file + secret source:
    text = wrap_gitea(
        text, "      GITEA_DB_PASS_FILE: /run/secrets/gitea_db_password\n"
    )
    text = wrap_gitea(text, "      - source: gitea_db_password\n")
    # the gitea service block (open + close anchors):
    text = opt_replace(text, "  gitea:\n", "% if include_gitea:\n  gitea:\n")
    text = opt_replace(
        text,
        "    restart: unless-stopped\n\nvolumes:\n",
        "    restart: unless-stopped\n\n% endif\nvolumes:\n",
    )
    # the gitea named volumes:
    text = wrap_gitea(text, "  gitea_data:\n  gitea_config:\n")
    # the gitea db_password secret-file entry:
    text = wrap_gitea(
        text,
        "    gitea_db_password:\n"
        "      file: ./.secrets/gitea_db_password.gen\n",
    )
    return text


_ROUTER_BLOCK = (
    'haiku_rag_config_file: "./haiku.rag.yaml"\n'
    "\n"
    "#" + "=" * 74 + "\n"
    "# FastAPI routers (custom)\n"
    "#" + "=" * 74 + "\n"
    "# Add this project's own router (defined in src/${package_name}/"
    "views.py)\n"
    "# by dotted name, without clearing the default Soliplex routers.\n"
    "#" + "=" * 74 + "\n"
    "app_router_operations:\n"
    '  - kind: "add"\n'
    '    group_name: "${package_name}"\n'
    '    router_name: "${package_name}.views.router"\n'
    '    prefix: "/api"\n'
)


def t_installation(text: str) -> str:
    return repl(
        text,
        [
            ('id: "soliplex-conf-minimal"', 'id: "${setup_id}"'),
            (
                '  - id: "default_chat"\n    model_name: "gpt-oss:latest"',
                '  - id: "default_chat"\n    model_name: "${chat_model}"',
            ),
            (
                '  - id: "title"\n    model_name: "gpt-oss:latest"',
                '  - id: "title"\n    model_name: "${title_model}"',
            ),
            ('"gpt-oss:20b"', '"${chat_model_alt}"'),
            ("soliplex_agui", "${agui_db}"),
            ("soliplex_authz", "${authz_db}"),
            # The hypothetical 'my_package' meta-config examples become a
            # dotted reference into this project's own package, so the
            # commented examples point somewhere real once uncommented.
            ("my_package", "${package_name}"),
            # Load the demonstration room that wires in the custom tool.
            (
                '  - "./rooms/bwrap_sandbox"',
                '  - "./rooms/bwrap_sandbox"\n  - "./rooms/custom"',
            ),
            # Register this project's FastAPI router by dotted name.
            ('haiku_rag_config_file: "./haiku.rag.yaml"', _ROUTER_BLOCK),
        ],
    )


def t_backend_haiku(text: str) -> str:
    return repl(
        text,
        [
            ("name: qwen3-embedding:4b", "name: ${rag_embed_model}"),
            ("vector_dim: 2560", "vector_dim: ${rag_embed_dim}"),
            (
                "qa:\n  model:\n    name: gpt-oss:latest",
                "qa:\n  model:\n    name: ${rag_qa_model}",
            ),
            (
                "research:\n  model:\n    name: gpt-oss:latest",
                "research:\n  model:\n    name: ${rag_research_model}",
            ),
            ("chunk_size: 256", "chunk_size: ${chunk_size}"),
        ],
    )


def t_ingester_haiku(text: str) -> str:
    return repl(text, [("chunk_size: 256", "chunk_size: ${chunk_size}")])


def t_backend_constraints(text: str) -> str:
    new, n = re.subn(
        r"(?m)^soliplex .*$", "soliplex ${soliplex_backend_constraint}", text
    )
    require(
        n == 1,
        f"expected one 'soliplex ...' line in backend constraints, got {n}",
    )
    return new


def t_tui_constraints(text: str) -> str:
    new, n = re.subn(
        r"(?m)^soliplex .*$", "soliplex ${soliplex_tui_constraint}", text
    )
    require(
        n == 1, f"expected one 'soliplex ...' line in tui constraints, got {n}"
    )
    return new


def t_nginx_conf(text: str) -> str:
    require(
        text.count("server_name localhost;") == 2,
        "expected two 'server_name localhost;' in nginx.conf",
    )
    text = text.replace(
        "server_name localhost;", "server_name ${server_name};"
    )
    # Wrap the opt-in /gitea reverse-proxy location (HTTPS server) in a Mako
    # conditional so it is emitted only when include_gitea is true.
    gitea_loc = (
        "        # Gitea under /gitea/.  Only mounted on the HTTPS server"
        " -- Gitea's\n"
        "        # ROOT_URL points at https://.../gitea/, so browsers"
        " hitting\n"
        "        # http://.../gitea would get mixed absolute links.\n"
        "        # '^~' makes this prefix trump the regex locations above."
        "  Gitea\n"
        "        # itself does not know the sub-path; we strip /gitea before"
        " proxying\n"
        "        # and let ROOT_URL prepend it on Gitea's generated links."
        "  See:\n"
        "        # https://docs.gitea.com/administration/reverse-proxies\n"
        "        location ^~ /gitea {\n"
        "            client_max_body_size 512M;\n"
        "\n"
        "            # Preserve encoded chars (e.g. %2F in branch names)"
        " through the\n"
        "            # rewrite; two-step trick from the Gitea docs.\n"
        "            rewrite ^ $request_uri;\n"
        "            rewrite ^/gitea/?(.*) /$1 break;\n"
        "\n"
        '            set $backend_gitea "gitea";\n'
        "            proxy_pass http://$backend_gitea:3000$uri;\n"
        "            proxy_http_version 1.1;\n"
        "            proxy_set_header Connection $http_connection;\n"
        "            proxy_set_header Upgrade $http_upgrade;\n"
        "            proxy_set_header Host $host$host_port;\n"
        "            proxy_set_header X-Real-IP $remote_addr;\n"
        "            proxy_set_header X-Forwarded-For"
        " $proxy_add_x_forwarded_for;\n"
        "            proxy_set_header X-Forwarded-Port $backend_port;\n"
        "            proxy_set_header X-Forwarded-Proto https;\n"
        "            proxy_connect_timeout 10;\n"
        "            proxy_send_timeout 60;\n"
        "            proxy_read_timeout 600;\n"
        "            proxy_buffering off;\n"
        "        }\n"
    )
    return wrap_gitea(text, gitea_loc)


def t_nginx_dockerfile(text: str) -> str:
    # Wrap everything literal in <%text> (preserves backslash-newlines and the
    # builder's shell ${...} expansions); break out only for the two
    # parameters: the GitHub releases API path (frontend version selection)
    # and the self-signed cert subject.
    m = re.search(r'-subj "([^"]*)"', text)
    require(m is not None, 'expected a -subj "..." line in nginx/Dockerfile')
    subj = m.group(1)
    require(text.count(subj) == 1, f"cert subject {subj!r} is not unique")
    require(
        text.count("releases/latest") == 1,
        "expected one 'releases/latest' frontend API URL in nginx/Dockerfile",
    )
    # Move any trailing newline outside the final <%text> block so the
    # generated .mako file ends with a newline (keeps end-of-file-fixer happy
    # and refresh idempotent) while the rendered Dockerfile stays identical
    # for the default (frontend_release_path="latest").
    stripped = text.rstrip("\n")
    trailing = text[len(stripped) :]
    body = "<%text>" + stripped + "</%text>"
    body = body.replace(
        "releases/latest",
        "releases/</%text>${frontend_release_path}<%text>",
    )
    body = body.replace(subj, "</%text>${tls_subject}<%text>")
    return body + trailing


def t_init_sh(text: str) -> str:
    text = repl(
        text,
        [
            ("soliplex_agui", "${agui_db}"),
            ("soliplex_authz", "${authz_db}"),
        ],
    )
    # Wrap the opt-in gitea fragments in Mako conditionals (see t_compose).
    # The gitea DB name is fixed (not parameterized), so these anchors are
    # untouched by the agui/authz substitutions above. Tolerant of absence.
    return repl_opt(
        text,
        [
            (
                "# Read password from secret file if available, otherwise"
                " fallback to environment variable\n"
                'if [ -f "$GITEA_DB_PASS_FILE" ]; then\n'
                '    GITEA_DB_PASS=$(cat "$GITEA_DB_PASS_FILE")\n'
                'elif [ -z "$GITEA_DB_PASS" ]; then\n'
                '    echo "ERROR: Neither GITEA_DB_PASS_FILE nor'
                ' GITEA_DB_PASS is set"\n'
                "    exit 1\n"
                "fi\n",
                "% if include_gitea:\n"
                "# Read password from secret file if available, otherwise"
                " fallback to environment variable\n"
                'if [ -f "$GITEA_DB_PASS_FILE" ]; then\n'
                '    GITEA_DB_PASS=$(cat "$GITEA_DB_PASS_FILE")\n'
                'elif [ -z "$GITEA_DB_PASS" ]; then\n'
                '    echo "ERROR: Neither GITEA_DB_PASS_FILE nor'
                ' GITEA_DB_PASS is set"\n'
                "    exit 1\n"
                "fi\n"
                "% endif\n",
            ),
            (
                "    -- Create Gitea application user with password\n"
                "    CREATE USER soliplex_gitea WITH PASSWORD"
                " '$GITEA_DB_PASS';\n",
                "% if include_gitea:\n"
                "    -- Create Gitea application user with password\n"
                "    CREATE USER soliplex_gitea WITH PASSWORD"
                " '$GITEA_DB_PASS';\n"
                "% endif\n",
            ),
            (
                "    -- Create database owned by postgres"
                " (not application user)\n"
                "    CREATE DATABASE soliplex_gitea;\n"
                "    ALTER DATABASE soliplex_gitea OWNER TO postgres;\n",
                "% if include_gitea:\n"
                "    -- Create database owned by postgres"
                " (not application user)\n"
                "    CREATE DATABASE soliplex_gitea;\n"
                "    ALTER DATABASE soliplex_gitea OWNER TO postgres;\n"
                "% endif\n",
            ),
            (
                "    -- Connect to the soliplex_gitea database to set up"
                " schema permissions\n"
                "    \\c soliplex_gitea\n"
                "\n"
                "    -- Grant minimal required PRIVILEGES"
                " (EVAL.md #14 recommendation)\n"
                "    -- Only CONNECT, not superuser or database ownership\n"
                "    GRANT CONNECT ON DATABASE soliplex_gitea TO"
                " soliplex_gitea;\n"
                "\n"
                "    -- Schema-level permissions\n"
                "    GRANT USAGE ON SCHEMA public TO soliplex_gitea;\n"
                "\n"
                "    GRANT ALL PRIVILEGES ON DATABASE soliplex_gitea to"
                " soliplex_gitea;\n"
                "    GRANT ALL PRIVILEGES ON SCHEMA public TO"
                " soliplex_gitea;\n",
                "% if include_gitea:\n"
                "    -- Connect to the soliplex_gitea database to set up"
                " schema permissions\n"
                "    \\c soliplex_gitea\n"
                "\n"
                "    -- Grant minimal required PRIVILEGES"
                " (EVAL.md #14 recommendation)\n"
                "    -- Only CONNECT, not superuser or database ownership\n"
                "    GRANT CONNECT ON DATABASE soliplex_gitea TO"
                " soliplex_gitea;\n"
                "\n"
                "    -- Schema-level permissions\n"
                "    GRANT USAGE ON SCHEMA public TO soliplex_gitea;\n"
                "\n"
                "    GRANT ALL PRIVILEGES ON DATABASE soliplex_gitea to"
                " soliplex_gitea;\n"
                "    GRANT ALL PRIVILEGES ON SCHEMA public TO"
                " soliplex_gitea;\n"
                "% endif\n",
            ),
            (
                "echo \"Database 'soliplex_gitea' initialized with minimal"
                " privileges for user 'soliplex_gitea'\"\n",
                "% if include_gitea:\n"
                "echo \"Database 'soliplex_gitea' initialized with minimal"
                " privileges for user 'soliplex_gitea'\"\n"
                "% endif\n",
            ),
        ],
    )


def t_gitignore(text: str) -> str:
    # The repo ignores its own skill build artifacts under /dist/; a generated
    # project has no such artifact, so strip that block (a '# Skill build
    # artifacts' comment, any further comment lines, and the /dist/ entry).
    new, n = re.subn(
        r"\n# Skill build artifacts[^\n]*\n(?:#[^\n]*\n)*/dist/\n\n?",
        "\n",
        text,
        count=1,
    )
    require(
        n == 1,
        "expected the '/dist/' skill build artifacts block in .gitignore",
    )
    return new


# Files transformed but kept under their original name (NOT Mako templates).
VERBATIM_EDITS = {
    ".gitignore": t_gitignore,
}

DERIVED = {
    "docker-compose.yml": t_compose,
    "backend/environment/installation.yaml": t_installation,
    "backend/environment/haiku.rag.yaml": t_backend_haiku,
    "haiku.rag/haiku.rag.yaml": t_ingester_haiku,
    "backend/constraints.txt": t_backend_constraints,
    "tui/constraints.txt": t_tui_constraints,
    "nginx/nginx.conf": t_nginx_conf,
    "nginx/Dockerfile": t_nginx_dockerfile,
    "postgres/config/init.sh": t_init_sh,
}

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
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
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
| haiku-ingester   | ${ingester_port} |
| docling-serve    | ${docling_port} |
| postgres         | ${postgres_port} |

Open the app at <http://localhost:${nginx_http}/> (or
<https://${server_name}:${nginx_https}/> for TLS).

<%text>## Custom Python package</%text>

This project is also an installable Python library: your own code lives under
`src/${package_name}/` (a demo tool and FastAPI router ship wired up) and its
tests under `tests/unit/`.

```bash
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
```

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
    "pyproject.toml.mako": """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# Host-side dependencies for this project's own tooling and the custom code
# under src/${package_name}/ (e.g. running `soliplex-cli` against the Postgres
# backing store, or this project's tests). The container images install their
# own pinned deps; this file is for host-side use:
#   uv sync                 # create/refresh the dev environment
#   uv pip install -e .     # or a plain editable install
dependencies = [
    "soliplex ${soliplex_backend_constraint}",
    "psycopg[binary]",
    "asyncpg",
]

[dependency-groups]
dev = [
    "pytest",
    # Builds the documentation site under docs/ (`uv run zensical build`).
    "zensical",
]

# src/ layout: the importable package lives at src/${package_name}/. The
# Soliplex backend puts src/ on PYTHONPATH (see docker-compose.yml); the
# build + test config below points at the same layout for host-side use.
[tool.hatch.build.targets.wheel]
packages = ["src/${package_name}"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]
""",
    "src/__package__/tools.py.mako": '''\
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
    "src/__package__/views.py.mako": '''\
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
    "tests/unit/test_tools.py.mako": '''\
"""Tests for :mod:`${package_name}.tools`."""

from ${package_name} import tools


def test_greeting_includes_name():
    result = tools.greeting("Ada")

    assert "Ada" in result
''',
    "tests/unit/test_views.py.mako": '''\
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
# '${package_name}' package (defined in src/${package_name}/tools.py). The
# dotted 'tool_name' below is importable because src/ is on the backend's
# PYTHONPATH (see docker-compose.yml). Delete this room once you have your own.
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
    # Documentation site (Zensical). Markdown headings (## ...) are wrapped in
    # <%text> so Mako does not treat them as comments; ${param} substitutions
    # stay outside the wrappers. The repo's own docs/ + zensical.toml are
    # excluded from the verbatim copy (see EXCLUDE_PATHSPECS) -- these are the
    # project's parameterized equivalents.
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
        "getting-started/installation.md",
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
    "docs/index.md.mako": """\
---
icon: lucide/rocket
---

# ${project_name}

A Soliplex Docker Compose stack — nginx + Soliplex backend + Flutter frontend +
haiku-ingester + Postgres, plus docling-serve and a TUI — scaffolded from the
`soliplex-template` generator.

This site documents *this* project. The quickest path to a running stack is the
`README.md` at the project root; for the full walkthrough see
[Installation](getting-started/installation.md).

<%text>## Services</%text>

- **nginx** — serves the Flutter web frontend and reverse-proxies the API.
- **backend** — the Soliplex server (`soliplex-cli serve`).
- **haiku-ingester** — the document-pipeline writer for the RAG vector store.
- **docling-serve** — stateless document converter.
- **postgres** — thread persistence and authorization policy.

<%text>## Exposed ports</%text>

| Service | Host port |
|---------|-----------|
| nginx (HTTP) | ${nginx_http} |
| nginx (HTTPS) | ${nginx_https} |
| haiku-ingester | ${ingester_port} |
| docling-serve | ${docling_port} |
| postgres | ${postgres_port} |

<%text>## Documentation map</%text>

- **[Installation](getting-started/installation.md)** — generate secrets,
  confirm `OLLAMA_BASE_URL`, bring the stack up.
- **[Service graph](architecture/services.md)** — what each container does and
  how they connect.
- **[Backend configuration](architecture/configuration.md)** — the
  `backend/environment/` layout and the sandbox.
- **[Backend image & dependencies](architecture/backend.md)** — building the
  backend image and adding dependencies.
- **[Secrets](operations/secrets.md)** — file-based vs env-var secret modes.
- **[RAG pipeline](operations/rag.md)** — the vector store, the ingester, and
  adding documents.
- **[Ingester control plane](operations/ingester.md)** — the control-plane API
  and its auth token.
- **[Custom Python package](custom-package.md)** — the installable
  `src/${package_name}` library.
""",
    "docs/getting-started/installation.md.mako": """\
---
icon: lucide/download
---

# Installation

This project was scaffolded by the `soliplex-template` generator, so most setup
is already done. Bringing the stack up takes three steps.

<%text>## Prerequisites</%text>

- **Docker** with the **Compose** plugin (`docker compose version`).
- An **Ollama** server reachable from the containers, serving the models named
  in `backend/environment/installation.yaml`. Its URL is recorded in `.env` as
  `OLLAMA_BASE_URL` (see step 2).

<%text>## 1. Generate secrets</%text>

The Postgres roles and the backend read their credentials from Docker secrets.
Generate them before the first `up`:

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Deleting these files after the Postgres volume
    already exists breaks the backend's auth to the database. See
    [Secrets](../operations/secrets.md).

The generated `.env` records `PUID` / `PGID` — the uid/gid the containers run
as and that owns these secret files (defaulted to the operator who scaffolded
the project). If you run services as a different account, set them explicitly
and rebuild; see [Secrets](../operations/secrets.md).

<%text>## 2. Confirm `OLLAMA_BASE_URL`</%text>

The generator wrote `.env` with the `OLLAMA_BASE_URL` you supplied. Confirm it
points at your Ollama server and adjust if needed. You can also set
`INGESTER_TOKEN` there — see
[Ingester control plane](../operations/ingester.md).

<%text>## 3. Bring the stack up</%text>

```bash
docker compose up        # foreground
docker compose up -d     # detached
```

The first run builds the `nginx` and `backend` images and initializes Postgres;
it takes a few minutes. Subsequent runs are fast.

<%text>## Exposed ports</%text>

| Port | Service | Purpose |
|------|---------|---------|
| `${nginx_http}` | nginx | HTTP — the web frontend |
| `${nginx_https}` | nginx | HTTPS (self-signed cert) |
| `8000` | backend | Soliplex backend, direct |
| `${ingester_port}` | haiku-ingester | Control plane + dashboard |
| `${docling_port}` | docling-serve | Document converter |
| `${postgres_port}` | postgres | Database |

(Container-internal ports are fixed; these are the host-published sides.)

<%text>## Verify the stack</%text>

```bash
docker compose ps
curl -fsS http://localhost:${ingester_port}/health
docker compose logs -f backend
```

Then open <http://localhost:${nginx_http}> for the web frontend.

<%text>## Everyday commands</%text>

```bash
docker compose build <service>   # rebuild one image (backend, nginx, …)
docker compose down              # stop (keeps the postgres_data volume)
docker compose down -v           # stop AND wipe the postgres volume
```

!!! danger "`down -v` is destructive"
    `docker compose down -v` drops the `postgres_data` volume — all chat
    threads and authorization grants go with it. The ingester's SQLite job
    queue lives under `rag/db/` (a bind mount), so it survives a `down -v`.
""",
    "docs/architecture/services.md.mako": """\
---
icon: lucide/network
---

# Service graph

The stack is defined in `docker-compose.yml`. Five services cooperate; two of
them share the RAG vector store through a bind mount.

```mermaid
graph LR
  browser([Browser]) -->|${nginx_http}/${nginx_https}| nginx
  nginx -->|/api/ /mcp/| backend
  backend -->|reads LanceDB| ragdb[(rag/db)]
  ingester[haiku-ingester] -->|writes LanceDB| ragdb
  ingester -->|convert/chunk| docling[docling-serve]
  backend -->|threads + authz| postgres[(postgres)]
```

<%text>## nginx</%text>

Serves the Flutter web frontend (built from a `soliplex/frontend` release in
`nginx/Dockerfile`) and reverse-proxies `/api/` and `/mcp/` to `backend:8000`.
Terminates TLS on host port ${nginx_https} with a self-signed cert.

<%text>## backend</%text>

Runs `soliplex-cli serve /environment`, installed from the pinned `soliplex`
package (see [Backend image & dependencies](backend.md)). The `--reload=config`
flag means edits under `backend/environment/` take effect without a rebuild.

<%text>## haiku-ingester</%text>

The **writer** for the LanceDB at `rag/db/`. Runs `haiku-ingester serve` with a
persistent SQLite job queue, an async worker pool, retries + a dead-letter
queue, and an HTTP control plane on host port ${ingester_port}. There is a
**single-writer constraint**: only one ingester per LanceDB. The backend reads
the same store through a bind mount. See [RAG pipeline](../operations/rag.md).

<%text>## docling-serve</%text>

A stateless document converter (CPU image by default; a GPU variant is a
commented swap in `docker-compose.yml`).

<%text>## postgres</%text>

Creates two databases on first boot via `postgres/config/init.sh`:
`${agui_db}` (thread persistence) and `${authz_db}` (authorization policy).
Each gets a dedicated low-privilege role whose password is a Docker secret.
Init runs **only on an empty data volume**; to re-run it,
`docker compose down -v` first.
""",
    "docs/architecture/configuration.md.mako": """\
---
icon: lucide/settings
---

# Backend configuration

The backend's behavior is driven by the files under `backend/environment/`,
bind-mounted into the container at `/environment`. Because the backend runs
with `--reload=config`, edits here take effect without a rebuild.

<%text>## Layout</%text>

- `installation.yaml` — the top-level install config: agents, secrets, env
  vars, room list, skills, DB URIs, paths. **Start here.**
- `rooms/<name>/room_config.yaml` — per-room agent prompts, tools, and skills.
- `skills/<name>/` — filesystem skills discovered via
  `filesystem_skills_paths`.
- `logging.yaml`, `haiku.rag.yaml` — logging and RAG configuration.

`installation.yaml` is heavily commented with pointers to the
[Soliplex config docs](https://soliplex.github.io/soliplex/config/). Those
comments describe **defaults** — so a section being empty or absent is not the
same as being unconfigured.

!!! note "Two sources of truth to remember"
    - The `room_paths` list in `installation.yaml` determines which rooms load.
      Adding a directory under `rooms/` without listing it there does nothing.
    - A filesystem skill must be both present under a `filesystem_skills_paths`
      directory **and** declared in `skill_configs` to be enabled.

<%text>## Sandbox (code execution for agents)</%text>

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a
  `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build
  time, so adding or changing one requires `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents
  at runtime (gitignored).

Dependencies needed by agent-executed sandbox code belong in the relevant
sandbox environment, **not** in the backend image's top-level dependency list
(see [Backend image & dependencies](backend.md)).
""",
    "docs/architecture/backend.md.mako": """\
---
icon: lucide/box
---

# Backend image & dependencies

The `backend` service image is built from `backend/Dockerfile`. It uses
[`uv`](https://docs.astral.sh/uv/) to install the pinned `soliplex` release and
a handful of runtime dependencies into `/app/.venv`, subject to the pins in
`backend/constraints.txt`. Because dependencies are baked in at build time,
adding or changing one requires a rebuild:

```bash
docker compose build backend
docker compose up -d backend
```

<%text>## Adding a third-party dependency (from PyPI)</%text>

1. Append the distribution name(s) to the `uv add` invocation in
   `backend/Dockerfile`.
2. (Recommended) pin the version in `backend/constraints.txt` so builds are
   reproducible:

    ```text
    soliplex ${soliplex_backend_constraint}
    httpx >= 0.28, < 0.29
    ```

3. Rebuild and restart the service (see above).

<%text>## Adding a local Python dependency</%text>

"Local" means a package whose source lives on your machine. The build context
of the `backend` image is the `backend/` directory, so anything the Dockerfile
`COPY`s must live **inside** `backend/`. Place the source under
`backend/vendor/<pkgname>/` and add it as a path dependency in the `uv add`
step (`uv add /vendor/<pkgname>`, or `--editable` for editable installs).

!!! tip "Your own code does not need this"
    For code that belongs to *this* project, use the bundled `src/` package
    instead — it is bind-mounted onto the backend's `PYTHONPATH` with no
    rebuild. See [Custom Python package](../custom-package.md).

<%text>## Gotchas</%text>

- The build cache is keyed on `constraints.txt`, `Dockerfile`, and
  `sandbox/environments/*/pyproject.toml`. Editing `constraints.txt` or the
  `uv add` line forces a dependency re-resolve.
- Sandbox environments under `backend/sandbox/environments/<name>/` are
  **separate** `uv` projects; their dependencies belong there, not in the
  top-level `uv add` list.
- `constraints.txt` only affects PyPI resolution; local path dependencies are
  not version-constrained by it.
""",
    "docs/operations/secrets.md.mako": """\
---
icon: lucide/key-round
---

# Secrets

The Postgres roles and the Soliplex backend read their credentials from
secrets. There are two modes, both documented at the bottom of
`docker-compose.yml`.

<%text>## File-based (active by default)</%text>

`scripts/generate-secrets.sh` writes `.secrets/*.gen` files, which Compose
mounts as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored, so the
initial commit never captures secrets.

```bash
./scripts/generate-secrets.sh
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Destroying these files after the Postgres volume
    already exists breaks the backend's auth to the database — you must also
    `docker compose down -v` and re-init.

<%text>## File ownership (`PUID` / `PGID`)</%text>

The secret files are mode `0600` (owner-only). For an in-container service
(e.g. Postgres) to read one, the file's **owner must match the uid the
container runs as**. The stack ties both ends to `PUID` / `PGID` in `.env`:

- every built image runs as `PUID:PGID` (Compose `build.args`), and
- `scripts/generate-secrets.sh` ensures the `.secrets/*.gen` files end up owned
  by `PUID:PGID`.

The generator defaults `PUID` / `PGID` to the host operator who scaffolded the
project, so on a single-developer machine this is automatic. On a deploy host
whose login uid differs from the runtime service account, set `PUID` / `PGID`
explicitly (and rebuild — see below); `generate-secrets.sh` then re-owns the
secret files to that uid/gid via a throwaway container (it needs Docker for
that step).

!!! warning "Changing `PUID` / `PGID` needs a rebuild"
    The uid is baked into the images at build time, so after editing `PUID` /
    `PGID` in `.env` you must `docker compose build` (and re-run
    `./scripts/generate-secrets.sh` so the secret files are re-owned to match).

!!! note "Override uid: who owns the secret files"
    When `PUID` differs from your login uid, the `.secrets/*.gen` files are
    owned by `PUID`, so reading or deleting them from the host needs that uid
    (or `sudo`). This is expected — it is what lets the container read them at
    mode `0600`.

<%text>## Env-var (commented alternative)</%text>

Uncomment the env-var secret blocks in `docker-compose.yml` and set the
corresponding `SOLIPLEX_*` environment variables instead of using files.

<%text>## The `down -v` caveat</%text>

`docker compose down -v` drops the `postgres_data` volume — all chat threads
and authorization grants go with it. The ingester's SQLite job queue lives
separately under `rag/db/` (a bind mount), so a `down -v` does not touch it.
""",
    "docs/operations/rag.md.mako": """\
---
icon: lucide/database
---

# RAG pipeline

`rag/db/` is the single source of truth for vector data. It is mounted by
**two** services:

- **haiku-ingester** is the writer (`/data`); it also owns the persistent
  SQLite job queue.
- **backend** reads it via the `haiku.rag.skills.rag` skill.

Only one ingester may write a given LanceDB (the single-writer constraint).

<%text>## Adding documents</%text>

Drop files into `${docs_dir}/`. The filesystem source in
`haiku.rag/haiku.rag.yaml` picks them up on its next poll, and docling-serve
converts and chunks them.

<%text>## Adding other sources</%text>

To pull from S3, HTTP, WebDAV, and the like, append entries under
`ingester.sources` in `haiku.rag/haiku.rag.yaml` and restart the ingester:

```bash
docker compose restart haiku-ingester
```

<%text>## A separate, static database</%text>

The ingester only maintains **one** LanceDB. For a *separate*, mostly-static
corpus, build a standalone database with the `haiku-rag` CLI via a one-off
`docker compose run` on the `haiku-ingester` service — it already mounts
`rag/db`, carries `OLLAMA_BASE_URL`, and ships the same `haiku.rag.yaml`. Point
it at a **different** `--db` so it never collides with `haiku.rag.lancedb`:

```bash
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb init
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb \
    add-src /docs/handbook/
```

`add-src` also takes a URL or `s3://` URI; for a local path outside
`${docs_dir}/`, add `-v /abs/path:/src:ro` and point `add-src` at `/src`. Use
`rebuild` to re-index and `delete <id>` to remove a document. The database
lands at `rag/db/handbook.lancedb`, which the backend already reads through its
`rag/db` mount. Wire a room to it with `rag_lancedb_stem: "handbook"` in that
room's `room_config.yaml`.

<%text>## Operating the pipeline</%text>

The ingester exposes a control plane on host port ${ingester_port} — `/health`,
`/jobs`, `/sources`, `/dlq`, `/stats`, and a dashboard at `/`. It requires a
bearer token; see [Ingester control plane](ingester.md).
""",
    "docs/operations/ingester.md.mako": """\
---
icon: lucide/shield
---

# Ingester control plane

`haiku-ingester` binds its control plane on `0.0.0.0:8765` so the host port
mapping works. With a non-loopback bind, haiku.rag **requires a bearer token**
(`ingester.api.auth_token`); otherwise anyone who can reach the port could
cancel jobs, retry from the dead-letter queue, and trigger source refreshes.

<%text>## How the token gets in</%text>

1. `haiku.rag/haiku.rag.yaml` ships
   `ingester.api.auth_token: __INGESTER_TOKEN__` as a placeholder.
2. The `haiku-ingester` service runs a small `sh -c "sed ... && exec ..."`
   wrapper that replaces the placeholder with the value of `$INGESTER_TOKEN`
   before haiku-ingester reads the config.
3. `INGESTER_TOKEN` defaults to `secret` — Compose sets
   `<%text>${INGESTER_TOKEN:-secret}</%text>`. Override it in `.env`
   for anything that isn't a single-developer laptop.

<%text>## Calling the API</%text>

Clients send `Authorization: Bearer $INGESTER_TOKEN`:

```bash
curl -fsS -H "Authorization: Bearer $INGESTER_TOKEN" \\
  http://localhost:${ingester_port}/stats
```

The browser dashboard at `/` is unauthenticated HTML; its in-page JavaScript
attaches the bearer to its JSON fetches itself.

!!! warning "Watch the startup log"
    The startup log warns if `auth_token` is `None`. If you see that
    warning, the substitution didn't fire and the API is **open**.

<%text>## Token character restrictions</%text>

The token cannot contain `|`, `\\`, or `&` — they are the `sed` delimiter and
escape characters used by the substitution wrapper. Use alphanumerics, e.g.:

```bash
openssl rand -hex 32
```
""",
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
    "docs/custom-package.md.mako": """\
---
icon: lucide/package
---

# Custom Python package

This project is an installable Python library: your own code lives under
`src/${package_name}/` and its tests under `tests/unit/`.

```bash
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
uv pip install -e .     # or a plain editable install into another environment
```

<%text>## How it reaches the backend</%text>

The Soliplex backend bind-mounts `./src` (read-only) into its container and
puts it on `PYTHONPATH` (see `docker-compose.yml`), so anything you define
here is importable by **dotted name** from the Soliplex config under
`backend/environment/` — no image rebuild needed to edit your code.

<%text>## What ships wired up</%text>

Two examples are referenced from the config so you can see the pattern:

- a tool, `${package_name}.tools.greeting`, referenced from
  `backend/environment/rooms/custom/room_config.yaml`;
- a FastAPI router, `${package_name}.views.router`, registered via
  `app_router_operations` in `backend/environment/installation.yaml`.

Dotted names into this package can equally be used in the `installation.yaml`
`meta:` section (tool/agent/skill config classes, MCP wrappers, secret sources)
— see the commented `${package_name}.*` examples there.

<%text>## Making it your own</%text>

Add modules under `src/${package_name}/`, reference them by dotted name from
the config, and add tests under `tests/unit/`. Delete the demonstration
`custom` room once you have your own.
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
    server_name="probe",
    backend_auth_flag="",
    tls_subject="probe",
    chat_model="m",
    chat_model_alt="m",
    title_model="m",
    rag_embed_model="m",
    rag_embed_dim=1,
    rag_qa_model="m",
    rag_research_model="m",
    chunk_size=1,
    agui_db="db",
    authz_db="db2",
    soliplex_backend_constraint="c",
    soliplex_tui_constraint="c",
    frontend_release_path="latest",
    docs_dir="d",
    include_gitea=True,
)


def tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z", "--", ".", *EXCLUDE_PATHSPECS],
        cwd=REPO,
        text=True,
    )
    return [p for p in out.split("\0") if p]


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
    """Assemble template into ``dest_root``; return (derived, authored)."""
    # copy verbatim
    for rel in files:
        src = REPO / rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)  # preserves mode (executable scripts, etc.)

    # in-place edits to files kept under their original name
    for rel, fn in VERBATIM_EDITS.items():
        copied = dest_root / rel
        require(
            copied.is_file(),
            f"verbatim-edit source {rel} not found (committed?)",
        )
        try:
            copied.write_text(fn(copied.read_text()))
        except RefreshError as exc:
            raise RefreshError.wrap(rel, exc) from exc

    # rewrite parameterized files as .mako
    derived = 0
    for rel, fn in DERIVED.items():
        copied = dest_root / rel
        require(
            copied.is_file(),
            f"derived source {rel} not found among tracked files (committed?)",
        )
        try:
            mako_text = fn(copied.read_text())
        except RefreshError as exc:
            raise RefreshError.wrap(rel, exc) from exc
        copied.with_name(copied.name + ".mako").write_text(mako_text)
        copied.unlink()
        derived += 1

    # authored templates (paths may nest, e.g. src/__package__/tools.py.mako)
    for name, content in AUTHORED.items():
        dest = dest_root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    return derived, len(AUTHORED)


def _assemble(staging: pathlib.Path, files: list[str]) -> tuple[int, int]:
    """Build the template into ``staging`` and render-check it."""
    derived, authored = _build_into(staging, files)
    bad = render_check(staging)
    if bad:
        raise RefreshError.render_failures(bad)
    return derived, authored


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
        derived, authored = _assemble(staging, files)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    staging.rename(TEMPLATE)  # atomic on the same filesystem

    total_mako = sum(1 for _ in TEMPLATE.rglob("*.mako"))
    print(
        f"refreshed {TEMPLATE.relative_to(REPO)}: "
        f"{len(files)} files copied, {derived} derived + "
        f"{authored} authored = {total_mako} .mako templates, render-check OK"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except RefreshError as exc:
        print(f"refresh_skill_template: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
