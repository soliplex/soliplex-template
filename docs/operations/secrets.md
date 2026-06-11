---
icon: lucide/key-round
---

# Secrets

The Postgres roles and the Soliplex backend read their credentials from secrets.
There are two modes, both documented at the bottom of `docker-compose.yml`.

## File-based (active by default)

`scripts/generate_secrets.py` writes `.secrets/*.gen` files, which Compose
mounts as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored, so the
initial commit never captures secrets.

```bash
uv run scripts/generate_secrets.py
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Destroying these files after the Postgres volume
    already exists breaks the backend's auth to the database — you must also
    `docker compose down -v` and re-init.

## Env-var (commented alternative)

Uncomment the env-var secret blocks in `docker-compose.yml` and set the
corresponding `SOLIPLEX_*` environment variables instead of using files.

## The `down -v` caveat

`docker compose down -v` drops the `postgres_data` volume — chat threads,
authorization grants, and the ingester's job queue (its own `soliplex_ingester`
database) all go with it. The RAG vector store under `rag/db/` (a bind mount,
not the postgres volume) is separate, so a `down -v` does not touch it.

If you destroy the secret files **and** the Postgres volume already exists, the
backend can no longer authenticate to the database. Recover by re-running the
secrets script, then `docker compose down -v` to wipe the volume so
`postgres/config/init.sh` re-creates the roles with the new passwords on the
next `up`.

## See also

The ingester control plane has its own bearer token, configured separately —
see [Ingester control plane](ingester.md).
