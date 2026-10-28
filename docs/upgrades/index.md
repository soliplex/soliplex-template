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
| soliplex 0.84 or earlier | `backend/constraints.txt` pins below `0.85` | `template-skill-2026.10.10-37f7a2d` | [1](move-to-soliplex-0.85.md) |
| soliplex 0.85, built from PyPI | `backend/Dockerfile` runs `uv add … soliplex` | `template-skill-2026.10.14-9a17a20` | [2](build-on-soliplex-release-images.md) |
| on the 0.85 release images | `backend/Dockerfile` builds `FROM ghcr.io/soliplex/soliplex-postgres:0.85` | `template-skill-2026.10.24-932e134` | [3](move-to-soliplex-0.86.1.md) |
| soliplex 0.86.1 | `backend/Dockerfile` builds `FROM ghcr.io/soliplex/soliplex-postgres:0.86.1` | `template-skill-2026.10.26-b57f7ba` | [4](move-to-soliplex-0.87.md) |
| soliplex 0.87 | `nginx/nginx.conf` rewrites other cookies' `SameSite` via a catch-all `proxy_cookie_flags`; the ingester bearer rides an `INGESTER_TOKEN` compose environment entry | `template-skill-2026.10.28-aadeb80` | [5](serve-through-nginx.md) |

The `[tool.soliplex-template]` manifest at the end of the stack's
`pyproject.toml` records the skill commit (`skill_source_commit`) and date
(`skill_generated`) that generated it, and the parameters it was generated
with. (`skill_version` may be empty.)

!!! tip "Compare against a fresh stack"
    Each step names what the current template has. To see it in place,
    generate a throwaway stack with **the skill release the rung
    walks with** (see Before you start #1) and your own parameters,
    and diff the two; see
    [Checking the whole stack against a reference](../getting-started/migrating-layout.md#checking-the-whole-stack-against-a-reference).

!!! note "Which `haiku.rag` pairs with which soliplex?"
    The two pins are not a free pair: the ingester writes the LanceDB the
    backend reads, so its `haiku.rag-slim` release must stay inside the
    range the pinned soliplex release declares — including for
    `--params` overrides. Every rung below names its pair; the full
    generated table sits in the [appendix](#pairings-table) at the end
    of this page.

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
   (5);  earlier rungs walk with the release that generated the
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

One page per rung:

- [1: Move to soliplex 0.85](move-to-soliplex-0.85.md)
- [2: Build on the soliplex release images](build-on-soliplex-release-images.md)
- [3: Move to soliplex 0.86.1 and haiku.rag 0.101.0](move-to-soliplex-0.86.1.md)
- [4: Move to soliplex 0.87](move-to-soliplex-0.87.md)
- [5: Serve through nginx](serve-through-nginx.md)

??? note "Appendix: the generated `soliplex` ↔ `haiku.rag-slim` pairing table"

    <a id="pairings-table"></a>

    The stack couples two pins — the soliplex release and the ingester's
    `haiku.rag-slim` tag — and they are not a free pair: the ingester
    writes the LanceDB that the backend reads, so the `haiku.rag-slim`
    release must stay inside the range the pinned soliplex release
    declares. The generated table below (extracted from the soliplex
    repo's own release tags) lists that pin for every release; the rungs
    pick their pairings from it.

    --8<-- "docs/getting-started/includes/haiku-rag-pairings.md"

    A two-part tag name (e.g. `v0.85`) is the *first* release of its
    series: later patches tag their own commits and may raise the pin
    (`0.85` pins `>= 0.95.0, < 0.96`, while `0.85.4` pins
    `>= 0.97.0, < 0.98`). For the moving image tag
    (`ghcr.io/soliplex/soliplex-postgres:0.85`), the effective pairing
    is the newest patch's row.  An out-of-range pair still generates
    cleanly, and only breaks at run time — so pick every
    `haiku_rag_version` from this table, not by release number, and let
    the post-upgrade audit (the **Check it** phase, above) remain the
    net.
