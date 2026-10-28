# Move to soliplex 0.85

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.10-37f7a2d` skill release** — its generator produces the rung's end state (Soliplex 0.85 built from PyPI; `uv add`-shaped).  The stack being upgraded names the release that generated it in its own `[tool.soliplex-template]` manifest — use that
  one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.10-37f7a2d
```

This section keeps the stack's PyPI-built `backend/Dockerfile`; only versions
and configuration shapes change. Skip the steps that do not apply.

1. **Raise the soliplex pin**, everywhere the stack has one:

    - `backend/constraints.txt`, and `tui/constraints.txt` if the stack has
      the TUI:

        ```text
        soliplex >= 0.85, < 0.86
        ```

    - the `soliplex` entry in the root `pyproject.toml`'s `dependencies`
      (`"soliplex >= 0.85, < 0.86"`);
    - the `soliplex` requirement in `src/<package>/pyproject.toml`. That is
      your own library, so check rather than overwrite: if the range it
      gives does not admit `0.85`, raise it to `>= 0.85, < 0.86`.

    Then refresh both lock files and the host environment, so host-side
    work runs 0.85 from here on:

    ```bash
    uv lock && uv sync
    (cd src/<package> && uv lock && uv sync)
    ```

    Expect large lock diffs: the new soliplex pulls in a newer dependency
    tree.

2. **Add `bubblewrap`** to the `apt-get install` list in
   `backend/Dockerfile`, whether or not any room uses the sandbox skill yet:
   soliplex 0.84 and later refuse a room configuring that skill unless
   `bwrap` can run, and the current template always installs it. (A stack
   going on to section 2 replaces this Dockerfile there, so it may skip
   this step.)

3. **Bump the haiku.rag image** to `ghcr.io/ggozad/haiku.rag-slim:0.96.1`, in
   both places it appears: the `FROM` line of `haiku.rag/Dockerfile`, and the
   `haiku-ingester` service's `image:` in `docker-compose.yml` (in newer
   stacks that line also carries an `&haiku_rag_image` anchor; keep it).
   soliplex 0.85 requires `haiku.rag-slim >= 0.96.1, < 0.97`, and the
   ingester writes the stores the backend reads. Record the new version in
   the manifest:

    ```toml
    haiku_rag_version = "0.96.1"
    ```

    The template's two `haiku.rag.yaml` files need no changes between
    haiku.rag 0.82 and 0.96.1. For a later bump, check haiku.rag's release
    notes for configuration changes.

4. **Bump docling-serve** to `v1.32.0`, in `docling-serve/Dockerfile` and
   the `docling-serve` service's `image:` (and the commented GPU
   alternatives beside each). It goes with the haiku.rag bump: `v1.32.0`
   carries the docling stack haiku.rag 0.83 and later pin. It changes no
   stored data, but it does change how documents re-ingested from now on
   are chunked, and so their chunk ids; see
   [Converter bumps](../users/operations/migrations.md#converter-bumps-docling-serve).

5. **Rename the database settings** (stacks from before soliplex 0.82, whose
   `installation.yaml` still has `thread_persistence_dburi:`). Each section
   gets a new name, and its keys a `_dburi` suffix:

    ```yaml
    thread_persistence_db:
      sync_dburi: "postgresql+psycopg://soliplex_agui:secret:AGUI_DB_PASSWORD@postgres/soliplex_agui"
      async_dburi: "postgresql+asyncpg://soliplex_agui:secret:AGUI_DB_PASSWORD@postgres/soliplex_agui"

    authorization_db:
      sync_dburi: "postgresql+psycopg://soliplex_authz:secret:AUTHZ_DB_PASSWORD@postgres/soliplex_authz"
      async_dburi: "postgresql+asyncpg://soliplex_authz:secret:AUTHZ_DB_PASSWORD@postgres/soliplex_authz"
    ```

    Rename the commented SQLite alternatives (`#sync:`, `#async:`) the same
    way, so uncommenting one later does not bring the old spelling back.
    Keep your own role and database names if they differ. (Section 2 later
    switches the `async_dburi` driver to `psycopg`.)

6. **Update sandbox rooms.** List the rooms that configure the sandbox
   skill:

    ```bash
    grep -rln 'kind: "\(bubble-sandbox\|bwrap_sandbox\)"' backend/environment/rooms
    ```

    soliplex 0.84 flattened the skill's settings, and rejects the old keys,
    so in each room listed:

    | Before | After |
    |---|---|
    | `default_environment: "bare"` | `environment: "bare"` |
    | `allowed_environments: [...]` | *(gone: the room runs one environment, and the model does not choose it)* |
    | `sandbox_config:` → `execution_timeout_seconds: 30` | `execution_timeout_seconds: 30.0` (top level) |
    | `sandbox_config:` → `max_output_chars: 100000` | `max_output_chars: 10000` (top level) |

7. **Add the sandbox `security_opt` block** to the `backend` service in
   `docker-compose.yml`, after its `user:` line (where the template has
   it), and `security_opt: *sandbox_security_opt` to `soliplex-dev` if the
   stack has one. Add it **live** if a room from step 6 is to run code:

    ```yaml
        security_opt: &sandbox_security_opt
          - seccomp=unconfined
          - apparmor=unconfined
          - systempaths=unconfined
    ```

    and **commented out** otherwise, as a stack generated today without the
    sandbox carries it, so opting in later means uncommenting it:

    ```yaml
        #security_opt: &sandbox_security_opt
        #  - seccomp=unconfined
        #  - apparmor=unconfined
        #  - systempaths=unconfined
    ```

    `add_room.py` checks for the live block, and refuses to add a sandbox
    room to a stack without it. Record the choice in the manifest's
    `[tool.soliplex-template.params]` table, beside `include_tui`:

    ```toml
    enable_sandbox = false    # true with the live block
    ```

    [Configuration](../users/architecture/configuration.md) explains what
    the block lifts and why.

8. **Add the one-shot `haiku-rag` service** if `docker-compose.yml` lacks it:
   `migrate_rag_dbs.py` runs on it. It shares the ingester's settings through
   YAML anchors, so first name four of the `haiku-ingester` service's own
   keys, the ones directly under `haiku-ingester:` at its four-space indent
   (other services have keys of the same names; leave those alone):

    ```yaml
      haiku-ingester:
        image: &haiku_rag_image ghcr.io/ggozad/haiku.rag-slim:0.96.1
        # ...
        volumes: &haiku_rag_volumes
        # ...
        environment: &haiku_rag_environment
        # ...
        ulimits: &haiku_rag_ulimits
    ```

    then add the service after it:

    ```yaml
      haiku-rag:
        profiles: ["tools"]
        image: *haiku_rag_image
        build:
          context: haiku.rag
          dockerfile: Dockerfile
        entrypoint:
          - sh
          - -c
          - |
            export INGESTER_DB_PASSWORD="$$(cat /run/secrets/ingester_db_password)"
            exec haiku-rag --config /app/haiku.rag.yaml "$$@"
          - haiku-rag
        volumes: *haiku_rag_volumes
        environment: *haiku_rag_environment
        user: "${PUID:-1000}:${PGID:-1000}"
        secrets:
          - ingester_db_password
        ulimits: *haiku_rag_ulimits
    ```

    Check it by running the CLI on it. It is held out of `docker compose up`
    by its profile, so plain `docker compose config` does not list it, but
    `run` enables the profile itself; against the new haiku.rag image this
    also reports any store migrations step 11 will apply:

    ```bash
    docker compose run --rm haiku-rag info --db /data/haiku.rag.lancedb
    ```

9. **Tag the current backend image, then rebuild.** The tag keeps the old
   image to return to if the upgrade goes wrong; `<project>` is the `name:`
   at the top of `docker-compose.yml`:

    ```bash
    docker tag <project>-backend:latest <project>-backend:pre-soliplex-0.85
    docker compose build --no-cache backend
    docker compose build haiku-ingester docling-serve
    ```

    (and tag and rebuild `tui` the same way, if the stack has it).
    `--no-cache` matters: the backend resolves soliplex from PyPI in a
    cached layer, and without it Docker can reuse a layer an earlier build
    resolved, silently installing an older `0.85.x` than the newest. To roll
    back, switch back to your usual branch and tag the old image as
    `latest` again; a database already migrated (step 10) also needs its
    backup restored.

10. **Migrate the Soliplex databases**, with only Postgres running (the
    script runs the rebuilt backend image in a throwaway container, and
    refuses while the backend service is up). Databases created by soliplex
    0.81 or earlier carry no Alembic stamp, and the backend refuses to start
    on them until stamped:

    ```bash
    docker compose up -d --wait postgres
    uv run scripts/migrate_soliplex_dbs.py --check
    uv run scripts/migrate_soliplex_dbs.py
    ```

    Stamping prints the bootstrap script's own advice to run `soliplex-cli
    database upgrade`; the script then runs it itself, so there is nothing
    to do by hand. A second `--check` should report both databases at head.

    Do this **before** section 2. Releases of the script up to
    soliplex-template 0.17.x fetch soliplex's stamping step with `curl`
    inside the backend image, which this PyPI-built image has and the release
    images do not.

11. **Migrate the RAG stores**, with the ingester still stopped:

    ```bash
    uv run scripts/migrate_rag_dbs.py --check
    uv run scripts/migrate_rag_dbs.py
    ```

    haiku.rag prints a warning that it is outdated, naming a newer release;
    ignore it, since soliplex 0.85 requires a release below `0.97`.
    `rag/db/` is ignored by git, so this step leaves nothing to commit.

12. **Bring the stack up** (`docker compose up -d`), [**Check it**](index.md#check-it),
    and commit.

[Database migrations](../users/operations/migrations.md) covers both
migration scripts, and what their reports mean, in full.
