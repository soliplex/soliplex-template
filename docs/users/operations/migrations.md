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

The one exception is a stack whose databases were created by soliplex 0.81 or
earlier. Those releases recorded no Alembic revision, and from 0.82 on the
backend refuses to start against such a database rather than guess its
schema:

```text
soliplex.alembic_migrations.UnstampedDatabase: agui: tables are present but
alembic_version is empty, so this database was created by soliplex 0.81 or
earlier. ...
```

Stamping them is a one-time step, after which the backend migrates them like
any other. A stack first brought up on 0.82 or later is stamped when its
databases are created, and never needs it.

### Ask, rather than assume

`scripts/migrate_soliplex_dbs.py` puts the question to the *backend image*,
so raise the pin in `backend/constraints.txt` and rebuild first — the script
refuses an image too old to answer:

```bash
docker compose build backend
uv run scripts/migrate_soliplex_dbs.py --check
```

A stack with nothing owed reports:

```text
soliplex 0.82; head b7e2f41c9d05
✓ agui: at head
✓ authz: at head
```

A database behind head is listed with the revisions it would apply, and an
unstamped one with the revision its schema matches — here, a stack created by
soliplex 0.79:

```text
soliplex 0.82; head b7e2f41c9d05
! agui: unstamped, created by soliplex 0.81 or earlier (would stamp 63edaa5987f6)
! authz: unstamped, created by soliplex 0.81 or earlier (would stamp 63edaa5987f6)
```

Checking only reads, so it is safe while the stack is up, and it exits
non-zero when anything is owed — or when a database cannot be migrated as it
stands, most often because `postgres` is not running.

### Apply them

Migrating changes the schema under the backend, so the script refuses while
the backend is running:

```bash
docker compose stop backend
uv run scripts/migrate_soliplex_dbs.py
docker compose start backend
```

It stamps any unstamped database first, with soliplex's one-time
`scripts/bootstrap_alembic_version.py`, fetched at the tag matching the
`soliplex` in the backend image. That writes the `alembic_version` row and
nothing else, detecting the revision by fingerprinting the live schema. Then
it runs `soliplex-cli database upgrade`, which brings both databases to head
in one run, committing them together. Re-running on a stack with nothing owed
reports `Nothing to migrate.` and changes nothing.

Starting the backend would apply the pending revisions too, once the
databases are stamped; running the script first reports them where you can
see them.

Back the databases up first if their threads matter. A dump taken as the
`postgres` superuser, inside its own container:

```bash
docker compose exec -T postgres sh -c \
    'PGPASSWORD=$(cat /run/secrets/postgres_password) \
     pg_dump -h localhost -U postgres -Fc soliplex_agui' > soliplex_agui.dump
```

and the same for `soliplex_authz`.

### Driving the CLI directly

The script is a wrapper over commands the backend image already has, each run
with `docker compose run --rm --no-deps backend`:

```bash
docker compose run --rm --no-deps backend \
    /app/.venv/bin/soliplex-cli database status /environment
docker compose run --rm --no-deps backend \
    /app/.venv/bin/soliplex-cli database upgrade /environment
```

`status` lists each database's applied and pending revisions, and exits
non-zero for a database which cannot be migrated as it stands (unstamped,
unreachable, or stamped by a newer release); `soliplex-cli audit databases
/environment` gives the same verdict in one line per database. The bootstrap
script is not part of the installed package, so it is fetched and run in one
throwaway container. Use the tag matching the image's `soliplex`, and drop
`--dry-run` to write the stamp:

```bash
docker compose run --rm --no-deps backend sh -c '
  curl -fsSL https://raw.githubusercontent.com/soliplex/soliplex/v0.82/scripts/bootstrap_alembic_version.py \
    -o /tmp/bootstrap.py &&
  /app/.venv/bin/python /tmp/bootstrap.py --installation-path /environment --dry-run'
```

### The ingester's job queue

`soliplex_ingester`, the haiku-ingester's job queue, is not an Alembic
database. The ingester records its schema version in the queue itself and
migrates it in place every time it opens it, so a `haiku.rag-slim` bump
needs nothing for the queue beyond restarting the ingester on the new image.

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
