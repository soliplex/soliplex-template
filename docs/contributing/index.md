---
icon: lucide/wrench
---

# Developing this template

This page is for working on the `soliplex-template` repository itself — not for
running a generated stack. It is repo-only: it never ships into a generated
project. To *create* a stack, see
[Generate a project](../getting-started/generator.md).

## What this repository is

`soliplex-template` is **configuration + Dockerfiles**, not application source:
the Soliplex backend is installed from the published `soliplex` package, the
frontend from a `soliplex/frontend` release, and `haiku-rag` / `docling-serve`
use upstream images.

The repository also doubles as its own **exemplar** — the stack files at the
repo root (`docker-compose.yml`, `backend/environment/`, `nginx/`, …) are a
real, runnable stack *and* the source the shipped template is derived from.

## Repo → template

The skill ships an embedded, parameterized copy of the stack under
`skills/soliplex-template/assets/template/`. It is **generated** from the repo
exemplars by `scripts/refresh_skill_template.py`:

- most tracked files are copied verbatim (minus `EXCLUDE_PATHSPECS`);
- the parameterized files (`docker-compose.yml`, `installation.yaml`,
  `nginx.conf`, …) are rewritten as `*.mako` by per-file transforms (`DERIVED`),
  injecting `${param}` substitutions and the `include_gitea` / `include_tui`
  conditionals;
- the shipped doc pages are derived from `docs/users/` (see below);
- a few templates with no repo exemplar are written verbatim (`AUTHORED`, e.g.
  `README.md.mako`);
- every generated `.mako` is render-checked with Mako.

So the workflow for changing the stack is to **edit the exemplar, then
regenerate and commit both**:

```bash
uv run scripts/refresh_skill_template.py   # regenerate the embedded template
```

The regenerated `skills/soliplex-template/assets/template/` is expected to stay
in sync with the exemplar, so commit it alongside your change. Build and
validate the packaged skill with `uv run scripts/build_skill.py`.

## Editing the documentation

Documentation lives under `docs/`, split by audience:

- **`docs/users/`** documents a *generated* stack, and is the authoritative
  source the shipped `docs/*.md.mako` are derived from. Edit the `.md` here —
  **never** the generated `.md.mako`, which the next refresh overwrites.
- **`docs/contributing/`** (this page) documents developing the template, and
  is repo-only.

The `docs/users/` pages are plain Markdown that read correctly on this site;
`t_user_doc()` (in `refresh_skill_template.py`) turns a few inline conventions
into Mako when deriving each `.md.mako`.

**Conditional content** for an opt-in service is wrapped in HTML-comment
fences. They are invisible on this site and become a Mako conditional in a
generated project, so the block ships only when that service is included:

```text
<!-- if:gitea -->
Shown only when the stack includes Gitea (likewise `if:tui`).
<!-- endif -->
```

To make a single list *item* conditional, indent the fences to the list's
content column so the Markdown linter still sees one list:

```text
- always shipped
  <!-- if:gitea -->
- shipped only with Gitea
  <!-- endif -->
```

**Repo-site-only content** — e.g. the "About this page" banner — is wrapped so
it appears on this site but is stripped from the shipped docs entirely:

```text
<!-- site-only -->
Shown only on this repo's docs site.
<!-- endsite-only -->
```

**Parameters** are written as their concrete default; a page-specific anchor
list in `USER_DOC_PARAMS` rewrites each to the matching Mako `${...}`:

```text
`9000`          ->  `${nginx_http}`
`soliplex_agui` ->  `${agui_db}`
```

Pages that should **not** ship at all — the
[generator](../getting-started/generator.md) page and this contributing page —
simply live outside `docs/users/` (under `docs/getting-started/` and
`docs/contributing/`), so the derive step never touches them.

As with the stack files, after editing a `docs/users/` page run
`refresh_skill_template.py` and commit the regenerated `.mako`.
