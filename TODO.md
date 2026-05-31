# TODO

## Project-generator filesystem skill

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
