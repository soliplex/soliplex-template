---
icon: lucide/download
---

# Installation

This project was scaffolded by the `soliplex-template` generator, so most setup
is already done. Bringing the stack up takes three steps.

<%text>## Prerequisites</%text>

- **Docker** with the **Compose** plugin (`docker compose version`).
- An **Ollama** server reachable from the containers, serving the models named
  in `backend/environment/installation.yaml`. Its URL is recorded in `.env` as
  `OLLAMA_BASE_URL` (see step 2).

<%text>## 1. Generate secrets</%text>

The Postgres roles and the backend read their credentials from Docker secrets.
Generate them before the first `up`:

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Deleting these files after the Postgres volume
    already exists breaks the backend's auth to the database. See
    [Secrets](../operations/secrets.md).

The generated `.env` records `PUID` / `PGID` — the uid/gid the containers run
as and that owns these secret files (defaulted to the operator who scaffolded
the project). If you run services as a different account, set them explicitly
and rebuild; see [Secrets](../operations/secrets.md).

<%text>## 2. Confirm `OLLAMA_BASE_URL`</%text>

The generator wrote `.env` with the `OLLAMA_BASE_URL` you supplied. Confirm it
points at your Ollama server and adjust if needed. You can also set
`INGESTER_TOKEN` there — see
[Ingester control plane](../operations/ingester.md).

<%text>## 3. Bring the stack up</%text>

```bash
docker compose up        # foreground
docker compose up -d     # detached
```

The first run builds the `nginx` and `backend` images and initializes Postgres;
it takes a few minutes. Subsequent runs are fast.

<%text>## Exposed ports</%text>

| Port | Service | Purpose |
|------|---------|---------|
| `${nginx_http}` | nginx | HTTP — the web frontend |
| `${nginx_https}` | nginx | HTTPS (self-signed cert) |
| `8000` | backend | Soliplex backend, direct |
| `${ingester_port}` | haiku-ingester | Control plane + dashboard |
| `${docling_port}` | docling-serve | Document converter |
| `${postgres_port}` | postgres | Database |

(Container-internal ports are fixed; these are the host-published sides.)

<%text>## Verify the stack</%text>

```bash
docker compose ps
curl -fsS http://localhost:${ingester_port}/health
docker compose logs -f backend
```

Then open <http://localhost:${nginx_http}> for the web frontend.

<%text>## Everyday commands</%text>

```bash
docker compose build <service>   # rebuild one image (backend, nginx, …)
docker compose down              # stop (keeps the postgres_data volume)
docker compose down -v           # stop AND wipe the postgres volume
```

!!! danger "`down -v` is destructive"
    `docker compose down -v` drops the `postgres_data` volume — all chat
    threads and authorization grants go with it. The ingester's SQLite job
    queue lives under `rag/db/` (a bind mount), so it survives a `down -v`.
