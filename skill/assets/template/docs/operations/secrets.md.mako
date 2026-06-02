---
icon: lucide/key-round
---

# Secrets

The Postgres roles and the Soliplex backend read their credentials from
secrets. There are two modes, both documented at the bottom of
`docker-compose.yml`.

<%text>## File-based (active by default)</%text>

`scripts/generate-secrets.sh` writes `.secrets/*.gen` files, which Compose
mounts as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored, so the
initial commit never captures secrets.

```bash
./scripts/generate-secrets.sh
```

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Re-run the script instead. Destroying these files after the Postgres volume
    already exists breaks the backend's auth to the database — you must also
    `docker compose down -v` and re-init.

<%text>## File ownership (`PUID` / `PGID`)</%text>

The secret files are mode `0600` (owner-only). For an in-container service
(e.g. Postgres) to read one, the file's **owner must match the uid the
container runs as**. The stack ties both ends to `PUID` / `PGID` in `.env`:

- every built image runs as `PUID:PGID` (Compose `build.args`), and
- `scripts/generate-secrets.sh` ensures the `.secrets/*.gen` files end up owned
  by `PUID:PGID`.

The generator defaults `PUID` / `PGID` to the host operator who scaffolded the
project, so on a single-developer machine this is automatic. On a deploy host
whose login uid differs from the runtime service account, set `PUID` / `PGID`
explicitly (and rebuild — see below); `generate-secrets.sh` then re-owns the
secret files to that uid/gid via a throwaway container (it needs Docker for
that step).

!!! warning "Changing `PUID` / `PGID` needs a rebuild"
    The uid is baked into the images at build time, so after editing `PUID` /
    `PGID` in `.env` you must `docker compose build` (and re-run
    `./scripts/generate-secrets.sh` so the secret files are re-owned to match).

!!! note "Override uid: who owns the secret files"
    When `PUID` differs from your login uid, the `.secrets/*.gen` files are
    owned by `PUID`, so reading or deleting them from the host needs that uid
    (or `sudo`). This is expected — it is what lets the container read them at
    mode `0600`.

<%text>## Env-var (commented alternative)</%text>

Uncomment the env-var secret blocks in `docker-compose.yml` and set the
corresponding `SOLIPLEX_*` environment variables instead of using files.

<%text>## The `down -v` caveat</%text>

`docker compose down -v` drops the `postgres_data` volume — all chat threads
and authorization grants go with it. The ingester's SQLite job queue lives
separately under `rag/db/` (a bind mount), so a `down -v` does not touch it.
