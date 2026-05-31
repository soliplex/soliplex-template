#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["mako"]
# ///
"""Regenerate skill/assets/template/ from the live repo exemplars.

The embedded template shipped inside the soliplex-project-generator skill is a
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

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "skill" / "assets" / "template"

# Tracked paths NOT copied into the template (git pathspecs, repo-relative).
EXCLUDE_PATHSPECS = [
    ":!TODO.md",
    ":!.claude",
    ":!.github",                            # this repo's CI, not a project file
    ":!skill",                              # the skill source itself (no recursion)
    ":!dist",                               # build artifact
    ":!scripts/build_skill.py",             # skill-build tooling, not project files
    ":!scripts/refresh_skill_template.py",
    ":!pyproject.toml",                     # repo tooling; project gets pyproject.toml.mako
    ":!uv.lock",                            # repo tooling lockfile
    ":!README.md",                          # replaced by the authored README.md.mako
]


class RefreshError(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise RefreshError(msg)


def esc(text: str, *literals: str) -> str:
    """Wrap literal ``${...}`` runs in ``<%text>`` so Mako passes them through."""
    for lit in literals:
        require(lit in text, f"expected literal {lit!r} to escape")
        text = text.replace(lit, f"<%text>{lit}</%text>")
    return text


def repl(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        require(old in text, f"expected to find {old!r}")
        text = text.replace(old, new)
    return text


# --------------------------------------------------------------------------
# Per-file transforms: repo-relative path -> function(original text) -> mako text
# Output path is the key + ".mako".
# --------------------------------------------------------------------------
def t_compose(text: str) -> str:
    text = esc(
        text,
        "${OLLAMA_BASE_URL}", "${INGESTER_TOKEN:-secret}", "${AUTO_CREATE_DATABASE:-1}",
        "${OPENAI_API_KEY}", "${ANTHROPIC_API_KEY}", "${VOYAGE_API_KEY}", "${CO_API_KEY}",
    )
    return repl(text, [
        ("name: soliplex-template", "name: ${project_name}"),
        ('- "9000:9000"', '- "${nginx_http}:9000"'),
        ('- "9443:9443"', '- "${nginx_https}:9443"'),
        ("--public-url https://soliplex.localhost:9443/tui",
         "--public-url https://${server_name}:${nginx_https}/tui"),
        ("soliplex-cli serve --no-auth-mode --reload=config",
         "soliplex-cli serve ${backend_auth_flag}--reload=config"),
        ('- "8765:8765"', '- "${ingester_port}:8765"'),
        ('- "5001:5001"', '- "${docling_port}:5001"'),
        ('- "5432:5432"', '- "${postgres_port}:5432"'),
        ("- ./rag/docs:/docs", "- ./${docs_dir}:/docs"),
    ])


def t_installation(text: str) -> str:
    return repl(text, [
        ('id: "soliplex-conf-minimal"', 'id: "${setup_id}"'),
        ('  - id: "default_chat"\n    model_name: "gpt-oss:latest"',
         '  - id: "default_chat"\n    model_name: "${chat_model}"'),
        ('  - id: "title"\n    model_name: "gpt-oss:latest"',
         '  - id: "title"\n    model_name: "${title_model}"'),
        ('"gpt-oss:20b"', '"${chat_model_alt}"'),
        ("soliplex_agui", "${agui_db}"),
        ("soliplex_authz", "${authz_db}"),
    ])


def t_backend_haiku(text: str) -> str:
    return repl(text, [
        ("name: qwen3-embedding:4b", "name: ${rag_embed_model}"),
        ("vector_dim: 2560", "vector_dim: ${rag_embed_dim}"),
        ("qa:\n  model:\n    name: gpt-oss:latest",
         "qa:\n  model:\n    name: ${rag_qa_model}"),
        ("research:\n  model:\n    name: gpt-oss:latest",
         "research:\n  model:\n    name: ${rag_research_model}"),
        ("chunk_size: 256", "chunk_size: ${chunk_size}"),
    ])


def t_ingester_haiku(text: str) -> str:
    return repl(text, [("chunk_size: 256", "chunk_size: ${chunk_size}")])


def t_backend_constraints(text: str) -> str:
    new, n = re.subn(r"(?m)^soliplex .*$",
                     "soliplex ${soliplex_backend_constraint}", text)
    require(n == 1, f"expected one 'soliplex ...' line in backend constraints, got {n}")
    return new


def t_tui_constraints(text: str) -> str:
    new, n = re.subn(r"(?m)^soliplex .*$",
                     "soliplex ${soliplex_tui_constraint}", text)
    require(n == 1, f"expected one 'soliplex ...' line in tui constraints, got {n}")
    return new


def t_nginx_conf(text: str) -> str:
    require(text.count("server_name localhost;") == 2,
            "expected two 'server_name localhost;' in nginx.conf")
    return text.replace("server_name localhost;", "server_name ${server_name};")


def t_nginx_dockerfile(text: str) -> str:
    # Wrap everything literal in <%text> (preserves backslash-newlines and the
    # builder's shell ${...} expansions); break out only for the cert subject.
    m = re.search(r'-subj "([^"]*)"', text)
    require(m is not None, "expected a -subj \"...\" line in nginx/Dockerfile")
    subj = m.group(1)
    require(text.count(subj) == 1, f"cert subject {subj!r} is not unique")
    before, after = text.split(subj, 1)
    return "<%text>" + before + "</%text>${tls_subject}<%text>" + after + "</%text>"


def t_init_sh(text: str) -> str:
    return repl(text, [
        ("soliplex_agui", "${agui_db}"),
        ("soliplex_authz", "${authz_db}"),
    ])


def t_gitignore(text: str) -> str:
    # The repo ignores its own skill build artifacts under /dist/; a generated
    # project has no such artifact, so strip that block (a '# Skill build
    # artifacts' comment, any further comment lines, and the /dist/ entry).
    new, n = re.subn(
        r"\n# Skill build artifacts[^\n]*\n(?:#[^\n]*\n)*/dist/\n\n?",
        "\n", text, count=1,
    )
    require(n == 1, "expected the '/dist/' skill build artifacts block in .gitignore")
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

## First-time setup

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
```

Set `OLLAMA_BASE_URL` in `.env` if it is not already correct.

## Run the stack

```bash
docker compose up            # foreground
docker compose up -d         # detached
docker compose logs -f backend
docker compose down          # stop (keeps the postgres_data volume)
docker compose down -v       # stop AND wipe the postgres volume
```

## Ports

| Service          | Host port |
|------------------|-----------|
| nginx (HTTP)     | ${nginx_http} |
| nginx (HTTPS)    | ${nginx_https} |
| haiku-ingester   | ${ingester_port} |
| docling-serve    | ${docling_port} |
| postgres         | ${postgres_port} |

Open the app at <http://localhost:${nginx_http}/> (or
<https://${server_name}:${nginx_https}/> for TLS).
""",
    "pyproject.toml.mako": """\
[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# Dependencies for running this project's own tooling outside Docker
# (e.g. `soliplex-cli` against the Postgres backing store). The container
# images install their own pinned deps; this file is for host-side use:
#   uv sync     # or: pip install -e .
dependencies = [
    "soliplex ${soliplex_backend_constraint}",
    "psycopg[binary]",
    "asyncpg",
]
""",
}

# Probe parameters for the post-refresh render check (values are arbitrary).
PROBE = dict(
    project_name="probe", setup_id="probe", nginx_http=1, nginx_https=2,
    ingester_port=3, docling_port=4, postgres_port=5, server_name="probe",
    backend_auth_flag="", tls_subject="probe", chat_model="m", chat_model_alt="m",
    title_model="m", rag_embed_model="m", rag_embed_dim=1, rag_qa_model="m",
    rag_research_model="m", chunk_size=1, agui_db="db", authz_db="db2",
    soliplex_backend_constraint="c", soliplex_tui_constraint="c", docs_dir="d",
)


def tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z", "--", ".", *EXCLUDE_PATHSPECS],
        cwd=REPO, text=True,
    )
    return [p for p in out.split("\0") if p]


def render_check(root: Path) -> int:
    from mako.template import Template
    from mako import exceptions
    bad = 0
    for mako in sorted(root.rglob("*.mako")):
        try:
            Template(filename=str(mako), strict_undefined=True).render(**PROBE)
        except Exception:
            bad += 1
            print(f"render FAILED: {mako.name}", file=sys.stderr)
            print(exceptions.text_error_template().render(), file=sys.stderr)
    return bad


def _build_into(dest_root: Path, files: list[str]) -> tuple[int, int]:
    """Assemble the template under ``dest_root``. Returns (derived, authored)."""
    # copy verbatim
    for rel in files:
        src = REPO / rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)  # preserves mode (executable scripts, etc.)

    # in-place edits to files kept under their original name
    for rel, fn in VERBATIM_EDITS.items():
        copied = dest_root / rel
        require(copied.is_file(), f"verbatim-edit source {rel} not found (committed?)")
        try:
            copied.write_text(fn(copied.read_text()))
        except RefreshError as exc:
            raise RefreshError(f"{rel}: {exc}") from exc

    # rewrite parameterized files as .mako
    derived = 0
    for rel, fn in DERIVED.items():
        copied = dest_root / rel
        require(copied.is_file(),
                f"derived source {rel} not found among tracked files (committed?)")
        try:
            mako_text = fn(copied.read_text())
        except RefreshError as exc:
            raise RefreshError(f"{rel}: {exc}") from exc
        copied.with_name(copied.name + ".mako").write_text(mako_text)
        copied.unlink()
        derived += 1

    # authored templates
    for name, content in AUTHORED.items():
        (dest_root / name).write_text(content)

    return derived, len(AUTHORED)


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
        derived, authored = _build_into(staging, files)
        bad = render_check(staging)
        if bad:
            raise RefreshError(f"{bad} template(s) failed the render check")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    staging.rename(TEMPLATE)  # atomic on the same filesystem

    total_mako = sum(1 for _ in TEMPLATE.rglob("*.mako"))
    print(f"refreshed {TEMPLATE.relative_to(REPO)}: "
          f"{len(files)} files copied, {derived} derived + "
          f"{authored} authored = {total_mako} .mako templates, render-check OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RefreshError as exc:
        print(f"refresh_skill_template: error: {exc}", file=sys.stderr)
        raise SystemExit(1)
