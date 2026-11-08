# Migrating the stack databases after a version bump

Loads when: Load this reference after raising the soliplex image tag (backend DBs) or the haiku.rag-slim tag (RAG stores) in a generated stack.

## Migrating the Soliplex databases after a soliplex bump

The backend's `soliplex_agui` and `soliplex_authz` databases are
Alembic-managed, and in a generated stack the backend migrates them to its
release's head itself, on startup — so **most** `soliplex` bumps need only
`docker compose build backend` and a restart. The exception is a stack whose
databases were created by soliplex 0.81 or earlier: they carry no
`alembic_version` stamp, and from 0.82 on the backend refuses to start
against them (`UnstampedDatabase` in `docker compose logs backend`) until
they have been stamped once. Do not guess which case a stack is — ask, as
below.

The stack ships `scripts/migrate_soliplex_dbs.py` for this; it runs the
backend image's own `soliplex-cli database` commands, so rebuild the backend
after raising the soliplex image tag in `backend/Dockerfile` (the script
refuses an image older than 0.82). Report first (read-only, safe while the stack is up,
non-zero exit if a database is unstamped, behind head, or unreachable):

```bash
docker compose build backend
uv run scripts/migrate_soliplex_dbs.py --check
```

Applying changes the schema, so the backend has to stop first — the script
refuses while it is running:

```bash
docker compose stop backend
uv run scripts/migrate_soliplex_dbs.py
docker compose start backend
```

It stamps unstamped databases with soliplex's one-time
`bootstrap_alembic_version.py` (fetched at the tag matching the image's
`soliplex`), then runs `soliplex-cli database upgrade /environment`.
Re-running is safe: with nothing owed it reports `Nothing to migrate.`. Both
forms take `--project-dir` (default: the stack holding the script). Suggest a
`pg_dump` first when the threads matter; the "Database migrations" operations
docs page has the command. For a single command by hand, run it in the
backend image: `docker compose run --rm --no-deps backend
/app/.venv/bin/soliplex-cli database status /environment` (or `upgrade`).

The haiku-ingester's job queue (`soliplex_ingester`) is not Alembic-managed:
the Compose command runs `haiku-ingester queue migrate` before serving.
Queues from haiku.rag before 0.97 also need their existing rows assigned to
the `soliplex` namespace; see `docs/operations/migrations.md` in the stack.

## Migrating the RAG stores after a haiku.rag bump

haiku.rag ships a store *upgrade* only for the few releases that changed the
LanceDB layout, and checks a store against that set rather than against the
release number — so **most** `haiku.rag-slim` bumps require no migration, even
ones that skip releases. A bump that does cross an upgrade leaves every store
under `rag/db/` written before it needing a one-off migration; until it runs,
the ingester crash-loops at startup and reads fail, the backend's included, so
rooms lose RAG search. Stacks that have only ever run the pinned image have
nothing to migrate. Do not guess which case a bump is — ask, as below.

Raising the tag means both `docker-compose.yml` and `haiku.rag/Dockerfile`,
which must agree.

The pairing is not a free choice: the `haiku.rag-slim` release must stay
inside the range the target `soliplex` release declares (the ingester
writes the LanceDB the backend reads). The extracted per-release pins live
in `references/version_pairings.toml` in this skill -- check the target
release's row before picking the new tag.

The stack ships `scripts/migrate_rag_dbs.py` for this; it runs the pinned
image's own `haiku-rag` CLI over every `*.lancedb` under `rag/db/`. Report
first (read-only, safe while the stack is up, non-zero exit if anything is
behind):

```bash
uv run scripts/migrate_rag_dbs.py --check
```

Applying writes, so the single writer has to stop first — the script refuses
while `haiku-ingester` is running:

```bash
docker compose stop haiku-ingester
uv run scripts/migrate_rag_dbs.py
docker compose start haiku-ingester
```

Both forms take `--project-dir` (default: the stack holding the script) and
`--db-name STEM` (repeatable; default: every store). Migrating is idempotent,
so re-running after a failure is safe. Suggest a copy of `rag/db/` first when
the corpus is expensive to rebuild. The underlying calls, if one is wanted by
hand, are `docker compose run --rm haiku-rag info --db /data/<stem>.lancedb`
and the same with `migrate`.
