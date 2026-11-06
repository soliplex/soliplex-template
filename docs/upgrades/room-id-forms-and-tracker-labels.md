# Room id forms and tracker labels

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `v0.22` skill release** —
its generator produces the rung's end state. The stack being upgraded names
the release that generated it in its own `[tool.soliplex-template]` manifest —
use that one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py upgrade v0.22
```

This rung changes no releases: soliplex, `haiku.rag`-slim and the web client
stay where rung 8 left them. It makes the room id's form configurable, tags
the tracking issues with their provision status, and `soliplex-template` 0.22
depends on `soliplex-concierge[provisioner] >= 0.15` for. What changes:

- the room id's form is a setting (`PROVISIONER_ROOM_ID_SEPARATOR`; default
  `--`, the concierge's; `:` keeps the `foremen:quarry-notes` form rooms
  provisioned before rung 8 carry), as is the owner part's case
  (`PROVISIONER_LOWERCASE_OWNER`, default `true`)
- `soliplex-provision-room --reconcile` honors the configured form: before,
  it read the clone against `--` whatever it held
- in stacks with Gitea, `scripts/init_gitea.py` creates the tracking
  repository's provision-status labels (`provision-start`,
  `provision-complete`, `provision-incomplete`, `provision-failed`); the
  provisioner files `new-room` issues tagged `provision-start`, and tags one
  of the others when the provision resolves or fails (see
  [Room provisioning](../users/operations/room-provisioning.md))

Where a step differs, it says which kind of stack it means:

- a **stack with provisions** has directories under `src/rooms-provisioned/`
- a **stack with the concierge** has a `soliplex-concierge` line in
  `backend/requirements.txt`, and an `about-<project>` room
- a **stack with Gitea** has `GITEA_HOST` set in `.env`

Skip the steps that do not apply. Each names what the current template has;
compare against a stack the rung's release generates.

1. **Stop the stack** with `docker compose down`. This rung changes no
   database, so no backup is needed beyond your usual one.

2. **In a stack with provisions, choose the room id form.** Listing the
   clone shows which forms a stack's rooms live in:

   ```bash
   ls src/rooms-provisioned/
   ```

   Rooms provisioned before rung 8 are in the `:` form; rooms provisioned
   under it follow the framework's default, `--`. In `.env`, set the form
   the stack keeps:

   ```bash
   # Keep the room ids' legacy ':' form
   PROVISIONER_ROOM_ID_SEPARATOR=:
   ```

   Stacks whose rooms are all `--`-form, or that provisioned none, leave it
   unset: `--` is the default everywhere. A clone holding both forms: set
   the form new provisions should file, and reconcile reads the rooms that
   match; rooms in the other form keep working as rooms, but after a
   `docker compose down -v` their stored `authz.yaml` records must be
   replayed by hand, `room-authz from-yaml` per room from each clone
   directory. There is no rename tool yet.

3. **In a stack with the concierge, raise its pin to 0.15.** In
   `backend/requirements.txt`, change the `soliplex-concierge` line's
   version (for example `soliplex-concierge[truststore]==0.14` to
   `==0.15`). `soliplex-dev` installs `soliplex-template` 0.22 with the
   backend image's versions as constraints, so an older concierge pin makes
   its build fail.

4. **Install `soliplex-template` 0.22 in `soliplex-dev`.** In
   `soliplex-dev/Dockerfile`, change the `SOLIPLEX_TEMPLATE` default:

    ```dockerfile
    ARG SOLIPLEX_TEMPLATE="soliplex-template[provisioner]>=0.22"
    ```

5. **Rebuild and start.** The backend image rebuilds in a stack with the
   concierge, for its new pin; `soliplex-dev` and `soliplex-provisioner`
   rebuild in every stack:

   ```bash
   docker compose build backend soliplex-dev soliplex-provisioner
   docker compose up -d
   ```

6. **In a stack with Gitea, create the labels.** Run the stack's Gitea
   setup again: it is idempotent, and it now also creates the tracking
   repository's provision-status labels. Until it runs, a provision cannot
   file: the provisioner tags the issue as it files, and the labels do not
   exist yet. It rotates the service account's token, so recreate the
   services that read `.env` afterwards:

   ```bash
   uv run scripts/init_gitea.py
   docker compose up -d backend soliplex-provisioner
   ```

7. **Reconcile, to see where the clone stands.** Rooms whose ids parse the
   configured form are named in the report; rooms without an `authz.yaml`
   are reported, and their policies left alone:

   ```bash
   docker compose run --rm --quiet-build soliplex-dev \
       /app/.venv/bin/soliplex-provision-room --reconcile
   ```

8. [**Check it**](index.md#check-it).

    Then check that the provisioner service answers (`GET /ok` through the
    compose network, or `docker compose ps soliplex-provisioner` reporting
    it healthy).

    Commit.
