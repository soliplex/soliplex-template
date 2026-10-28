# AGENTS.md

This file provides guidance to coding agents (Claude Code and others) when
working with code in this repository. `CLAUDE.md` is a thin shim that imports
it.

## What this repo is

A Docker Compose template that assembles a running Soliplex stack (backend, Flutter web frontend served by nginx, haiku.rag document pipeline, docling-serve, Postgres). This repo is **configuration + Dockerfiles**, not application source code — the Soliplex backend is built on the published `ghcr.io/soliplex/soliplex-postgres` release image (its tag is in `backend/Dockerfile`), the frontend is copied from the published `ghcr.io/soliplex/frontend-web` image, and `haiku-rag` / `docling-serve` use upstream images.

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

Ports exposed to the host: `9000` (nginx HTTP), `9443` (nginx HTTPS, self-signed)${", `${gitea_http_port}` (gitea HTTP)" if include_gitea and gitea_http_port else ""}${", `${gitea_ssh_port}` (gitea SSH)" if include_gitea and gitea_ssh_port else ""}${", `${backend_port}` (backend direct)" if backend_port else ""}${", `${ingester_port}` (haiku-ingester control plane direct: bearer required; behind nginx at `/ingester/` see \"Ingester control plane auth\")" if ingester_port else ""}${", `${docling_port}` (docling-serve)" if docling_port else ""}${", `${postgres_port}` (postgres)" if postgres_port else ""}.

Every other service binds only the compose network. Set a `<service>_port` parameter to publish one (e.g. `ingester_port` for direct bearer-authed access to the control plane -- see "Ingester control plane auth" for the token).

`.env` must define `OLLAMA_BASE_URL` (points at the Ollama server that serves `gpt-oss:*` models referenced in `installation.yaml`) — see "Ingester control plane auth" below for how the ingester's bearer token is provisioned.

## Architecture

### Service graph (see `docker-compose.yml`)

- **nginx** — serves the Flutter web frontend (copied in `nginx/Dockerfile` from the published `ghcr.io/soliplex/frontend-web` image) and reverse-proxies `/api/` and `/mcp/` to `backend:8000`. Terminates TLS on 9443 with a self-signed cert generated at build time.
- **backend** — runs `soliplex-cli serve /environment`. **Currently launched with `--no-auth-mode`** (see `docker-compose.yml`; marked temporary). The `--reload=config` flag means edits under `backend/environment/` take effect without rebuild, and one `--reload-dirs` flag per `PYTHONPATH` entry (kept in step by `skills/soliplex-template/scripts/src_projects.py`) restarts it when that project's code changes.
- **soliplex-dev** — one-shot dev task runner, held out of `docker compose up` by `profiles: ["devmode"]`. Built on the backend's image (`soliplex-dev/Dockerfile`, via compose's `service:backend` build context) plus every `src/` project's `dev` dependency group, listed in the committed `soliplex-dev/requirements-dev.txt` that `skills/soliplex-template/scripts/src_projects.py dev-requirements` regenerates, so project tests run here against the backend's own versions (`docker compose run --rm --no-deps -w /app/src/<project> soliplex-dev /app/.venv/bin/python -m pytest`); and mirroring its secrets, mounts and environment — the same `/app/src` paths and, via a YAML alias, the very same `PYTHONPATH`, so `soliplex-cli audit` here validates the installation the backend actually serves. The one difference is that `src/` is mounted **read-write**, for tools that write inside a project directory: `docker compose run --rm -w /app/src/<project> soliplex-dev /app/.venv/bin/alembic …` (alembic writes into its `versions/` directory and prepends `.` to `sys.path`). Any project directory under `src/` works, not just a `soliplex` checkout.
- **haiku-ingester** — writer process for the LanceDB databases at `rag/db/`. Runs `haiku-ingester serve --root-path /ingester` with a Postgres-backed job queue (its own `soliplex_ingester` database), async worker pool, retries + DLQ, and an HTTP control plane on `8765`. The FS source of the `ingester.databases` entry keyed `haiku.rag`, in
  `haiku.rag.yaml` polls `rag/docs/` and emits upsert/delete jobs that docling-serve converts and chunks. Single-writer constraint: only one ingester per LanceDB. The backend reads the same LanceDB through a bind mount, so no separate MCP server is needed.
- **haiku-rag** — one-shot runner for the `haiku-rag` CLI, held out of `docker compose up` by `profiles: ["tools"]` (`docker compose run` enables the profile itself). Shares the ingester's image (via the `*haiku_rag_image` alias), its `rag/db` + `rag/docs` mounts, its environment and its `ingester_db_password` + `ingester_bearer` secrets; everything after the service name is passed to the CLI: `docker compose run --rm haiku-rag info --db /data/haiku.rag.lancedb`. Its entrypoint exports both secrets first — see "Ingester control plane auth" below. Reads are safe while the stack is up; writing a store the ingester owns needs `docker compose stop haiku-ingester` first.
- **docling-serve** — stateless document converter. Runs the upstream `-cpu` image directly (no local build);  its variant/version are chosen at generation and baked into `docker-compose.yml`:  re-point (a GPU-capable variant, another registry) by editing that line and committing — see `docling-serve/README.md` for the variants, their sizes, the tagging policy, and a local-build recipe.
- **postgres** — application databases created on first boot by `postgres/config/init.sh`: `soliplex_agui` (thread persistence), `soliplex_authz` (authorization policy)${", `soliplex_gitea` (Gitea backing store)" if include_gitea else ""}, `soliplex_ingester` (haiku-ingester job queue). Each gets a dedicated low-privilege role whose password is read from `/run/secrets/<name>_db_password`. Init runs only on an empty data volume; to re-run, `docker compose down -v`.
% if include_gitea:
- **gitea** — rootless Gitea (`docker.gitea.com/gitea:*-rootless`) backed by the `soliplex_gitea` database, reverse-proxied by nginx at `https://localhost:9443/gitea/` (HTTPS only; override via `GITEA_ROOT_URL`). State lives in the `gitea_data` / `gitea_config` named volumes${"; built-in SSH published on host `:" + str(gitea_ssh_port) if gitea_ssh_port else "; built-in SSH unpublished (set `gitea_ssh_port` for git-over-SSH clones from the host)"}. Provision an admin user, access token, and tracking repo with `scripts/init_gitea.py` (a rotating service account whose password is never persisted; pass `--admin-user NAME` to also create a distinct, known web-UI admin login, prompted for its password). Pass `--push-to-gitea` to also set this stack's git `origin` to a Gitea repo over SSH and push the initial commit.
% endif

### Secrets

Two modes, documented at the bottom of `docker-compose.yml`:

- **File-based (active):** `.secrets/*.gen` created by `scripts/generate_secrets.py` and mounted as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored.
- **Env-var (commented):** uncomment the env-var secret blocks and set `SOLIPLEX_*` vars.

Don't hand-edit `.secrets/*.gen` — re-run the script. Destroying those files after the Postgres volume exists will break backend auth to the DB; you must also `down -v` and re-init.

The `.secrets/*.gen` files are mode `0600`. They're readable in-container because every built image runs as `PUID:PGID` (compose `build.args`, sourced from `.env`; default `1000:1000`) and `generate_secrets.py` makes the files owned by `PUID:PGID` (re-owning via a throwaway container when the operator's uid differs). This repo has no committed `.env`, so its own stack defaults to `1000:1000`; the generator (`skill/scripts/generate_soliplex_project.py`) seeds `PUID`/`PGID` from the host operator and writes them into the generated project's `.env`. Changing the uid means a `docker compose build` (it's baked in at build time).

### Soliplex configuration layout (`backend/environment/`, bind-mounted to `/environment`)

- `installation.yaml` — the top-level Soliplex install config (agents, secrets, environment vars, room list, skills, DB URIs, upload/sandbox paths). Start here when reasoning about backend behavior. The file is heavily commented with pointers to <https://soliplex.github.io/soliplex/config/> — those comments describe defaults, so a section being empty/absent is not the same as being unconfigured.
- `rooms/<name>/room_config.yaml` — per-room agent prompts, tools, skills. `room_paths` in `installation.yaml` points at `./rooms`, so every `rooms/<name>/room_config.yaml` is loaded; to exclude a room, remove its directory or replace `./rooms` with an explicit list of the wanted rooms. `room_paths` also lists `/app/src/rooms-provisioned`: rooms created on request by the provisioner land in `src/rooms-provisioned/`, a git clone of its own that the provisioner alone writes (the backend sees it through its read-only `src/` mount).
- `skills/<name>/` — filesystem skills discovered via `filesystem_skills_paths`. Must also be declared in `skill_configs` to be enabled.
- `completions/`, `quizzes/`, `oidc/`, `logging.yaml`, `haiku.rag.yaml` — feature-specific configs referenced from `installation.yaml`.

### Sandbox (code execution for agents)

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build time, so adding/changing a sandbox env requires a `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents at runtime. Gitignored.
- Running code is opt-in (generator parameter `enable_sandbox`). The backend image ships `bwrap` (`bubblewrap`), but inside a container it also needs `seccomp=unconfined`, `apparmor=unconfined` and `systempaths=unconfined` (user namespaces plus a fresh `/proc`; lifting fewer than all three still fails). The `security_opt` block that lifts them sits on `backend` (and on `soliplex-dev`, which must match) in `docker-compose.yml`: live when the stack was generated with `enable_sandbox: true`, commented out otherwise. Without it, soliplex (>= 0.84) refuses to run a room that configures the `bubble-sandbox` skill ("Sandbox unavailable"), and `add_room.py` refuses to add one.

### RAG pipeline

`rag/db/` is the single source of truth for vector data and is mounted by **two** services:
- `haiku-ingester` is the writer (`/data`). Its job queue is a Postgres database (`soliplex_ingester`), not a file under `rag/db/`.
- `backend` reads it via the `haiku.rag.skills.rag` skill (`/db`, configured by `RAG_LANCE_DB_PATH` env var in `installation.yaml`).

One-off CLI work on a store (`info`, `migrate`, building a separate static database) runs on the `haiku-rag` service; `skills/soliplex-template/scripts/rag_db.py` drives it for the static-database case.

To add documents, drop files into `rag/docs/` — the FS source of the `ingester.databases` entry keyed `haiku.rag` in `haiku.rag/haiku.rag.yaml` will pick them up on its next poll. To add other sources (S3, HTTP, WebDAV), append entries under the `ingester.databases` entry keyed `haiku.rag` in the same file and restart the ingester.

The ingester (and the one-shot `haiku-rag` service) also mounts room uploads, `backend/uploads/rooms/`, read-only at `/uploads/rooms`: a room with its own database ingests from `/uploads/rooms/<room_id>/`.

## Gotchas

- `backend/Dockerfile` builds `FROM ghcr.io/soliplex/soliplex-postgres:${soliplex_image_tag}`. A `<major>.<minor>` tag moves: `docker compose build --pull backend` picks up its latest patch release, and an exact `<major>.<minor>.<patch>` pins one. Changing the tag, or pulling a new base image, is a backend rebuild (and a `soliplex-dev` one, which builds its own copy). Extra PyPI packages go in `backend/requirements.txt`, installed with the image's own versions frozen as constraints.${" (The opt-in TUI's `tui/Dockerfile` builds on `ghcr.io/soliplex/soliplex-tui` at the same tag.)" if include_tui else ""}
- The backend migrates `soliplex_agui` / `soliplex_authz` to the pinned release's Alembic head itself, on startup (each app role owns its schema, so `installation.yaml` sets no `migration_dburi` / `migration_policy`). The exception: databases created by soliplex <= 0.81 carry no `alembic_version` stamp, and 0.82+ refuses to start on them (`UnstampedDatabase`) until soliplex's one-time `scripts/bootstrap_alembic_version.py` has stamped them. `uv run scripts/migrate_soliplex_dbs.py --check` reports what a rebuilt backend image would demand; with `backend` stopped, the same command minus `--check` stamps and upgrades both. The ingester's `soliplex_ingester` queue is not Alembic-managed: the Compose command runs `haiku-ingester queue migrate` before `serve`. It uses namespace `soliplex`; upgrading a pre-0.97 queue also requires assigning existing rows to that namespace (see `docs/users/operations/migrations.md`).
- The `haiku-ingester` image tag
  `ghcr.io/ggozad/haiku.rag-slim:${haiku_rag_version}` in `docker-compose.yml` (the
  `&haiku_rag_image` anchor the one-shot `haiku-rag` service aliases)
  is not a free choice: the pinned `soliplex` release constrains
  `haiku.rag-slim` as a dependency, and the ingester writes the LanceDB the backend reads — check that constraint before changing the tag. Most bumps need nothing more, but a release that changed the LanceDB layout (haiku.rag ships a store upgrade only for those, and checks a store against that set rather than against the release number) leaves every store under `rag/db/` needing a one-off migration, without which the ingester crash-loops and reads fail. Check with `uv run scripts/migrate_rag_dbs.py --check`, then apply with the same command minus `--check` and
`haiku-ingester` stopped. A locally built ingester image (a patched
build) tags itself as the anchor value: see `haiku.rag/README.md`.
- A docling-serve release pairs with the pinned `haiku.rag-slim`
  release (the conversion schema they were exercised against);  the
  converter's image is baked into `docker-compose.yml` at generation
  and moves in step with the ingester's tag through the upgrade
  flow. Most docling bumps shift nothing (conversion output changes
  shift chunk boundaries like any other — see
  `migrate_rag_dbs.py --check`).
- The `embeddings:` block (model name, provider, `vector_dim`) appears in both `haiku.rag/haiku.rag.yaml` (ingester and one-shot `haiku-rag` service) and `backend/environment/haiku.rag.yaml` (backend), and the two must agree, because both open the same LanceDB. The ingester's copy is not optional: without it haiku.rag uses its own default (`qwen3-embedding:4b` / 2560), which happens to match the template's default and so hides the gap (#218). A store records its vector dimension and won't open under a different one, so changing the model on an existing store means `haiku-rag rebuild --embed-only` (see `docs/users/operations/rag.md`). In `scripts/refresh_skill_template.py`, both transforms apply the shared `EMBEDDING_SUBS`, so `rag_embed_model` / `rag_embed_dim` reach both files.
- `nginx/Dockerfile` copies the web client from `ghcr.io/soliplex/frontend-web:${frontend_version}`. A `<major>.<minor>` tag moves: `docker compose build --pull nginx` picks up its latest patch release, and an exact `<major>.<minor>.<patch>` (or `<major>.<minor>.<patch>-<build>`, the release tag's `+<build>` spelled `-`) pins one. The tree is already cache-busted: its assets sit under a `v<release-tag>/` directory, which `nginx/nginx.conf` caches for a year (its location pattern has to accept the tag's `+<build>`), while `index.html` is never cached.
- Backend `--no-auth-mode` is explicitly labeled temporary in `docker-compose.yml`. Don't assume auth is enforced end-to-end in this template.
- `docker compose down -v` drops the `postgres_data` volume — all chat threads, authz grants, and the ingester's job queue (its own `soliplex_ingester` database) go with it. The RAG vector store under `rag/db/` (bind mount, not the postgres volume) is separate, so a `down -v` doesn't touch it.

### Ingester control plane auth

`haiku-ingester` binds the control plane on `0.0.0.0:8765` so the host port mapping works. With a non-loopback bind haiku.rag requires a bearer token (`ingester.api.auth_token`); otherwise the API can cancel jobs, retry from the DLQ, and trigger source refreshes from anywhere the port is reachable.

The token is the file-based Docker secret `ingester_bearer` (`.secrets/ingester_bearer.gen`, generated by `scripts/generate_secrets.py` like the DB passwords) — never an environment variable or `.env` entry, so it stays invisible to `docker inspect` env output and rotates by regenerating the file and restarting the consuming services.

How it gets in:

- `haiku.rag/haiku.rag.yaml` has `ingester.api.auth_token: <%text>${INGESTER_TOKEN}</%text>`. haiku.rag's YAML loader expands `<%text>${VAR}</%text>` / `<%text>${VAR:-default}</%text>` itself (since 0.57), so the ingester reads the bind-mounted config directly — but interpolation reads the **environment**, not files.
- The `haiku-ingester` command uses `sh -ec` to export `INGESTER_DB_PASSWORD` *and* `INGESTER_TOKEN` from their Docker secrets, run `queue migrate`, and then start `serve --root-path /ingester`. The whole config is expanded on load (the queue `dburi` interpolates too), so both exports must precede it; a missing or empty secret fails loudly at startup instead of leaving the control plane unauthenticated.
- That export is why one-off CLI calls go through the **`haiku-rag`** service, whose entrypoint does the same two exports. `docker compose run` *replaces* a service's command, so running the CLI on `haiku-ingester` loses the exports and the config fails to load (`MissingEnvVarError`) before the subcommand starts.
- nginx also mounts `ingester_bearer` and injects the bearer itself for anything it proxies under `/ingester/`: its service command `sed`-renders the `@@INGESTER_BEARER@@` placeholders out of the bind-mounted `nginx.conf` into `/tmp/nginx.conf` at start (nginx cannot env-interpolate its config, nor read secret files inside location blocks), so browser users — and the dashboard's in-page fetches — never type the token. In `auth_mode: auth` stacks the proxy also gates `/ingester/` on the backend's admin check (`auth_request` against `/_authz_check`), so only admins reach the surface at all.

Two ways in for clients:

- **Through nginx** (the HTTP/HTTPS server, at `/ingester/`): just use it — the token is injected at the proxy, and in auth-mode stacks the soliplex admin check gates the route.
- **Direct** (host port ${ingester_port}): send `Authorization: Bearer $(cat .secrets/ingester_bearer.gen)` yourself, e.g.
  `curl -fsS -H "Authorization: Bearer $(cat .secrets/ingester_bearer.gen)" http://localhost:${ingester_port}/stats`. The haiku.rag-side 401 is `{"detail": "Unauthorized"}`.

The dashboard served at `/ingester/` is plain HTML; the control plane's API is reached only with the bearer — the browser gets it because nginx injects it. Keep that in mind when running the dashboard behind any *other* proxy: it must inject the same header, or the dashboard falls back to its paste-token prompt (the in-page JS still asks when a fetch 401s).

The token still cannot carry a literal `$` — compose's `$$` escape and haiku.rag's interpolation make writing one easier than reading it — but generated tokens are simply alphanumeric (`scripts/generate_secrets.py`), like every other `.gen` secret.
