---
icon: lucide/database
---

# RAG pipeline

`rag/db/` is the single source of truth for vector data. It is mounted by
**two** services:

- **haiku-ingester** is the writer (`/data`). It also owns `/data/ingester.db`,
  the persistent SQLite job queue.
- **backend** reads it via the `haiku.rag.skills.rag` skill (`/db`, configured by
  the `RAG_LANCE_DB_PATH` env var in `installation.yaml`).

Only one ingester may write a given LanceDB (the single-writer constraint).
Because the backend reads the same store through a bind mount, no separate MCP
server is needed.

## Adding documents

Drop files into `rag/docs/`. The filesystem source under `ingester.sources` in
`haiku.rag/haiku.rag.yaml` picks them up on its next poll, and docling-serve
converts and chunks them.

## Adding other sources

To pull from S3, HTTP, WebDAV, and the like, append entries under
`ingester.sources` in `haiku.rag/haiku.rag.yaml` and restart the ingester:

```bash
docker compose restart haiku-ingester
```

## A separate, static database

The continuous ingester only maintains **one** LanceDB (the single-writer
constraint). When you want a *separate* corpus — say one room per knowledge base
whose sources rarely change — build a standalone database with the `haiku-rag`
CLI instead of standing up a second ingester. Reuse the `haiku-ingester` service
via `docker compose run`: it already mounts `rag/db` at `/data`, carries
`OLLAMA_BASE_URL`, and ships the same `haiku.rag.yaml` (so the new database's
embeddings and chunking match). Just point it at a **different** `--db` so it
never collides with the ingester's `haiku.rag.lancedb`:

```bash
# create + populate (a directory already under rag/docs/)
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb init
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb \
    add-src /docs/handbook/

# later: add more, re-index, or remove a document
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb rebuild
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb delete <id>
```

`add-src` also takes a URL or `s3://` URI directly; for a local path outside
`rag/docs/`, add `-v /abs/path:/src:ro` to the `run` and point `add-src` at
`/src`. The new database lands at `rag/db/handbook.lancedb`, which the backend
already sees through its `rag/db → /db` mount. Wire a room to it by setting its
LanceDB stem:

```yaml
# rooms/<room>/room_config.yaml
skills:
  skill_configs:
    - kind: "haiku.rag.skills.rag"
      rag_lancedb_stem: "handbook"
```

## Operating the pipeline

The ingester exposes a control plane on host port `8765` — `/health`, `/jobs`,
`/sources`, `/dlq`, `/stats`, and a dashboard at `/`. Use it to watch jobs,
retry from the dead-letter queue, and trigger source refreshes. It requires a
bearer token; see [Ingester control plane](ingester.md).
