# AGENTS.md

This file provides guidance to coding agents (Claude Code and others) when
working with code in this repository. `CLAUDE.md` is a thin shim that imports
it.

## What this repo is

A Docker Compose template that assembles a running Soliplex stack (backend, Flutter web frontend served by nginx, haiku.rag document pipeline, docling-serve, Postgres). This repo is **configuration + Dockerfiles**, not application source code — the Soliplex backend is installed from the `soliplex` Python package (pinned in `backend/constraints.txt`), the frontend is fetched from the `soliplex/frontend` GitHub release tarball at image-build time, and `haiku-rag` / `docling-serve` use upstream images.

## Common commands

First-time setup (required before `up`):

```bash
uv run scripts/generate_secrets.py   # populates .secrets/*.gen (gitignored)
```

Run the stack:

```bash
docker compose up                # foreground
docker compose up -d             # detached
docker compose build <service>   # rebuild one service (backend, nginx, postgres, …)
docker compose logs -f backend
docker compose down              # stop (keeps postgres_data volume)
docker compose down -v           # stop AND wipe postgres volume (nukes created users/DBs)
```

Ports exposed to the host: `9000` (nginx HTTP), `9443` (nginx HTTPS, self-signed), `8000` (backend direct), `8765` (haiku-ingester control plane: `/health`, `/jobs`, `/sources`, `/dlq`, `/stats`, dashboard at `/`), `5001` (docling-serve), `5432` (postgres), `3000` (gitea HTTP), `2222` (gitea SSH).

`.env` must define `OLLAMA_BASE_URL` (points at the Ollama server that serves `gpt-oss:*` models referenced in `installation.yaml`). Optionally set `INGESTER_TOKEN` to override the default placeholder token (`secret`) — see "Ingester control plane auth" below.

## Architecture

### Service graph (see `docker-compose.yml`)

- **nginx** — serves the Flutter web frontend (built from the `soliplex/frontend` release tarball inside `nginx/Dockerfile`) and reverse-proxies `/api/` and `/mcp/` to `backend:8000`. Terminates TLS on 9443 with a self-signed cert generated at build time.
- **backend** — runs `soliplex-cli serve /environment`. **Currently launched with `--no-auth-mode`** (see `docker-compose.yml`; marked temporary). The `--reload=config` flag means edits under `backend/environment/` take effect without rebuild.
- **haiku-ingester** — writer process for the LanceDB at `rag/db/`. Runs `haiku-ingester serve` with a Postgres-backed job queue (its own `soliplex_ingester` database), async worker pool, retries + DLQ, and an HTTP control plane on `8765`. The FS source under `ingester.sources` in `haiku.rag.yaml` polls `rag/docs/` and emits upsert/delete jobs that docling-serve converts and chunks. Single-writer constraint: only one ingester per LanceDB. The backend reads the same LanceDB through a bind mount, so no separate MCP server is needed.
- **docling-serve** — stateless document converter. CPU image by default; comment swap in `docker-compose.yml` for GPU.
- **postgres** — application databases created on first boot by `postgres/config/init.sh`: `soliplex_agui` (thread persistence), `soliplex_authz` (authorization policy), `soliplex_gitea` (Gitea backing store), `soliplex_ingester` (haiku-ingester job queue). Each gets a dedicated low-privilege role whose password is read from `/run/secrets/<name>_db_password`. Init runs only on an empty data volume; to re-run, `docker compose down -v`.
- **gitea** — rootless Gitea (`docker.gitea.com/gitea:*-rootless`) backed by the `soliplex_gitea` database, reverse-proxied by nginx at `https://localhost:9443/gitea/` (HTTPS only; override via `GITEA_ROOT_URL`). State lives in the `gitea_data` / `gitea_config` named volumes; built-in SSH on host `:2222`. Provision an admin user, access token, and tracking repo with `scripts/init_gitea.py` (a rotating service account whose password is never persisted; pass `--admin-user NAME` to also create a distinct, known web-UI admin login, prompted for its password). Pass `--push-to-gitea` to also set this stack's git `origin` to a Gitea repo over SSH and push the initial commit.

### Secrets

Two modes, documented at the bottom of `docker-compose.yml`:

- **File-based (active):** `.secrets/*.gen` created by `scripts/generate_secrets.py` and mounted as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored.
- **Env-var (commented):** uncomment the env-var secret blocks and set `SOLIPLEX_*` vars.

Don't hand-edit `.secrets/*.gen` — re-run the script. Destroying those files after the Postgres volume exists will break backend auth to the DB; you must also `down -v` and re-init.

The `.secrets/*.gen` files are mode `0600`. They're readable in-container because every built image runs as `PUID:PGID` (compose `build.args`, sourced from `.env`; default `1000:1000`) and `generate_secrets.py` makes the files owned by `PUID:PGID` (re-owning via a throwaway container when the operator's uid differs). This repo has no committed `.env`, so its own stack defaults to `1000:1000`; the generator (`skill/scripts/generate_soliplex_project.py`) seeds `PUID`/`PGID` from the host operator and writes them into the generated project's `.env`. Changing the uid means a `docker compose build` (it's baked in at build time).

### Soliplex configuration layout (`backend/environment/`, bind-mounted to `/environment`)

- `installation.yaml` — the top-level Soliplex install config (agents, secrets, environment vars, room list, skills, DB URIs, upload/sandbox paths). Start here when reasoning about backend behavior. The file is heavily commented with pointers to <https://soliplex.github.io/soliplex/config/> — those comments describe defaults, so a section being empty/absent is not the same as being unconfigured.
- `rooms/<name>/room_config.yaml` — per-room agent prompts, tools, skills. `room_paths` in `installation.yaml` points at `./rooms`, so every `rooms/<name>/room_config.yaml` is loaded; to exclude a room, remove its directory or replace `./rooms` with an explicit list of the wanted rooms.
- `skills/<name>/` — filesystem skills discovered via `filesystem_skills_paths`. Must also be declared in `skill_configs` to be enabled.
- `completions/`, `quizzes/`, `oidc/`, `logging.yaml`, `haiku.rag.yaml` — feature-specific configs referenced from `installation.yaml`.

### Sandbox (code execution for agents)

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build time, so adding/changing a sandbox env requires a `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents at runtime. Gitignored.

### RAG pipeline

`rag/db/` is the single source of truth for vector data and is mounted by **two** services:
- `haiku-ingester` is the writer (`/data`). Its job queue is a Postgres database (`soliplex_ingester`), not a file under `rag/db/`.
- `backend` reads it via the `haiku.rag.skills.rag` skill (`/db`, configured by `RAG_LANCE_DB_PATH` env var in `installation.yaml`).

To add documents, drop files into `rag/docs/` — the FS source under `ingester.sources` in `haiku.rag/haiku.rag.yaml` will pick them up on its next poll. To add other sources (S3, HTTP, WebDAV), append entries under `ingester.sources` in the same file and restart the ingester.

## Gotchas

- `backend/constraints.txt` pins `soliplex >= 0.79, < 0.80`. Bumping it is a backend rebuild. (The opt-in TUI pins the same package separately, in `tui/constraints.txt`.)
- The `haiku-ingester` image tag `ghcr.io/ggozad/haiku.rag-slim:0.82.1` appears in both `docker-compose.yml` and `haiku.rag/Dockerfile`, and the two must agree. It is not a free choice: the pinned `soliplex` release constrains `haiku.rag-slim` as a dependency, and the ingester writes the LanceDB the backend reads — check that constraint before changing the tag.
- The frontend is pulled from **the latest** `soliplex/frontend` GitHub release inside `nginx/Dockerfile` — rebuilds are not reproducible across time unless you pin the tarball URL. Cache-bust hash is captured from the release tag and written to `/tmp/soliplex-frontend-release-hash` during build.
- Backend `--no-auth-mode` is explicitly labeled temporary in `docker-compose.yml`. Don't assume auth is enforced end-to-end in this template.
- `docker compose down -v` drops the `postgres_data` volume — all chat threads, authz grants, and the ingester's job queue (its own `soliplex_ingester` database) go with it. The RAG vector store under `rag/db/` (bind mount, not the postgres volume) is separate, so a `down -v` doesn't touch it.

### Ingester control plane auth

`haiku-ingester` binds the control plane on `0.0.0.0:8765` so the host port mapping works. With a non-loopback bind haiku.rag requires a bearer token (`ingester.api.auth_token`); otherwise the API can cancel jobs, retry from the DLQ, and trigger source refreshes from anywhere the port is reachable.

How the token gets in:

- `haiku.rag/haiku.rag.yaml` has `ingester.api.auth_token: ${INGESTER_TOKEN}`. haiku.rag's YAML loader expands `${VAR}` / `${VAR:-default}` itself (since 0.57), so the ingester reads the bind-mounted config directly.
- The `haiku-ingester` command is still a one-line `sh -c` wrapper, but only to `export INGESTER_DB_PASSWORD` from its Docker secret: interpolation reads the environment, not files.
- `INGESTER_TOKEN` defaults to `secret` (compose sets `${INGESTER_TOKEN:-secret}`). Override it in `.env` for any deployment that isn't a single-developer laptop.

Clients call the control plane with `Authorization: Bearer $INGESTER_TOKEN`. The browser dashboard at `/` is unauthenticated HTML; its in-page JS attaches the bearer to JSON fetches itself (paste the token into the dashboard's prompt). An unset or empty `${INGESTER_TOKEN}` is a load error, so the ingester fails to start rather than quietly serving an unauthenticated control plane.

The token no longer has to avoid `sed` metacharacters, but a literal `$` must be written `$$`. Alphanumerics stay the easy choice, e.g. `openssl rand -hex 32`.
