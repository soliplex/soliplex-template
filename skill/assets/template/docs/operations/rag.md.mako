---
icon: lucide/database
---

# RAG pipeline

`rag/db/` is the single source of truth for vector data. It is mounted by
**two** services:

- **haiku-ingester** is the writer (`/data`); it also owns the persistent
  SQLite job queue.
- **backend** reads it via the `haiku.rag.skills.rag` skill.

Only one ingester may write a given LanceDB (the single-writer constraint).

<%text>## Adding documents</%text>

Drop files into `${docs_dir}/`. The filesystem source in
`haiku.rag/haiku.rag.yaml` picks them up on its next poll, and docling-serve
converts and chunks them.

<%text>## Adding other sources</%text>

To pull from S3, HTTP, WebDAV, and the like, append entries under
`ingester.sources` in `haiku.rag/haiku.rag.yaml` and restart the ingester:

```bash
docker compose restart haiku-ingester
```

<%text>## Operating the pipeline</%text>

The ingester exposes a control plane on host port ${ingester_port} — `/health`,
`/jobs`, `/sources`, `/dlq`, `/stats`, and a dashboard at `/`. It requires a
bearer token; see [Ingester control plane](ingester.md).
