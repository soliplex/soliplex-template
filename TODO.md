# TODO

## Future work

Tracked as [GitHub issues](https://github.com/soliplex/soliplex-template/issues).

## Done

- **Generated project is an installable Python library.** The generator
  synthesizes a `src/<package_name>/` package and a `tests/` tree, and the
  generated `pyproject.toml` declares a build backend so the project is
  `uv sync` / `uv pip install -e .`-able. `package_name` is derived from
  `project_name` (lower-cased, hyphens → underscores) and validated as a Python
  identifier (`generate_soliplex_project.py`); `render_tree` maps the literal
  `src/__package__/` template directory onto that name. The package ships a
  sample tool (`tools.greeting`) and a FastAPI router (`views.router`), both
  **authored** `.mako` templates under `skill/assets/template/` (the repo
  itself stays config-only). The backend imports them over a read-only `./src`
  bind mount on `PYTHONPATH` (no rebuild to edit). Because they are referenced
  by **dotted name** from the Soliplex config, the referencing files are now
  Mako templates: a new `rooms/custom/room_config.yaml` wires the tool into a
  room; `installation.yaml` registers the router via `app_router_operations`
  (`kind: "add"`, preserving the default routers) and its hypothetical
  `meta:` `my_package.*` comments now point at `${package_name}.*`. Covered by
  unit tests (100% branch) plus functional assertions and a hermetic
  package-import check; `tests/test_tools.py` / `tests/test_views.py` ship in
  the generated project for its owner to run.

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
