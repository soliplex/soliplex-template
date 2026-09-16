---
icon: lucide/database-backup
---

# Database migrations

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

## The RAG vector store (LanceDB)

Each LanceDB under `rag/db/` records the haiku.rag version that last wrote it,
and haiku.rag ships an *upgrade* only for those few releases that changed the
store layout. Opening a store checks it against that set of upgrades rather
than against the release number, so **most `haiku.rag-slim` bumps need
nothing** — including bumps that skip several releases, as long as they cross
none of those upgrades.

When a bump does step over one, every store written before it has to be
migrated, and until that happens the pipeline is down rather than degraded: the
ingester crash-loops at startup and every read fails, the backend's included,
so rooms lose RAG search. Opening a store written by 0.56.0 with 0.82.1 — which
crosses three upgrades — reports:

```text
Error: Database requires migration from 0.56.0 to 0.82.1.
3 migration(s) pending. Run 'haiku-rag migrate' to upgrade.
```

A store created by the pinned image is current by definition, so a stack that
has only ever run one release has nothing to migrate.

### Ask, rather than assume

`scripts/migrate_rag_dbs.py` puts the question to the *pinned* image's
`haiku-rag` CLI for every `*.lancedb` under `rag/db/`. So bump the tag first —
in **both** `docker-compose.yml` and `haiku.rag/Dockerfile`, which have to
agree — and then ask what it would demand:

```bash
uv run scripts/migrate_rag_dbs.py --check
```

The answer for most bumps, and the one worth confirming rather than guessing:

```text
✓ haiku.rag.lancedb: up to date
✓ handbook.lancedb: up to date
```

When a bump does cross an upgrade, each store reports what it needs, in
haiku.rag's own words, one line per pending upgrade:

```text
! haiku.rag.lancedb: 3 migration(s) pending
    0.58.0: Move mutable document attributes into the document_meta table
    0.64.0: Rename the document_meta identity column document_id to id
    0.75.0: Index documents.id, chunks.id, chunks.document_id and …
✓ handbook.lancedb: up to date
```

Checking only reads, so it is safe while the stack is up, and it exits non-zero
when any store is behind — enough to gate a deployment or a CI job.

### Apply them

Migrating writes, and LanceDB allows a single writer, so the ingester has to be
out of the way. The script refuses while it is running rather than risking the
store:

```bash
docker compose stop haiku-ingester
uv run scripts/migrate_rag_dbs.py
docker compose start haiku-ingester
```

It migrates every store it finds; pass `--db-name` (repeatable) to select
stores by stem, e.g. `--db-name handbook` for `rag/db/handbook.lancedb`.
Migrating is idempotent — a store that needs nothing reports
`No migrations pending` and is left alone — so re-running after a partial
failure is safe, as is running it on a stack that turned out not to need it.

Back the stores up first if the corpus is expensive to rebuild. They are a bind
mount, not a volume, so a plain copy taken while the ingester is stopped is
enough:

```bash
docker compose stop haiku-ingester
cp -a rag/db rag/db.backup
```

### Driving the CLI directly

The script is a loop over two `haiku-rag` subcommands, either of which can be
run by hand on the `haiku-rag` service:

```bash
docker compose run --rm haiku-rag info --db /data/haiku.rag.lancedb
docker compose run --rm haiku-rag migrate --db /data/haiku.rag.lancedb
```

`info` is the one command a store awaiting migration still answers. It reports
the version that last wrote the store (`haiku.rag version (db)`) and either
`Database is up to date.` or the pending upgrades. Note that it exits `0` in
both cases — that is why `--check` reads its output rather than its status.
