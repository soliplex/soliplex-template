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
- [`uv`](https://docs.astral.sh/uv/) to run the secrets script
  (`scripts/generate_secrets.py`).

<%text>## 1. Generate secrets</%text>

The Postgres roles and the backend read their credentials from Docker secrets.
Generate them before the first `up`:

```bash
uv run scripts/generate_secrets.py   # populates .secrets/*.gen (gitignored)
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
% if include_gitea:
| `3000` | gitea | Gitea HTTP [^1] |
| `2222` | gitea | Gitea SSH |

[^1]: nginx also serves Gitea at `/gitea/` on the HTTPS port.
% endif

(Container-internal ports are fixed; these are the host-published sides.)

<%text>## Verify the stack</%text>

```bash
docker compose ps
curl -fsS http://localhost:${ingester_port}/health
docker compose logs -f backend
```

Then open <http://localhost:${nginx_http}> for the web frontend.

% if include_gitea:

<%text>## Provision Gitea</%text>

This stack includes a local Gitea, reverse-proxied at `/gitea/`. After the
stack is up, provision it:

```bash
uv run scripts/init_gitea.py
```

This creates a rotating service account (its password is never persisted).
Useful flags:

- `--admin-user NAME` — also create a distinct web-UI admin login (you are
  prompted for its password).
- `--push-to-gitea` — register your SSH key(s), create a repo, set this stack's
  git `origin` to it, and push the initial commit.

Open Gitea at <https://${server_name}:${nginx_https}/gitea/>.

% endif

<%text>## Using the TUI</%text>

Soliplex includes an interactive terminal client. The backend image bundles it,
so you can run it against the running stack without installing anything on the
host:

```bash
docker compose exec backend soliplex-tui --url http://localhost:8000
```

% if include_tui:
This stack also serves the same client as a web app via the `tui` service;
nginx proxies it at <https://${server_name}:${nginx_https}/tui/>.
% endif

<%text>## Everyday commands</%text>

```bash
docker compose build <service>   # rebuild one image (backend, nginx, …)
docker compose down              # stop (keeps the postgres_data volume)
docker compose down -v           # stop AND wipe the postgres volume
```

!!! danger "`down -v` is destructive"
    `docker compose down -v` drops the `postgres_data` volume — chat threads,
    authorization grants, and the ingester's job queue (now its own Postgres
    database) all go with it. The RAG vector store and your documents live
    under `rag/db/` (a bind mount), so they survive a `down -v`.
