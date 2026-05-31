# TODO

## Future work

- **Run the docker-gated functional cases in CI.** `python-test.yaml` currently
  excludes the `needs_docker` cases (the `postgres` bring-up); running them
  needs a docker-enabled CI job. Deferred alongside the full-stack bring-up
  item below.

- **Expand supported Python to match soliplex/soliplex.** This repo pins
  `requires-python = ">=3.13"` and the CI workflows run a single 3.13
  (`python-version-file`). soliplex tests a matrix (3.12, 3.13, 3.14, coverage
  only on >= 3.13). To match:
  - Lower `requires-python` toward `>=3.12` (reconcile with the scripts' PEP 723
    `requires-python`: `build_skill.py`/`refresh_skill_template.py` already say
    `>=3.11`, `generate_soliplex_project.py` says `>=3.13`; decide the real floor
    and align all of them + `[tool.ruff] target-version`).
  - Switch `python-lint.yaml` and `python-test.yaml` to a `strategy.matrix`
    over the supported versions (mirroring soliplex), running coverage only on
    `>= 3.13` (e.g. `--no-cov` on the lowest, as soliplex does for 3.12).
  - Verify the suites + the generator actually pass on each version (3.12 may
    need syntax/typing checks; `uv sync` against each interpreter).

- **Extend the functional test toward a full-stack bring-up.** The current
  functional test brings up only `postgres` (deterministic, no network beyond a
  base image). A full `docker compose up -d --wait` of the whole stack would be
  the strongest signal but is heavy and flaky: 4 local builds + 2 multi-GB image
  pulls, network egress to GitHub/PyPI/ghcr, the non-reproducible "latest
  frontend release", and a **live Ollama** serving `qwen3-embedding:4b`
  (haiku-ingester health) / `gpt-oss:*`. If added, gate it behind an explicit
  opt-in env flag plus daemon/network/Ollama skip-conditions, never in the
  default functional run.

- **Documentation.** Write docs covering the three ways this repo is used:
  1. **Using the main repo configuration as-is** — clone the template, run
     `scripts/generate-secrets.sh`, set `OLLAMA_BASE_URL`, `docker compose up`;
     the architecture/services/ports/secrets already sketched in `CLAUDE.md`.
  2. **Using the `soliplex-template` skill** — install/point an agent at the
     published skill, gather parameters, run `generate_soliplex_project.py` to
     scaffold a new project; how to fetch/upgrade the skill via
     `scripts/skill_versions.py`.
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
  `generate_soliplex_project.py` place them at the resolved package path, since
  the directory name itself is parameter-driven. Also extend the generated
  `pyproject.toml` to declare the package (build backend + `[tool.*]` packaging
  config) so the project is `pip install -e .` / `uv sync`-able, and wire the
  package onto the backend's `PYTHONPATH` (or install it) so the Soliplex
  install can import it.

- **Restore the Slack failure notification.** A `Notify Slack on failure` step
  (mirroring the `soliplex-docs` workflow) was omitted from all three workflows
  (`build-skill.yaml`, `python-lint.yaml`, `python-test.yaml`) because the
  `SLACK_NOTIFY_URL` secret isn't available yet. Add it once the secret exists —
  it posts to `#soliplex` via `slackapi/slack-github-action@v2.1.0` on
  `if: failure()`.

## Done

- **`soliplex-template` filesystem skill** (the core feature). A filesystem
  skill (agent-skills spec, <https://agentskills.io/home>) that prompts for
  parameters — project name / output dir, host ports (nginx HTTP/HTTPS, backend,
  haiku-rag, postgres), `OLLAMA_BASE_URL` + the models referenced in
  `installation.yaml`, Postgres database/role names, the `soliplex` version
  constraint, and auth mode (vs. `--no-auth-mode`) — and scaffolds a runnable
  Soliplex Docker Compose project:
  - **nginx** — serves the Flutter web frontend (from the `soliplex/frontend`
    release tarball) and reverse-proxies `/api/` and `/mcp/` to the backend.
  - **soliplex backend** — `soliplex-cli serve /environment`.
  - **haiku ingester** — watches `rag/docs/` (by default) and writes the LanceDB
    the backend reads.
  - **postgres** — backing store (thread persistence, authz, ingester DBs).

  Plus the supporting scaffolding: the `backend/environment/` config tree,
  `scripts/generate-secrets.sh`, `postgres/config/init.sh`, `.env`,
  `.gitignore`, etc. The skill source lives under `skill/`;
  `scripts/build_skill.py` assembles and validates it into `dist/`;
  `scripts/refresh_skill_template.py` re-derives the embedded template from the
  repo exemplars; and `.github/workflows/build-skill.yaml` publishes it as a
  GitHub Release asset (mirroring the `soliplex-docs` skill workflow).

- **Unit-test coverage for the Python scripts** (following
  soliplex/soliplex#1033). All four scripts are covered, hermetic, loaded by
  file path via `importlib.util`, AAA layout (one act per test):
  - `scripts/build_skill.py` → `tests/unit/scripts/test_build_skill.py`
  - `scripts/refresh_skill_template.py` → `test_refresh_skill_template.py`
  - `skill/scripts/generate_soliplex_project.py` →
    `test_generate_soliplex_project.py`
  - `skill/scripts/skill_versions.py` → `test_skill_versions.py` (GitHub seams
    mocked; published versions served from local `file://` tarballs)

  `pyproject.toml` carries the `pytest`/`pytest-cov`/`coverage` `dev` deps and a
  `[tool.pytest.ini_options]` block (`testpaths = ["tests/unit"]`,
  `--cov=scripts --cov=skill/scripts --cov=tests/unit --cov-branch
  --cov-fail-under=100`). 154 tests, 100% branch coverage on all four scripts.
  Run with `uv run --group dev pytest`.

- **Functional test for the generator.**
  `tests/functional/test_generate_project.py` runs the real CLI
  (`generate_soliplex_project.py`) as a subprocess with `--run-secrets` +
  `--disable-gpg-sign`, then asserts: no leftover `*.mako`; parameters
  substituted and cross-file consistent; `<%text>` literals survived; runtime
  dirs + `.gitkeep`; `.env`; the four `.secrets/*.gen` (mode 0600); a single
  clean `git` commit; `docker compose config -q` valid; and `postgres` brought
  up (`up -d --wait`) to **healthy** then `down -v`. Live deps skip-with-warning
  when absent (git/bash/openssl; docker CLI/daemon). The tree is opt-in (not in
  `testpaths`); `needs_docker` marker registered. Run with
  `uv run --group dev pytest tests/functional --no-cov`.

- **Linting / pre-commit** (mirrors soliplex/soliplex `pyproject.toml` +
  `.pre-commit-config.yaml`). `pyproject.toml` carries the `[tool.ruff*]`
  (line-length 79, `F/E/B/U/I/PD/TRY/PT`, single-line isort) and
  `[tool.pymarkdown]` config; `ruff` is in the `dev` group.
  `.pre-commit-config.yaml` runs ruff-check + ruff-format, the `pre-commit-hooks`
  hygiene set (incl. `no-commit-to-branch` main/master, trailing-whitespace,
  end-of-file, check-toml, check-yaml, debug-statements), gitleaks, a local
  `pip-audit`, pymarkdown, and actionlint. `uvx pre-commit run --all-files` is
  green. Notes:
  - check-yaml caught a **real bug** — a duplicate `processing:` key in
    `backend/environment/haiku.rag.yaml` that silently dropped `chunk_size`;
    merged into one block.
  - `refresh_skill_template.py` now excludes `tests/` and
    `.pre-commit-config.yaml` (they aren't project files), and
    `t_nginx_dockerfile` emits a newline-terminated `.mako` (keeps end-of-file
    and refresh from fighting) while the rendered Dockerfile stays
    byte-identical.
  - `ruff check` (lint) is **clean** repo-wide and gated by the `ruff-check`
    pre-commit hook. (This goes one step beyond soliplex, whose pre-commit only
    formats and enforces `ruff check` via CI; here it's enforced locally too.)
    Clearing the findings: `TRY003` messages were moved into
    `GenError`/`RefreshError` factory classmethods (no inline raise messages);
    `B904` (`raise ... from exc`), `B028` (`warnings.warn(..., stacklevel=2)`),
    `PT018` (split composite asserts), `TRY301` (extracted `refresh._assemble`),
    and `E501` (wrapped) all fixed in code — no `# noqa`, no rule ignores.
  - Imports are module-style repo-wide (`import pathlib` → `pathlib.Path`,
    `from mako import template`, `from collections import abc`); the four
    scripts were converted, the tests were already compliant.
  - `shellcheck` and a `build_skill.py` hook (from the earlier sketch) are not
    in soliplex's pre-commit, so left out to match. soliplex#1028 is historical
    reference only.

- **`python-lint.yaml` CI workflow** (parallel to soliplex/soliplex's). Runs
  `uv run ruff format --check` + `uv run ruff check` on push/PR to `main` (paths:
  `pyproject.toml`, `uv.lock`, `scripts/**`, `skill/scripts/**`, `tests/**`, the
  workflow itself) and on `workflow_dispatch`. Uses the repo's `setup-uv` +
  `python-version-file` convention (like `build-skill.yaml`); actionlint-clean.

- **`python-test.yaml` CI workflow** (parallel to soliplex/soliplex's). On
  push/PR to `main` (+ dispatch): `uv run pytest` (tests/unit under the pyproject
  addopts, so the `--cov-fail-under=100` gate is enforced in CI) then
  `uv run pytest --no-cov -m "not needs_docker" tests/functional/` (the hermetic
  functional cases; the docker-gated ones excluded — the analogue of soliplex's
  `not needs_llm`). actionlint-clean.
