---
icon: lucide/network
---

# Service graph

The stack is defined in `docker-compose.yml`. Five services cooperate; two of
them share the RAG vector store through a bind mount.

```mermaid
graph LR
  browser([Browser]) -->|${nginx_http}/${nginx_https}| nginx
  nginx -->|/api/ /mcp/| backend
  backend -->|reads LanceDB| ragdb[(rag/db)]
  ingester[haiku-ingester] -->|writes LanceDB| ragdb
  ingester -->|convert/chunk| docling[docling-serve]
  ingester -->|job queue| postgres[(postgres)]
  backend -->|threads + authz| postgres
```

<%text>## nginx</%text>

Serves the Flutter web frontend (built from a `soliplex/frontend` release in
`nginx/Dockerfile`) and reverse-proxies `/api/` and `/mcp/` to `backend:8000`.
Terminates TLS on host port ${nginx_https} with a self-signed cert.

<%text>## backend</%text>

Runs `soliplex-cli serve /environment`, installed from the pinned `soliplex`
package (see [Backend image & dependencies](backend.md)). The `--reload=config`
flag means edits under `backend/environment/` take effect without a rebuild.

<%text>## haiku-ingester</%text>

The **writer** for the LanceDB at `rag/db/`. Runs `haiku-ingester serve` with a
Postgres-backed job queue (its own `soliplex_ingester` database), an async
worker pool, retries + a dead-letter queue, and an HTTP control plane on host
port ${ingester_port}. There is a **single-writer constraint**: only one
ingester per LanceDB. The backend reads the same store through a bind mount.
See [RAG pipeline](../operations/rag.md).

<%text>## docling-serve</%text>

A stateless document converter (CPU image by default; a GPU variant is a
commented swap in `docker-compose.yml`).
% if include_tui:

<%text>## tui</%text>

Soliplex's [Textual](https://textual.textualize.io/) terminal client, served
as a web app over textual-serve; nginx proxies it at `/tui/`, so open
<https://${server_name}:${nginx_https}/tui/>. The same client is bundled in
the backend image — to run it from the command line, see
[Using the TUI](../getting-started/installation.md#using-the-tui).
% endif

<%text>## postgres</%text>

Creates two databases on first boot via `postgres/config/init.sh`:
`${agui_db}` (thread persistence) and `${authz_db}` (authorization policy).
Each gets a dedicated low-privilege role whose password is a Docker secret.
Init runs **only on an empty data volume**; to re-run it,
`docker compose down -v` first.
