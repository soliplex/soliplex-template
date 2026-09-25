---
icon: lucide/database-backup
---

# Database migrations

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

## The Soliplex databases (Postgres)

The backend keeps two databases, `soliplex_agui` (thread persistence) and
`soliplex_authz` (authorization policy), and tracks their schema with
[Alembic](https://soliplex.github.io/soliplex/server/migrations/). In this
stack each application role owns its own schema, so the backend migrates
both databases itself, on startup: a `soliplex` bump that adds a revision
needs nothing more than a rebuild and a restart.

### Upgrading from soliplex 0.81 or earlier

Releases through 0.81 created these databases without recording an Alembic
revision. From 0.82 on, the backend refuses to start against such a
database, rather than guess its schema:

```text
soliplex.alembic_migrations.UnstampedDatabase: agui: tables are present but
alembic_version is empty, so this database was created by soliplex 0.81 or
earlier. ...
```

Stamping them is a one-time step. Soliplex's
`scripts/bootstrap_alembic_version.py` is not part of the installed package,
so fetch it at the tag matching the pin and run it in the backend image,
where it reads the DBURIs and passwords from the installation config.
Check first, with `--dry-run`, then run again without it to write the stamp:

```bash
docker compose stop backend
docker compose run --rm --no-deps backend sh -c '
  curl -fsSL https://raw.githubusercontent.com/soliplex/soliplex/v0.82/scripts/bootstrap_alembic_version.py \
    -o /tmp/bootstrap.py &&
  /app/.venv/bin/python /tmp/bootstrap.py --installation-path /environment --dry-run'
```

It writes the `alembic_version` row and nothing else, detecting the revision
by fingerprinting the live schema. Then bring both databases to head and
start the backend:

```bash
docker compose run --rm --no-deps backend \
    /app/.venv/bin/soliplex-cli database upgrade /environment
docker compose start backend
```

(Starting the backend would also apply the pending revisions; running
`database upgrade` first reports them where you can see them.) At any point,
`soliplex-cli database status /environment` reports each database's applied
and pending revisions, and `soliplex-cli audit databases /environment` its
state, without changing anything.

A database created by 0.82 or later is stamped when it is created, so a
stack first brought up on 0.82 never needs this step.

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
