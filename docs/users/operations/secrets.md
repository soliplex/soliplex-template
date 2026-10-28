---
icon: lucide/key-round
---

# Secrets

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The Postgres roles and the Soliplex backend read their credentials from
secrets. There are two modes, both documented at the bottom of
`docker-compose.yml`.

## File-based (active by default)

`scripts/generate_secrets.py` writes `.secrets/*.gen` files, which Compose
mounts as Docker secrets at `/run/secrets/*`. `.secrets/` is gitignored, so the
initial commit never captures secrets.

```bash
uv run scripts/generate_secrets.py
```

By default the script only generates secrets whose `.gen` files are missing;
existing files are left untouched, so it is safe to re-run at any time -- e.g.
after adopting a stack feature that declares new secrets.

!!! warning "Don't hand-edit `.secrets/*.gen`"
    Deleting one after the Postgres volume already exists breaks the backend's
    auth to the database -- rotate the credential with `--rotate` instead, or
    `docker compose down -v` and re-init (see the caveat below).

### Rotating secrets

Pass secret names (the `.gen` suffix is optional) to replace specific
credentials:

```bash
uv run scripts/generate_secrets.py --rotate ingester_bearer
```

`--rotate-all` replaces every declared secret. Bare `--rotate` (no names)
replaces nothing: it prints a preview of what `--rotate-all` would do.

Rotation of a secret that feeds a Postgres role password needs one extra
step, and the script prints it after regenerating: role passwords are read
only at initdb, so once the `postgres_data` volume exists its roles still
hold the old passwords. The script prints a ready-to-paste `ALTER ROLE`
command per affected role (the role names are joined from the compose
wiring and the `postgres/config` init scripts), or `docker compose down -v`
as the wipe-everything alternative (see the caveat below).

`--dry-run` turns any invocation into a preview: it prints what the run
would write and touches nothing.

## File ownership (`PUID` / `PGID`)

The secret files are mode `0600` (owner-only). For an in-container service
(e.g. Postgres) to read one, the file's **owner must match the uid the
container runs as**. The stack ties both ends to `PUID` / `PGID` in `.env`:

- every built image runs as `PUID:PGID` (Compose `build.args`), and
- `scripts/generate_secrets.py` ensures the `.secrets/*.gen` files end up owned
  by `PUID:PGID`.

The generator defaults `PUID` / `PGID` to the host operator who scaffolded the
project, so on a single-developer machine this is automatic. On a deploy host
whose login uid differs from the runtime service account, set `PUID` / `PGID`
explicitly (and rebuild — see below); `generate_secrets.py` then re-owns the
secret files to that uid/gid via a throwaway container (it needs Docker for
that step).

!!! warning "Changing `PUID` / `PGID` needs a rebuild"
    The uid is baked into the images at build time, so after editing `PUID` /
    `PGID` in `.env` you must `docker compose build` (and re-run
    `uv run scripts/generate_secrets.py` so the secret files are re-owned to
    match).

!!! note "Override uid: who owns the secret files"
    When `PUID` differs from your login uid, the `.secrets/*.gen` files are
    owned by `PUID`, so reading or deleting them from the host needs that uid
    (or `sudo`). This is expected — it is what lets the container read them at
    mode `0600`.

## Env-var (commented alternative)

Uncomment the env-var secret blocks in `docker-compose.yml` and set the
corresponding `SOLIPLEX_*` environment variables instead of using files.

## The `down -v` caveat

`docker compose down -v` drops the `postgres_data` volume — chat threads,
authorization grants, and the ingester's job queue (its own `soliplex_ingester`
database) all go with it. The RAG vector store under `rag/db/` (a bind mount)
is separate, so a `down -v` does not touch it.
