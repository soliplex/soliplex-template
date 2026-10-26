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
the Soliplex backend is built on the published `soliplex` release image, the
frontend on the published `soliplex/frontend` web image, and `haiku-rag` /
`docling-serve` use upstream images.

The repository's stack files at the repo root (`docker-compose.yml.mako`,
`backend/environment/`, `nginx/`, …) double as its own **exemplar**: the
source the shipped template is derived from. The parameterized exemplars
carry `${param}` placeholders where per-stack values go, and the exemplar
is render-source, not a runnable stack: runnable stacks exist only on the
generation side (see
[Generate a project](../getting-started/generator.md)).

## Repo → template

The skill ships an embedded, parameterized copy of the stack under
`skills/soliplex-template/assets/template/`. It is **generated** from the repo
exemplars by `scripts/refresh_skill_template.py`:

- most tracked files are copied verbatim (minus `EXCLUDE_PATHSPECS`);
- the parameterized files are checked in as `*.mako`, already carrying the
  `${param}` substitutions and the `include_gitea` / `include_tui` /
  `enable_sandbox` conditionals, and are copied as-is;
- the shipped doc pages are copied from `docs/users/`, which carry the same
  placeholders (see below);
- a few templates with no repo exemplar are written verbatim (`AUTHORED`, e.g.
  `README.md.mako`);
- every `*.mako` the skill ships is render-checked with Mako against the
  generator's `DEFAULTS`, so a stale placeholder can't ship.

That generated tree is a **build artifact, not source**: it is gitignored and
**not committed**. `scripts/build_skill.py` regenerates it before assembling the
skill, and the functional tests regenerate it before they run, so it always
reflects the current exemplars — there is nothing to keep in sync by hand.

## Reviewing .mako diffs

Reviewing a parameterized file's diff runs into two source spellings that read
oddly against diff hunks:

- `<%text>…</%text>` encloses text Mako must pass through untouched (shell /
  Docker `${...}` expansions). A hunk showing a wrapper appear or disappear is
  an *escaping* change, not a content rewrite.
- `% if` / `% endif` are the opt-in-service conditionals; the generated stack
  shows the branch resolved, the source shows both.

When in doubt, review the *rendered* output rather than the source: every
shipped `.mako` is render-checked with the generator's `DEFAULTS`, and a
generated stack shows the final shape. For rename-plus-templatize pairs,
`git diff --find-renames` (and `-M`) keeps the edit attached to the rename.

So the workflow for changing the stack is just to **edit the exemplar** (the
real files at the repo root). To preview the generated template, or to drive the
generator from a checkout, regenerate it explicitly:

```bash
uv run scripts/refresh_skill_template.py   # (re)generate the embedded template
```

Build and validate the packaged skill (which refreshes first) with
`uv run scripts/build_skill.py`.

## Editing the documentation

Documentation lives under `docs/`, split by audience:

- **`docs/users/`** documents a *generated* stack, and is the authoritative
  source of the shipped doc pages: the pages are checked in as `*.md.mako`,
  carrying their parameters as `${...}` placeholders on top of the same
  conditional fences documented below. On this repo's site, the fences hide
  their conditional content and the placeholders render literally; in a
  generated project, the generator bakes the values and resolves the
  conditionals.
- **`docs/contributing/`** (this page) documents developing the template, and
  is repo-only.

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

**Parameters** are written as `${param}` placeholders, so the shipped value is
whatever the generator's `DEFAULTS` resolve for that stack. A generated
project's `9000` is only a value: the source spells it
`${nginx_http}`.

Pages that should **not** ship at all — the
[generator](../getting-started/generator.md) page and this contributing page —
simply live outside `docs/users/` (under `docs/getting-started/` and
`docs/contributing/`), so the copy step never touches them.

As with the stack files, the generated `.mako` are not committed — just edit
the `docs/users/` `.md`; the build and the functional tests regenerate the
template from it.
