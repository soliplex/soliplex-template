# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Docker Compose template that assembles a running Soliplex stack (backend, Flutter web frontend served by nginx, haiku.rag document pipeline, docling-serve, Postgres). This repo is **configuration + Dockerfiles**, not application source code — the Soliplex backend is installed from the `soliplex` Python package (pinned in `backend/constraints.txt`), the frontend is fetched from the `soliplex/frontend` GitHub release tarball at image-build time, and `haiku-rag` / `docling-serve` use upstream images.

## Common commands

First-time setup (required before `up`):

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
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

Ports exposed to the host: `9000` (nginx HTTP), `9443` (nginx HTTPS, self-signed), `8000` (backend direct), `8001` (haiku-rag MCP), `5001` (docling-serve), `5432` (postgres).

`.env` must define `OLLAMA_BASE_URL` (points at the Ollama server that serves `gpt-oss:*` models referenced in `installation.yaml`).

## Architecture

### Service graph (see `docker-compose.yml`)

- **nginx** — serves the Flutter web frontend (built from the `soliplex/frontend` release tarball inside `nginx/Dockerfile`) and reverse-proxies `/api/` and `/mcp/` to `backend:8000`. On the HTTPS listener (9443), both locations sit behind an `auth_request` sub-request to Authelia (see below). Terminates TLS on 9443 with a self-signed cert generated at build time.
- **backend** — runs `soliplex-cli serve /environment`. **Currently launched with `--no-auth-mode`** (see `docker-compose.yml`; marked temporary). The `--reload=config` flag means edits under `backend/environment/` take effect without rebuild. Receives `Remote-User` / `Remote-Groups` / `Remote-Name` / `Remote-Email` headers from nginx when Authelia authenticates a request, but does not yet consume them (soliplex-config work still pending).
- **authelia** — guards `/api/` and `/mcp/` on the HTTPS server via the [AuthRequest pattern](https://www.authelia.com/integration/proxies/nginx/). Portal served at `https://127.0.0.1:9443/authelia/` (path prefix, same origin — no hosts-file edits required). File-based user backend at `authelia/users_database.yml`; Postgres storage in DB `soliplex_authelia`; secrets injected via `AUTHELIA_*_FILE`. Default dev credentials `admin` / `authelia` — rotate the argon2 hash before any non-local use. Authelia requires HTTPS, so auth is not enforced on the plain-HTTP 9000 listener. **Access the stack via `https://127.0.0.1:9443/`, not `localhost`** — Authelia's validator rejects `localhost` as a session cookie domain, so the template uses `127.0.0.1` and the nginx cert carries an `IP:127.0.0.1` SAN.
- **haiku-rag** — watches `rag/docs/` and writes a LanceDB to `rag/db/`. That same `rag/db/` directory is bind-mounted into the backend at `/db` so the backend's `rag` skill can query it. Delegates document conversion/chunking to docling-serve.
- **docling-serve** — stateless document converter. CPU image by default; comment swap in `docker-compose.yml` for GPU.
- **postgres** — four databases created on first boot by `postgres/config/init.sh`: `soliplex_agui` (thread persistence), `soliplex_authz` (soliplex's own authorization policy — distinct from Authelia), `soliplex_ingester`, `soliplex_authelia` (Authelia session/config storage). Each gets a dedicated low-privilege role whose password is read from `/run/secrets/<name>_db_password`. Init runs only on an empty data volume; to re-run, `docker compose down -v`.

### Secrets

Two modes, documented at the bottom of `docker-compose.yml`:

- **File-based (active):** `.secrets/*.gen` created by `scripts/generate-secrets.sh` and mounted as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored.
- **Env-var (commented):** uncomment the env-var secret blocks and set `SOLIPLEX_*` vars.

Don't hand-edit `.secrets/*.gen` — re-run the script. Destroying those files after the Postgres volume exists will break backend auth to the DB; you must also `down -v` and re-init.

### Soliplex configuration layout (`backend/environment/`, bind-mounted to `/environment`)

- `installation.yaml` — the top-level Soliplex install config (agents, secrets, environment vars, room list, skills, DB URIs, upload/sandbox paths). Start here when reasoning about backend behavior. The file is heavily commented with pointers to https://soliplex.github.io/soliplex/config/ — those comments describe defaults, so a section being empty/absent is not the same as being unconfigured.
- `rooms/<name>/room_config.yaml` — per-room agent prompts, tools, skills. The `room_paths` list in `installation.yaml` is the source of truth for which rooms are loaded; adding a directory under `rooms/` without listing it there does nothing.
- `skills/<name>/` — filesystem skills discovered via `filesystem_skills_paths`. Must also be declared in `skill_configs` to be enabled.
- `completions/`, `quizzes/`, `oidc/`, `logging.yaml`, `haiku.rag.yaml` — feature-specific configs referenced from `installation.yaml`.

### Sandbox (code execution for agents)

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build time, so adding/changing a sandbox env requires a `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents at runtime. Gitignored.

### RAG pipeline

`rag/db/` is the single source of truth for vector data and is mounted by **two** services:
- `haiku-rag` writes it (`/data`)
- `backend` reads it via the `haiku.rag.skills.rag` skill (`/db`, configured by `RAG_LANCE_DB_PATH` env var in `installation.yaml`)

Drop documents into `rag/docs/` and haiku-rag will ingest them on its monitor cycle.

## Gotchas

- `constraints.txt` pins `soliplex >= 0.60.0.1, < 0.61`. Bumping this is a backend rebuild.
- The frontend is pulled from **the latest** `soliplex/frontend` GitHub release inside `nginx/Dockerfile` — rebuilds are not reproducible across time unless you pin the tarball URL. Cache-bust hash is captured from the release tag and written to `/tmp/soliplex-frontend-release-hash` during build.
- Backend `--no-auth-mode` is explicitly labeled temporary in `docker-compose.yml`. Authelia gates at nginx but the backend does not yet enforce the forwarded `Remote-*` identity headers — don't assume auth is enforced end-to-end.
- `docker compose down -v` drops the `postgres_data` volume — all chat threads, authz grants, ingester state, **and Authelia session/config state** go with it.
- If nginx refuses to start with `"auth_request" directive is not allowed here`, the nginx variant pulled into the image lacks the `http_auth_request` module — add `nginx-full` to the `apt-get install` line in `nginx/Dockerfile`.
- Authelia requires HTTPS; the `/api/` and `/mcp/` auth gates are only wired on the 9443 listener. The 9000 listener stays open for behind-an-upstream-proxy deployments that terminate TLS upstream.
