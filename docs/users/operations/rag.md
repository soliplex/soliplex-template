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

Drop files into `${docs_dir}/`. The filesystem source in
`haiku.rag/haiku.rag.yaml` picks them up on its next poll, and docling-serve
converts and chunks them.

To ingest only part of what lands there, set `ignore_patterns` /
`include_patterns` (gitignore-style) on that source. Note that a non-empty
`include_patterns` is exclusive — anything it does not match is skipped.

## Adding other sources

To pull from S3, HTTP, WebDAV, and the like, append entries under
the `ingester.databases` entry keyed `haiku.rag`, in
`haiku.rag/haiku.rag.yaml`, and
restart the ingester:

```bash
docker compose restart haiku-ingester
```

## A separate, static database

For a separate, mostly-static corpus, build a standalone database with the
`haiku-rag` CLI through the
`haiku-rag` service — the stack's one-shot CLI runner, which shares the
ingester's image, its `rag/db` and `rag/docs` mounts, its `OLLAMA_BASE_URL` and
its `haiku.rag.yaml`. Point it at a **different** `--db` so it never collides
with `haiku.rag.lancedb`:

```bash
docker compose run --rm --no-TTY haiku-rag init --db /data/handbook.lancedb
docker compose run --rm --no-TTY haiku-rag add-src /docs/handbook/ \
    --db /data/handbook.lancedb
```

Everything after the service name goes to `haiku-rag`. Do not run the CLI on
the `haiku-ingester` service instead: `docker compose run` replaces that
service's command, which is the only thing that exports the queue password
`haiku.rag.yaml` interpolates, and the config then fails to load before the
subcommand runs.

`add-src` also takes a URL or `s3://` URI; for a local path outside
`${docs_dir}/`, add `-v /abs/path:/src:ro` and point `add-src` at `/src`. Use
`rebuild` to re-index and `delete <id>` to remove a document. The database
lands at `rag/db/handbook.lancedb`, which the backend already reads through its
`rag/db` mount. Wire a room to it with `rag_lancedb_stem: "handbook"` in that
room's `room_config.yaml`.

## Ingesting multiple databases

One ingester can maintain several databases with the same queue and control
plane. In `haiku.rag/haiku.rag.yaml`, list their locations under
`lancedb.databases` and their sources under `ingester.databases`. Keep the
default database in both maps when adding another:

```yaml
lancedb:
  databases:
    haiku.rag: /data/haiku.rag.lancedb
    handbook: /data/handbook.lancedb

ingester:
  # Keep the existing queue and api sections here.
  databases:
    haiku.rag:
      sources:
        - type: fs
          id: local-docs
          root: /docs
          ignore_patterns: ["handbook/**"]
          delete_orphans: true
    handbook:
      sources:
        - type: fs
          id: handbook-docs
          root: /docs/handbook
          delete_orphans: true
```

Source ids must be unique across databases. Each database must have only
one writer. Point a room at `handbook` with `rag_lancedb_stem: "handbook"`.
Moving a source does not remove documents already stored in another database.

Apply database and source changes without restarting the other databases:

```bash
docker compose kill -s SIGHUP haiku-ingester
```

`POST /reload` is the HTTP equivalent. `GET /databases/{name}` reports a
database's details. Changes outside `lancedb.databases` and
`ingester.databases`, such as embeddings or queue settings, require a restart.

## Migrating a store

Most `haiku.rag-slim` bumps need nothing, but a release that changed the store
layout leaves every LanceDB written before it needing a one-off upgrade before
haiku.rag will open it. See [Database migrations](migrations.md) for how to
ask which case a bump is.

## Changing the embedding model

The embedding model is set in **two** files, and they must agree:
`haiku.rag/haiku.rag.yaml` configures the ingester (and the one-shot
`haiku-rag` service), and `backend/environment/haiku.rag.yaml` configures the
backend. Both open the same LanceDB, and a store records the vector dimension
it was written with, so it refuses to open under a different one:

```text
Database configuration is incompatible with current settings:
  - Stored (db) embedding vector dimension 2560 -> Environment
    (current) embedding vector dimension 1024
```

When that check fails, the ingester crash-loops. Stacks generated before
soliplex-template#218 was fixed are affected even if the model was never
changed. In those stacks the ingester's file has no `embeddings:` section, so
it fell back to haiku.rag's default (`qwen3-embedding:4b`, 2560 dimensions),
whatever `rag_embed_model` said.

To switch models, or to repair one of those stacks:

1. Set the same `embeddings:` block in both files:

    ```yaml
    embeddings:
      model:
        name: qwen3-embedding:0.6b
        provider: ollama
        vector_dim: 1024
    ```

2. Stop the writer: `docker compose stop haiku-ingester`.
3. Bring the store in line with the new model. Check whether it holds any
   documents (`info` reports the row count of each table):

    ```bash
    docker compose run --rm --no-TTY haiku-rag info \
        --db /data/haiku.rag.lancedb
    ```

    An **empty** store can just be moved aside, and the ingester creates a
    fresh one on start:
    `mv rag/db/haiku.rag.lancedb rag/db/haiku.rag.lancedb.old`.

    A store **holding documents** needs its existing chunks re-embedded with
    the new model. This drops and recreates the chunks table at the new
    dimension, and does not re-convert the source documents:

    ```bash
    docker compose run --rm --no-TTY haiku-rag rebuild --embed-only \
        --db /data/haiku.rag.lancedb
    ```

    Do the same for every other store under `rag/db/` that a room reads: one
    query is embedded once, with one model.

4. Restart: `docker compose start haiku-ingester`, then
   `docker compose restart backend`.
5. Jobs that failed under the old configuration, for example with a 404 for a
   model the Ollama server doesn't have, sit in the dead-letter queue. List
   them with `GET /dlq`, then retry each one with `POST /dlq/{id}/retry`, or
   let the next source sweep queue them again. Both calls need the bearer
   token; see [Ingester control plane](ingester.md).

## Searching several databases at once

A room's RAG skill or `search_documents` tool can name more than one database
under `rag_databases`, and a search then covers them as one set:

```yaml
rag_databases:
  - rag_lancedb_stem: "haiku.rag"
  - rag_lancedb_stem: "handbook"
```

Each entry carries exactly one of `rag_lancedb_stem` or
`rag_lancedb_override_path`, plus an optional `name` — the identity reported in
search results, documents and citations, defaulting to the stem that placed it.
Names must be unique, and every database in the list must have been written
with the **same embedding model**, since one query is embedded once for all of
them. A bare `rag_lancedb_stem` on the skill is shorthand for a one-entry list.

## Operating the pipeline

The ingester exposes a dashboard and control plane API behind nginx at
`/ingester/`. All requests under that path require the bearer token.
