---
icon: lucide/hammer
---

# Development guide

This guide is for developers working on the `soliplex-template`
repository itself — the Docker Compose template, its generator, and the
`soliplex-template` skill and Python package. If you only want to
*create* a stack, see [Generate a project](../getting-started/generator.md);
if you are operating one, see [Using a generated stack](../users/installation.md).

Much of the repo→template machinery is documented in
[Developing this template](index.md); this page covers working on the
repo day to day: the commands, the layout, and the release procedure.

## Getting started

Prerequisites:

- [uv](https://docs.astral.sh/uv/) (used for everything — do not use `pip`)
- Docker CLI and daemon (needed by the unit suite's `docker`-gated
  fixtures, and by every command under a stack directory)
- Access to an Ollama server or other inference:  at a minimum,
  run Ollama even on a machine without supported GPU, and pull tiny
  chat and embeddings models (e.g., `qwen3.5:4b`, `qwen3-embedding:0.6b`)
  for probing while generating.

Worktrees, following the container convention:

```bash
git worktree add [-b <branch>] ../<worktree-name>
cd ../<worktree-name>

# Install the project and its dev dependencies
uv sync --group dev
```

Run all Python commands through `uv run` so they use the project
environment. If `uv` resolves a Python newer than the repo supports, set
`UV_PYTHON` in your shell (the repo deliberately carries no
`.python-version` pin; see below).

```bash
# Working on the template from a checkout of this repo
uv run scripts/refresh_skill_template.py     # regenerate the embedded template
uv run scripts/generate_soliplex_project.py  # drive the generator directly
```

## Daily commands

```bash
uv run pytest tests             # full suite: unit + functional, 100% coverage required
uv run pytest tests --no-cov    # skip the coverage gate when iterating
uv run pytest tests/unit/scripts/test_generate_soliplex_project.py  -q --no-cov
uv run ruff check .
uv run ruff format .
uv run pre-commit run --all-files
uv run zensical build --strict  # the docs site, repo-only pages included
```

`scripts/refresh_skill_template.py` is the load-bearing step: it
regenerates the gitignored embedded template from the repo exemplars and
render-checks every shipped `.mako` against the generator's `DEFAULTS`,
so a stale placeholder cannot ship. Functional tests re-run it themselves.

## Repository structure

The repo-root stack files (`docker-compose.yml.mako`, `nginx/`,
`backend/`, …) are the exemplar: the render-source the skill's template
is assembled from. The repo is not a runnable stack; runnable stacks
exist only on the generation side, and what ships is the assembled
template copy under `skills/soliplex-template/assets/template/` — never
the repo files themselves. The non-obvious paths:

- `docs/users/` — the authoritative source of the shipped doc pages;
  everything ships, including the conditional fences
- `docs/getting-started/`, `docs/contributing/` — repo-only pages, never
  shipped (the contributing page documents the machinery)
- `skills/soliplex-template/` — the skill source; its
  `assets/template/` is a gitignored build artifact, regenerated from the
  exemplars (never edit it directly)
- `scripts/` — the generator, the refresh/build tooling, and the
  per-stack helpers the generated stacks run
  (`soliplex_config.py`, `rag_db.py`, `add_room.py`,
  `migrate_*.py`, `src_projects.py`)
- `src/soliplex_template/` — the published Python package (the thin
  `soliplex-plumber` implementation behind the per-stack scripts)
- `tests/functional/` — generation end-to-end (some fixtures require
  `docker`); `tests/unit/` — everything else

The generated stack's own files are parameterized copies: the `${param}`
placeholder spellings and the `if:`/`ifp:`/`site-only` fences live in the
repo sources, and
[Developing this template](index.md) documents how they resolve.

## Documentation sites

- This repo's site (`zensical.toml`) includes repo-only trees and is
  built with `uv run zensical build --strict` — part of the pre-release
  and pre-commit gates.
- A generated stack ships its own parameterized site under `docs/`,
  built the same way from the generated pages.

## Publishing

Changes to the exemplars, docs, or generator reach users by releasing
the template. Releases are made **from release branches, not `main`**:
`vX.Y` names the initial release of a minor line; `vX.Y.Z` names patch
releases on that branch, normally [cherry-picked](#backporting-a-fix)
from a squash-merged PR against `main`.

### Cutting a minor release (`vX.Y` in the examples)

1. Update `main` and open a new worktree for the release branch:

   ```bash
   # from `main` worktree
   git worktree add ../vX.Y
   ```

2. Capturing the changelog draft — the container's `release-notes/`
   directory (outside the repo) holds one draft per release:

   ```bash
   cd ../vX.Y
   git log vX.{Y-1}..HEAD > ../release-notes/soliplex-template-vX.Y.txt
   ```

   Edit it down to a human-focused changelog; drop anything already
   released in the previous line's patch releases.

3. Prepare the release on the branch: bump the package version in
   `pyproject.toml` from the `X.Ydev` dev spelling to `X.Y`, then:

   ```bash
   uv sync --all-groups
   git commit pyproject.toml uv.lock -m "chore: prep 'vX.Y' release"
   ```

4. (optional, for changes which introduce significant changes to the
   structure / configuration of generated stacks) Add the release's rung to
   [Upgrading an existing stack](../getting-started/upgrading-stack.md):
   one table row and a section describing the by-hand steps for stacks
   at the previous rung, naming the skill release this tag introduces.

5. Tag and push:

   ```bash
   git tag -s -F ../../release-notes/soliplex-template-vX.Y.txt vX.Y
   git push origin vX.Y.x vX.Y
   ```

6. Publish the GitHub release from the tag, pasting the changelog.

   Publication triggers two releases via CI:
   - `pypi.yaml` publishes the `soliplex-template` wheel to PyPI
     using Trusted Publishing
   - `build-skill.yaml` cuts the release-stamped skill.

   The stack's container images come from the other repos' releases;
   this release publishes no images.

7. Sanity-check with `uv run scripts/skill_versions.py list` from any
   installed skill: the release build appears, and the rolling line
   still tracks `main`.

### Smoke test before tagging

One full generate-walk from the release branch, both auth modes plus one
`*_port` opt-in case, walking the rendered result the way
[Using a generated stack](../users/installation.md) describes. The gates above
cover the contract; the smoke test covers the experience.

## Backporting a fix

Backports are cherry-picks of the merge commit from `main` onto the most
recent minor branch, in that branch's worktree:

```bash
git cherry-pick <pr-squash-merge-sha>
```

Then a follow the same flow as the minor release above:
- fresh changelog draft based on the most recent tag on the branch
- version bump in `pyproject.toml` and `uv sync --all-groups`
- commit
- signed tag
- push tag
- GitHub release

## Agent guidance (AGENTS.md / CLAUDE.md)

The repo's root `CLAUDE.md` is a thin stub that imports `AGENTS.md`; in
a *generated* stack that resolves to the stack's own agent guidance
(rendered from `AGENTS.md.mako`). In a *worktree* of this repo there is
no such `AGENTS.md` — the agent guidance for working on this repo is
this page plus the container-level `AGENTS.md` (which also describes the
worktree container layout). Keep the stub as-is: it is shipped
deliberately.
