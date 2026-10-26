---
icon: lucide/circle-arrow-up
---

# Upgrading an existing stack

A generated stack belongs to you. Regenerating it would throw away your rooms,
prompts and configuration, and nothing yet updates one in place, so bringing an
older stack up to the current template is a by-hand job. This page lists the
changes in order, grouped by the template release that introduced them: find
the section your stack is at, and work forward from there.

## Where do I start?

| Your stack | What its files show | Skill release to walk with | Start at |
|---|---|---|---|
| soliplex 0.84 or earlier | `backend/constraints.txt` pins below `0.85` | `template-skill-2026.10.10-37f7a2d` | [1](#1-move-to-soliplex-085) |
| soliplex 0.85, built from PyPI | `backend/Dockerfile` runs `uv add … soliplex` | `template-skill-2026.10.14-9a17a20` | [2](#2-build-on-the-soliplex-release-images) |
| on the 0.85 release images | `backend/Dockerfile` builds `FROM ghcr.io/soliplex/soliplex-postgres:0.85` | `template-skill-2026.10.24-932e134` | [3](#3-move-to-soliplex-0861-and-haikurag-01010) |

The `[tool.soliplex-template]` manifest at the end of the stack's
`pyproject.toml` records the skill commit (`skill_source_commit`) and date
(`skill_generated`) that generated it, and the parameters it was generated
with. (`skill_version` may be empty.)

!!! tip "Compare against a fresh stack"
    Each step names what the current template has. To see it in place,
    generate a throwaway stack with **the skill release the rung
    walks with** (see Before you start #1) and your own parameters,
    and diff the two; see
    [Checking the whole stack against a reference](migrating-layout.md#checking-the-whole-stack-against-a-reference).

## How an upgrade runs

Every upgrade on this page should follow this phased process:

1. **Prepare** — "Before you start": the new skill, a clean branch,
   the stack stopped, the state recorded and backed up.
2. **Run the rung** — the numbered section **Where do I start?**
   named for your stack's state.
3. **Check it** — the shared verification checklist: the audit's
   database lines and a room's `ask`.
4. **Afterwards** — restore dev mode, refresh the stack's own
   agent-facing docs.  Still on the rung's branch.
5. **Roll out** — merge the branch to `main` and bring the live stack
   up on the merged state.  `main` carries the *files*;  the migration
   steps the rung rehearsed (the queue migration, the LanceDB store
   upgrades, the Alembic head) run **live**, against the deployment's
   own stores and databases, in the rollout's stop-apply-restart
   order.

Each phase completes before the next begins:  a rung is not done
until it has passed **Check it** and its commit exists, and no rung
starts with the previous one unfinished.

### Before you start

1. **Upgrade the `soliplex-template` skill in your harness**, then pick
   the release that matches the *rung being walked*:  the rung's steps
   run the skill's scripts (`src_projects.py`), and its generator
   renders the reference stack the rung compares against. The
   release this page describes is the right one for the newest rung
   (3);  earlier rungs walk with the release that generated the
   shape the rung *lands on* — the stack's own manifest
   (`skill_source_commit` in `[tool.soliplex-template]`) names the
   release that generated it, the one the rung moving *from* it
   needs.

   For instance, upgrade rung #1 above specifies the
   `template-skill-2026.10.10-37f7a2d` skill release.  If that is your
   rung, ask your agent, e.g.:

    ```text
    Set the installed soliplex-template skill to the `template-skill-2026.10.10-37f7a2d` release.
    ```

    or run its helper yourself (`list` shows the published releases,
    `diff` what a move would change; both `upgrade` and `downgrade`
    land on the target from whatever is installed — no need to pass
    through the newest release first):

    ```bash
    uv run <skill>/scripts/skill_versions.py list
    uv run <skill>/scripts/skill_versions.py downgrade \
        template-skill-2026.10.10-37f7a2d   # e.g. the rung-1 cut
    ```

    The rung's scripts come from the installed release, so walking a
    rung with a mismatched skill runs older scripts and generates an
    old-template reference:  a comparison against it shows nothing
    to change. To check that the installed skill matches *this
    page*, ask its generator for its defaults: this page's release
    names the image tag, where an older one names the PyPI
    constraint instead:

    ```bash
    uv run <skill>/scripts/generate_soliplex_project.py --print-defaults \
        | grep -E 'soliplex_(image_tag|backend_constraint)'
    #   "soliplex_image_tag": "0.85",
    ```

2. **Start from a clean git tree, on a branch of its own**, so each step
   below is a commit you can review, and switching back to your usual
   branch undoes the lot. Tag the starting point too; section 2 compares
   against it:

    ```bash
    git status --short                     # should list nothing
    git switch -c upgrade-soliplex-0.85
    git tag pre-upgrade-soliplex-0.85
    ```

    A checkout cloned under `src/` (e.g. a dev-mode `src/soliplex`) is its
    own repository, which the stack's `.gitignore` hides from `git status`,
    so check it separately:

    ```bash
    git -C src/soliplex status --short     # for each checkout under src/
    ```

3. **Stop the stack** with `docker compose down`, and check that nothing is
   left running (a one-off `docker compose run` container, say):

    ```bash
    docker compose down
    docker compose ps -a                   # should list nothing
    ```

    Never `down -v` here: that drops the Postgres volume holding the
    databases this upgrade migrates. (From step 5 on, `docker compose ps -a`
    also lists a stopped `postgres` container, which step 5 starts and stops
    again; that one is expected.)

4. **Record the state of each RAG store**, to compare against after the
   upgrade. A stack whose `docker-compose.yml` has the one-shot `haiku-rag`
   service runs the CLI on it:

    ```bash
    docker compose run --rm haiku-rag info --db /data/haiku.rag.lancedb
    ```

    An older stack, without that service, runs it on the ingester instead.
    `--no-deps` keeps the ingester's dependencies (Postgres, docling-serve)
    stopped, and `-e` sets the queue password the config interpolates,
    which `run` would otherwise lose along with the ingester's own command;
    `info` never opens the queue, so any value will do:

    ```bash
    docker compose run --rm --no-deps -e INGESTER_DB_PASSWORD=unused \
        haiku-ingester haiku-rag info --db /data/haiku.rag.lancedb
    ```

    Run it once per store, `rag/db/<stem>.lancedb` appearing in the
    container as `/data/<stem>.lancedb`. Note the `haiku.rag version (db)`
    and the document and chunk counts.

5. **Back up the data** the migrations touch, into a new directory under
   `tmp/`, which the stack's `.gitignore` already ignores. The directory is
   named to the second, so a second run makes a second backup: `cp -a` into
   a directory that already exists would nest the copy inside it instead.
   The Postgres dumps need Postgres running, and only Postgres:

    ```bash
    BACKUP=tmp/backup-$(date +%Y%m%dT%H%M%S)
    mkdir "$BACKUP"
    docker compose up -d --wait postgres
    for db in soliplex_agui soliplex_authz; do
        docker compose exec -T postgres sh -c \
            'PGPASSWORD=$(cat /run/secrets/postgres_password) \
             pg_dump -h localhost -U postgres -Fc "$0"' "$db" \
            > "$BACKUP/$db.dump"
    done
    docker compose stop postgres
    cp -a rag/db "$BACKUP/rag-db"
    ```

    Then check the dumps: a failed `pg_dump` still leaves its output file
    behind, empty. Each should be non-empty and start with `PGDMP`:

    ```bash
    for f in "$BACKUP"/*.dump; do printf '%s: ' "$f"; head -c 5 "$f"; echo; done
    ```

    Use your own database names if they differ (the manifest's `agui_db` and
    `authz_db`).

6. **Take a dev-mode checkout off the path.** If the backend's `PYTHONPATH`
   in `docker-compose.yml` lists a checkout providing the `soliplex` package
   (e.g. `/app/src/soliplex/src`), take it off for the upgrade:

    ```bash
    uv run <skill>/scripts/src_projects.py remove soliplex
    ```

    Ignore its closing advice to `docker compose up -d backend`: the stack
    stays down until the end of the section. `remove` also takes the
    checkout off the backend's watch list (its `--reload-dirs`), and settles
    an older reload mode: `--reload=both` becomes `--reload=config`, while
    `--reload=python` stays, being what turns reload on without watching the
    config (`--reload=config` would work as well).

    The checkout's code would otherwise override the soliplex the rebuilt
    image installs, and the migrations would ask the wrong release. Putting
    it back is the last step, [Restore dev mode](#restore-dev-mode).

7. **Fetch the migration scripts** if the stack's `scripts/` lacks
   `migrate_soliplex_dbs.py` and `migrate_rag_dbs.py` (stacks generated
   before soliplex-template 0.17):

    ```bash
    for s in migrate_soliplex_dbs migrate_rag_dbs; do
        curl -fsSL -o "scripts/$s.py" \
            "https://raw.githubusercontent.com/soliplex/soliplex-template/main/scripts/$s.py"
    done
    ```

    Each is a PEP 723 shim: `uv run` provisions the `soliplex-template`
    package from PyPI, which holds the migration code itself.

8. **Read the scripts' advice with the upgrade in mind.** `src_projects.py`
   and both migration scripts end by telling you to start or restart a
   service. Mid-upgrade, ignore that: each section says when to bring the
   stack up.

`<skill>` is wherever the soliplex-template skill is installed, e.g.
`~/.claude/skills/soliplex-template`. The steps below edit the manifest in
a few places, although its header says not to: they are the exception, so
that it describes the stack the upgrade leaves.

### Follow the upgrade steps for your rung

Follow the steps listed for your upgrade rung in order.  After each step,
check the status of your stack's files:

```bash
git status
```

If there are changes, commit them with a short checkpoint commit message:

```bash
git add .
git commit -m "Step #2: <short description>"
```

### Restore Dev mode

A stack that ran a `soliplex` checkout in dev mode can put it back now;
`add` puts it on the path and watches it, like any other project.
Check out the release the image runs first: the checkout supplies only
soliplex's *code*, and its dependencies still come from the image, so a
checkout far from the image's release fails at import time.

```bash
docker compose run --rm --no-deps backend soliplex-cli --version
git -C src/soliplex checkout v<the version just reported>
uv run <skill>/scripts/src_projects.py add soliplex
docker compose up -d backend
```

### Check it

```bash
docker compose ps                    # every service running, backend healthy
docker compose run --rm --no-deps backend soliplex-cli audit /environment
ls backend/environment/rooms         # room ids, for the 'ask' below
docker compose run --rm --no-deps backend \
    soliplex-cli ask /environment <room-id> "Say hello"
```

For the upgrade, what matters in the audit is its database lines: both
databases `OK` at the same Alembic revision. It can still exit non-zero for
reasons of its own, such as models missing on the Ollama server
(`soliplex-cli ollama pull` fetches them). The `ask` should return a reply
and persist its thread. Open the frontend (`http://localhost:9000` by
default) for a last look.

### Afterwards

The stack's own agent guide (`AGENTS.md`, or `CLAUDE.md` in older
stacks) and its `docs/` were written for the stack as generated, and
the upgrade leaves them describing the old setup:
`backend/constraints.txt`, the old image tags.  Refresh them from a
reference stack generated with your parameters (see the tip at the
top), or at least treat them as out of date.

**Wrap the run into `main`**: merge the rung's branch and bring the
live stack up on the merged state.  What merges is the *files*;  what
rolls out is the *state*:  the migration steps the rung rehearsed run
**live**, against the deployment's own databases and stores.

- **In the live stack's clone**, the merge is direct, and the rollout
  follows it in the same working tree.
- **From a separate upgrade-only clone**, the clone is the
  *rehearsal*:  it proved the rung's steps against copied stores and
  databases;  push the branch and open the PR for the *files*, and
  roll out live afterwards by re-running the rung's migration steps
  for real.

## Upgrade rungs

### 1. Move to soliplex 0.85

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

12. **Bring the stack up** (`docker compose up -d`), [check it](#check-it),
    and commit.

[Database migrations](../users/operations/migrations.md) covers both
migration scripts, and what their reports mean, in full.

### 2. Build on the soliplex release images

**Walk this rung with the `template-skill-2026.10.20-a2c13ae` skill release** — its generator produces the rung's end state (the 0.85 *release-image* shape).  The stack being upgraded names the release that generated it in its own `[tool.soliplex-template]` manifest — use that
  one when climbing *from* an older shape.

    ```bash
    uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.20-a2c13ae
    ```

From here the backend no longer resolves soliplex from PyPI: it builds on the
published `ghcr.io/soliplex/soliplex-postgres` image, which already holds
soliplex and its PostgreSQL driver. See
[Backend image & dependencies](../users/architecture/backend.md) for how the
result works.

Stop the stack first (`docker compose down`). The backend reloads
`backend/environment/` as soon as a file there changes, so a running stack
would pick up step 5's edit on the old image.

1. **Note your extras.** The old `backend/Dockerfile` installs packages with
   `uv add`. Anything on that line other than `soliplex`, `psycopg[binary]`,
   `asyncpg`, `textual`, `textual-fspicker` and `textual-serve` is yours,
   and so is any pin in `backend/constraints.txt` other than `soliplex`'s.
   Your own edits to the two files show in their history from generation
   to the `pre-upgrade` tag, which leaves out section 1's changes:

    ```bash
    git diff $(git rev-list --max-parents=0 HEAD) pre-upgrade \
        -- backend/Dockerfile backend/constraints.txt
    ```

    That takes the stack's first commit as "as generated", which holds when
    the generator created the repository (its default). For a stack
    generated with `--no-git`, or whose history was rewritten, compare
    against a reference stack instead (see the tip at the top).

    The `textual` packages go: the backend image no longer carries the
    TUI's dependencies, so a TUI run inside the backend container needs the
    TUI service instead (`include_tui`).

2. **Create `backend/requirements.txt`** with your extras, one requirement
   per line: an `httpx` on the `uv add` line with an `httpx >= 0.28` pin in
   `constraints.txt` becomes the single line `httpx >= 0.28`. Drop pins on
   packages the soliplex release already holds: the build freezes the
   image's own versions as constraints, so a pin that disagrees fails the
   build rather than moving them. With no extras, the file holds only its
   header:

    ```text
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/backend/requirements.txt"
    ```

3. **Replace `backend/Dockerfile`** with the release-image one below. Carry
   over any local edits from step 1 (e.g. a `COPY` of `vendor/`).

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/backend/Dockerfile"
    ```

4. **Delete `backend/constraints.txt`**.

5. **Switch the async driver** in `backend/environment/installation.yaml`:
   both `async_dburi` values change `postgresql+asyncpg://` to
   `postgresql+psycopg://`. The image ships psycopg, not asyncpg. Leave
   `haiku.rag/haiku.rag.yaml` alone: the haiku.rag image has its own driver,
   as the comment above its `dburi` says.

6. **Replace the backend's healthcheck test** in `docker-compose.yml`. The
   image has no `curl`, so the `test:` entry under the backend's
   `healthcheck:` uses the venv's Python instead; keep its `interval`,
   `timeout` and `retries`:

    ```yaml
        healthcheck:
          # The soliplex image has no curl; use the venv's stdlib instead.
          test:
            - "CMD"
            - "python"
            - "-c"
            - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/ok', timeout=5).status == 200 else 1)"
          interval: 30s
          timeout: 10s
          retries: 4
    ```

    It takes effect when step 13 recreates the container.

7. **The TUI**, if `tui/Dockerfile` exists: replace it with the one below,
   built `FROM ghcr.io/soliplex/soliplex-tui` at the same tag as the
   backend, and delete `tui/constraints.txt`:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/tui/Dockerfile"
    ```

8. **Update the stack's host-side dependencies** in the root
   `pyproject.toml`. The `soliplex`, `psycopg[binary]` and `asyncpg` entries
   become one:

    ```toml
    dependencies = [
        "soliplex[postgres-binary] >= 0.85, < 0.86",
    ]
    ```

    then refresh the lock and the environment (`uv lock && uv sync`).
    Section 1 already moved the lock to 0.85, so the diff is small, mostly
    `asyncpg` dropping out. `src/<package>/pyproject.toml` already admits
    0.85 (section 1, step 1).

9. **Update the manifest's parameters** in the
   `[tool.soliplex-template.params]` table. The version parameters are
   renamed, `soliplex_backend_constraint` and `soliplex_tui_constraint`
   giving way to:

    ```toml
    soliplex_image_tag = "0.85"
    soliplex_dev_constraint = ">= 0.85, < 0.86"
    ```

    and, if section 1 did not already set them, `haiku_rag_version =
    "0.96.1"` and `enable_sandbox` (section 1, steps 3 and 7). Finally, so
    the manifest names the template the stack now matches, set its
    `skill_source_commit` and `skill_generated` to the upgraded skill's own
    `source_commit` and `generated`, from the `metadata:` at the top of its
    `SKILL.md`. Only a built release carries that block: the skill as it
    sits in the template repository has none. A skill built from a checkout
    with `scripts/build_skill.py` gets one, stamped with the checkout's
    `HEAD`, which does not cover changes not yet committed there.

10. **Watch your own package.** A stack generated before the backend
    watched its package carries no `--reload-dirs`, so an edit under
    `src/<package>/` needs a restart. Have it watched, as a newly generated
    stack's is:

    ```bash
    uv run <skill>/scripts/src_projects.py add <package>
    ```

    Its `PYTHONPATH` entry is already there (`unchanged`); `add` adds the
    matching `--reload-dirs=/app/src/<package>/src` to the serve command.
    Ignore its advice to bring the backend up: step 13 does that.

11. **Give `soliplex-dev` a build of its own** (stacks whose
    `docker-compose.yml` has a `soliplex-dev` service). It now builds on the
    backend's image plus every `src/` project's `dev` dependency group, so
    a project's tests run there against the backend's versions, without the
    backend image carrying them. Make a `soliplex-dev/` directory, save the
    template's Dockerfile into it as `soliplex-dev/Dockerfile`:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/soliplex-dev/Dockerfile"
    ```

    then generate its requirements file from the projects' dev groups:

    ```bash
    uv run <skill>/scripts/src_projects.py dev-requirements
    ```

    and point the service's `build:` at it, in place of its
    `context: backend`, `dockerfile: Dockerfile` and `args:`:

    ```yaml
        build:
          context: soliplex-dev
          additional_contexts:
            backend: "service:backend"
    ```

    Commit both files. Step 13 builds it.

12. **Build nginx on the published web client.** Instead of building the
    Flutter client from a release tarball, `nginx/Dockerfile` now copies it
    from the `ghcr.io/soliplex/frontend-web` image, already cache-busted. Replace the
    Dockerfile with the template's, keeping your own `-subj "…"` certificate
    subject on its `openssl req` line:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/nginx/Dockerfile"
    ```

    delete `nginx/post-build-cache-bust.sh`, which it no longer runs, and in
    `nginx/nginx.conf` replace the hashed-asset locations — in **both**
    `server` blocks, the HTTP one and the HTTPS one — with the one below. The
    image names its asset directory for the release tag, build metadata
    included (`v0.110.0+96/`), which the old pattern does not match, so
    without this change the assets lose their long-lived caching:

    ```nginx
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/nginx/nginx.conf:hashed-assets"
    ```

    Then, in the manifest, set `frontend_version` to the image tag (`0.110`
    follows the 0.110 series) and drop `frontend_release_path`, which nothing
    reads any more.

13. **Tag the current backend image, then rebuild with `--pull`**, so Docker
    fetches the release image, and bring the stack up:

    ```bash
    docker tag <project>-backend:latest <project>-backend:pre-release-image
    docker compose build --pull backend nginx
    docker compose up -d
    ```

    Tag and rebuild `tui` the same way if the stack has it, and add
    `soliplex-dev` to the build if the stack has that (it builds on the
    backend's image). Then [check it](#check-it), and commit.

### 3. Move to Soliplex 0.86.1 and haiku.rag 0.101.0

**Walk this rung with the `template-skill-2026.10.24-932e134` skill release** — its generator produces the rung's end state (the current template shape).  The stack being upgraded names the release that generated it in its own `[tool.soliplex-template]` manifest — use that
  one when climbing *from* an older shape.

    ```bash
    uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.24-932e134
    ```

1. Stop `backend` and `haiku-ingester`, and back up the Postgres databases
   and `rag/db/` before upgrading.

2. Adopt the image-shape changes, which came to the template ahead of
   this step: the image shim Dockerfiles (`haiku.rag/Dockerfile`,
   `docling-serve/Dockerfile`) are gone; their build recipes moved to
   `README.md` files beside their build contexts, and the template's
   `build:` stanzas for those services ship commented. Delete your
   copies (or rename them to the `README.md`s, which carry the
   recipes), and comment the stanzas the same way.

3. Set the backend and optional TUI image tags to `0.86.1`; set the
   haiku.rag image to `0.101.0` in the `haiku_rag_image` anchor in
   `docker-compose.yml`; set the `docling-serve` image to
   `quay.io/docling-project/docling-serve-cpu:v1.38.0`.

4. Update `soliplex_image_tag`, `haiku_rag_version`, and
   `soliplex_dev_constraint` in the project's manifest. Use
   `>= 0.86.1, < 0.87` for the Soliplex requirement in the root and package
   `pyproject.toml` files, then run `uv lock` in each project.

5. In `haiku.rag/haiku.rag.yaml`, move the source list from
   `ingester.sources` to the `sources` list of the `ingester.databases`
   entry keyed `haiku.rag` (the key is the database's own name, dot
   included). Keep its
   source ids and paths. Add `namespace: soliplex` under `ingester.queue`.
   This still writes `/data/haiku.rag.lancedb`. Additional databases are
   described in
   [RAG pipeline](../users/operations/rag.md#ingesting-multiple-databases).

6. In the `haiku-ingester` Compose command, change `sh -c` to `sh -ec` and
   insert this command after exporting `INGESTER_DB_PASSWORD` and before
   `exec haiku-ingester --config /app/haiku.rag.yaml serve`:

    ```bash
    haiku-ingester --config /app/haiku.rag.yaml queue migrate
    ```

7. Rebuild `backend`, `soliplex-dev`, `haiku-ingester`, `docling-serve`, and
   the optional `tui` with `docker compose build --pull`.

8. Before restarting the ingester, follow
   [the queue migration](../users/operations/migrations.md#the-ingesters-job-queue)
   to migrate the schema and assign existing rows to `soliplex`. Otherwise
   the ingester will not see its previous jobs and sync state.  The template
   prepares the queue schema before every ingester start. Manual namespace
   assignment is needed only when upgrading rows from a queue without
   namespaces.

9. The ingester's new database API is `GET /databases/{name}`; update any
   callers of `GET /database` to follow suit.

10. Run `uv run scripts/migrate_soliplex_dbs.py --check` and
   `uv run scripts/migrate_rag_dbs.py --check`; apply any reported migrations
   with the relevant service stopped. Start the stack with
   `docker compose up -d` and inspect the backend and ingester logs.
   Then [check it](#check-it), and commit.
