---
icon: lucide/database
---

# RAG pipeline

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

`rag/db/` is the single source of truth for vector data. It is mounted by
**two** services:

- **haiku-ingester** is the writer (`/data`); its job queue is a Postgres
  database (`soliplex_ingester`), not a file under `rag/db/`.
- **backend** reads it via the `haiku.rag.skills.rag` skill.

Only one ingester may write a given LanceDB (the single-writer constraint).

## Adding documents

Drop files into `rag/docs/`. The filesystem source in
`haiku.rag/haiku.rag.yaml` picks them up on its next poll, and docling-serve
converts and chunks them.

## Adding other sources

To pull from S3, HTTP, WebDAV, and the like, append entries under
`ingester.sources` in `haiku.rag/haiku.rag.yaml` and restart the ingester:

```bash
docker compose restart haiku-ingester
```

## A separate, static database

The ingester only maintains **one** LanceDB. For a *separate*, mostly-static
corpus, build a standalone database with the `haiku-rag` CLI via a one-off
`docker compose run` on the `haiku-ingester` service — it already mounts
`rag/db`, carries `OLLAMA_BASE_URL`, and ships the same `haiku.rag.yaml`. Point
it at a **different** `--db` so it never collides with `haiku.rag.lancedb`:

```bash
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb init
docker compose run --rm --no-TTY haiku-ingester \
    haiku-rag --config /app/haiku.rag.yaml --db /data/handbook.lancedb \
    add-src /docs/handbook/
```

`add-src` also takes a URL or `s3://` URI; for a local path outside
`rag/docs/`, add `-v /abs/path:/src:ro` and point `add-src` at `/src`. Use
`rebuild` to re-index and `delete <id>` to remove a document. The database
lands at `rag/db/handbook.lancedb`, which the backend already reads through its
`rag/db` mount. Wire a room to it with `rag_lancedb_stem: "handbook"` in that
room's `room_config.yaml`.

## Operating the pipeline

The ingester exposes a control plane on host port 8765 — `/health`,
`/jobs`, `/sources`, `/dlq`, `/stats`, and a dashboard at `/`. It requires a
bearer token; see [Ingester control plane](ingester.md).
