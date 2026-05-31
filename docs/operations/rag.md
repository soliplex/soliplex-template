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

## Operating the pipeline

The ingester exposes a control plane on host port `8765` — `/health`, `/jobs`,
`/sources`, `/dlq`, `/stats`, and a dashboard at `/`. Use it to watch jobs,
retry from the dead-letter queue, and trigger source refreshes. It requires a
bearer token; see [Ingester control plane](ingester.md).
