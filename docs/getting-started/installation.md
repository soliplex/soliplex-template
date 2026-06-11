---
icon: lucide/download
---

# Installation

This guide runs the stack straight from a clone of the template. (For a
customized stack, see [Generate a custom project](generator.md).)

## Prerequisites

- **Docker** with the **Compose** plugin (`docker compose version`).
- An **Ollama** server reachable from the containers, serving the models
  referenced in `backend/environment/installation.yaml` (the defaults use
  `gpt-oss:*` chat/title models and a `qwen3-embedding` model for RAG). You set
  its URL in step 3.
- [`uv`](https://docs.astral.sh/uv/) to run the secrets script
  (`scripts/generate_secrets.py`).

## 1. Clone the template

```bash
git clone https://github.com/soliplex/soliplex-template.git
cd soliplex-template
```

## 2. Generate secrets

The Postgres roles and Soliplex backend read their credentials from Docker
secrets mounted at `/run/secrets/*`. Generate them before the first `up`:

```bash
uv run scripts/generate_secrets.py   # populates .secrets/*.gen (gitignored)
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Deleting these files after the Postgres volume
    already exists breaks the backend's auth to the database — you would also
    need `docker compose down -v` and a re-init. See [Secrets](../operations/secrets.md).

## 3. Set `OLLAMA_BASE_URL`

`.env` must point at your Ollama server:

```bash
# .env
OLLAMA_BASE_URL=http://your-ollama-host:11434
```

`.env` is gitignored. You can also set `INGESTER_TOKEN` here to override the
weak default ingester control-plane token — see
[Ingester control plane](../operations/ingester.md).

## 4. Bring the stack up

```bash
docker compose up        # foreground
docker compose up -d     # detached
```

The first run builds the `nginx` and `backend` images and initializes the
Postgres databases; it takes a few minutes. Subsequent runs are fast.

## Exposed ports

Compose publishes these host ports (the left column is the host side):

| Port | Service | Purpose |
|------|---------|---------|
| `9000` | nginx | HTTP — the web frontend |
| `9443` | nginx | HTTPS (self-signed cert) |
| `8000` | backend | Soliplex backend, direct |
| `8765` | haiku-ingester | Control plane: `/health`, `/jobs`, `/sources`, `/dlq`, `/stats`, dashboard at `/` |
| `5001` | docling-serve | Document converter |
| `5432` | postgres | Database |

## Verify the stack

```bash
docker compose ps                        # all services healthy/running
curl -fsS http://localhost:8765/health   # ingester control plane
docker compose logs -f backend           # follow backend startup
```

Then open <http://localhost:9000> for the web frontend.

## Using the TUI

Soliplex includes an interactive terminal client. The backend image bundles it,
so you can run it against the running stack without installing anything on the
host:

```bash
docker compose exec backend soliplex-tui --url http://localhost:8000
```

This template also serves the same client as a web app via the optional `tui`
service; nginx proxies it at <https://localhost:9443/tui/>. A project from the
[generator](generator.md) includes that service only when scaffolded with
`include_tui=true` — but the command-line client above works either way.

## Everyday commands

```bash
docker compose build <service>   # rebuild one image (backend, nginx, postgres, …)
docker compose logs -f backend
docker compose down              # stop (keeps the postgres_data volume)
docker compose down -v           # stop AND wipe the postgres volume
```

!!! danger "`down -v` is destructive"
    `docker compose down -v` drops the `postgres_data` volume — chat threads,
    authorization grants, and the ingester's job queue (now its own Postgres
    database) all go with it. The RAG vector store and your documents live
    under `rag/db/` (a bind mount), so they survive a `down -v`.

## Notes

- The backend is launched with `--no-auth-mode` in this template (marked
  temporary in `docker-compose.yml`). Don't assume auth is enforced end-to-end.
- The `--reload=config` flag means edits under `backend/environment/` take
  effect without a rebuild.
- The frontend is pulled from a `soliplex/frontend` GitHub release inside
  `nginx/Dockerfile`; the backend `soliplex` version is pinned in
  `backend/constraints.txt`. Changing either is a rebuild of that image.
