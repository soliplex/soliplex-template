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
