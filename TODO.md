# TODO

## `soliplex-template` filesystem skill

Write a **filesystem skill** (per the agent-skills spec,
https://agentskills.io/home) that scaffolds a new Soliplex Docker
Compose project.

The skill should:

1. **Prompt the user** for a set of parameters needed to parameterize the
   generated stack, e.g.:
   - project name / output directory
   - exposed host ports (nginx HTTP/HTTPS, backend, haiku-rag, postgres)
   - `OLLAMA_BASE_URL` and the model(s) referenced in `installation.yaml`
   - Postgres database/role names
   - `soliplex` package version constraint
   - whether to enable auth (vs. `--no-auth-mode`)

2. **Generate a new project** from those parameters, producing a
   `docker-compose.yml` that runs:
   - **nginx** — serves the Soliplex Flutter web frontend and reverse-proxies
     `/api/` and `/mcp/` to the backend
   - **soliplex backend** — `soliplex-cli serve /environment`
   - **soliplex frontend** — built/served via nginx (from the
     `soliplex/frontend` release tarball)
   - **haiku ingester** — watches a user-specified directory (by default
     `rag/docs/`) writes the LanceDB read by the backend
   - **postgres** — backing store for the backend (thread persistence, authz,
     ingester databases)

   Along with the supporting scaffolding this template already provides:
   `backend/environment/` config tree, `scripts/generate-secrets.sh`,
   `postgres/config/init.sh`, `.env`, `.gitignore`, etc.

This template repo itself is the reference for the files the skill should emit.

### Status

Done: the skill source lives under `skill/`, `scripts/build_skill.py` assembles
and validates it into `dist/`, `scripts/refresh_skill_template.py` re-derives the
embedded template from the repo exemplars, and
`.github/workflows/build-skill.yaml` publishes the skill as a GitHub Release
asset (mirroring the `soliplex-docs` skill workflow).

## Future work

- **Add unit-test coverage for the repo-level `scripts/`.** Cover
  `scripts/build_skill.py` and `scripts/refresh_skill_template.py` (and, by the
  same pattern, the bundled `skill/scripts/generate.py` and
  `skill/scripts/skill_versions.py`), following soliplex/soliplex#1033:
  - Put tests under `tests/unit/scripts/` (`test_build_skill.py`,
    `test_refresh_skill_template.py`, …). Since these scripts aren't part of an
    importable package, load each by file path via `importlib.util` (as the PR's
    `test_skill_versions.py` does).
  - Keep tests **hermetic**: drive a `tmp_path` repo/skill tree, mock the
    external seams (`subprocess` for git/`agentskills`, `urllib` / GitHub API,
    `file://` tarballs for downloads) — no network, no real `git`/Docker.
  - Use the setup / act / assert layout (one act per test; parametrize or split
    otherwise), matching the PR's convention.
  - Wire it into `pyproject.toml`: add `pytest` + `pytest-cov` to the `dev`
    group and a `[tool.pytest.ini_options]` block with
    `testpaths = ["tests/unit"]`, `python_files = "test_*.py"`, and
    `addopts = "--cov=scripts --cov-branch --cov-fail-under=100"` (the PR holds
    100% branch coverage).
  - Add a CI step (or fold into the pre-commit work below) to run the suite.
- **Documentation.** Write docs covering the three ways this repo is used:
  1. **Using the main repo configuration as-is** — clone the template, run
     `scripts/generate-secrets.sh`, set `OLLAMA_BASE_URL`, `docker compose up`;
     the architecture/services/ports/secrets already sketched in `CLAUDE.md`.
  2. **Using the `soliplex-template` skill** — install/point an agent at the
     published skill, gather parameters, run `generate.py` to scaffold a new
     project; how to fetch/upgrade the skill via `scripts/skill_versions.py`.
  3. **Copying an appropriate subset of the repo-level docs into the generated
     project** — decide which docs belong in a scaffolded project (vs. this
     template repo), and have the generator emit that subset (as `.mako`
     templates under `skill/assets/template/`, parameterized like the rest) so
     each generated project ships its own right-sized documentation.
- **Make the generated project an installable Python library.** The scaffolded
  project should give its owner a place to write custom code for her Soliplex
  installation, so the generator must synthesize:
  - a `src/${project_name}/` package tree (e.g. `__init__.py`, a sample module)
    — using the validated/normalized `project_name` as the import name
    (lower-case, hyphens → underscores), and
  - an associated `tests/` tree (e.g. a sample `test_*.py`).

  These must be **synthesized by the generator**, not copied from this repo —
  the main repo has no corresponding `src/`/`tests/` files, and shouldn't need
  any. Likely approach: add `.mako` templates for the new files under
  `skill/assets/template/` (e.g. `src/__package__/__init__.py.mako`) and have
  `generate.py` place them at the resolved package path, since the directory
  name itself is parameter-driven. Also extend the generated `pyproject.toml`
  to declare the package (build backend + `[tool.*]` packaging config) so the
  project is `pip install -e .` / `uv sync`-able, and wire the package onto the
  backend's `PYTHONPATH` (or install it) so the Soliplex install can import it.
- **Restore the Slack failure notification in `build-skill.yaml`.** The
  `Notify Slack on failure` step (mirroring the `soliplex-docs` workflow) was
  removed for now because the `SLACK_NOTIFY_URL` secret isn't available yet.
  Re-add it once the secret exists — it posts to `#soliplex` via
  `slackapi/slack-github-action@v2.1.0` on `if: failure()`.
- **Add `.pre-commit-config.yaml`.** Set up pre-commit hooks for this repo,
  including:
  - `actionlint` — lint the GitHub Actions workflow(s) under
    `.github/workflows/` (we could not run it during the workflow's authoring).
  - other germane checks, e.g. `ruff` (lint/format the Python scripts under
    `scripts/` and `skill/scripts/`), `check-yaml`/`check-json` and the usual
    `pre-commit-hooks` hygiene hooks, `shellcheck` for the shell scripts
    (`scripts/generate-secrets.sh`, `postgres/config/init.sh`), and a
    hook that runs `scripts/build_skill.py` (skill validation via `skills-ref`).
  - See the `.pre-commit-config.yaml` added in soliplex/soliplex#1028 for a
    reference shape.
