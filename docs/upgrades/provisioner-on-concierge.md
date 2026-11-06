# The provisioner on soliplex-concierge

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `v0.21` skill release** —
its generator produces the rung's end state. The stack being upgraded names
the release that generated it in its own `[tool.soliplex-template]` manifest —
use that one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py upgrade v0.21
```

This rung changes no releases: soliplex, `haiku.rag`-slim and the web client
stay where rung 7 left them. It moves the room provisioner onto
soliplex-concierge's provisioner framework, which `soliplex-template` 0.21
depends on (`soliplex-concierge[provisioner] >= 0.14`). What changes:

- the `soliplex-provisioner` service runs a new factory,
  `soliplex_template.provisioner.wiring:create_app`
- `stack-init`'s provisioner step imports from the new
  `soliplex_template.provisioner` package
- the backend's environment, which `soliplex-dev`, `soliplex-provisioner`
  and `stack-init` share, sets `SOLIPLEX_INSTALLATION_PATH`, so
  `soliplex-cli` commands no longer need the installation's path
- each room provisioned from now on records its request and its policy in
  the provisioned-rooms clone (`provision.yaml`, `authz.yaml`), and
  `soliplex-provision-room --reconcile` replays them (see
  [Room provisioning](../users/operations/room-provisioning/index.md))
- in a stack with the concierge, reinstallint the about room enables
  the new provisioning features (see
  [Rooms for someone else](../users/operations/room-provisioning/service.md#rooms-for-someone-else))

The old factory (`soliplex_template.provision_service:create_app`) and
`stack-init`'s old import still work in `soliplex-template` 0.21, so a stack
that skips steps 3 and 4 keeps running. They are deprecated, and go in a
later release.

Where a step differs, it says which kind of stack it means:

- a **stack with the concierge** has a `soliplex-concierge` line in
  `backend/requirements.txt`, and an `about-<project>` room
- a **stack without the concierge** has neither

Skip the steps that do not apply. Each names what the current template has;
compare against a stack the rung's release generates.

1. **Stop the stack** with `docker compose down`. This rung changes no
   database, so no backup is needed beyond your usual one.

2. **In a stack with the concierge, raise its pin to 0.14.** In
   `backend/requirements.txt`, change the `soliplex-concierge` line's
   version to `0.14` (for example `soliplex-concierge[truststore]==0.14`).
   `soliplex-dev` installs `soliplex-template` 0.21 with the backend image's
   versions as constraints, so an older concierge pin makes its build fail.

3. **Run the new provisioner factory.** In `docker-compose.yml`, in the
   `soliplex-provisioner` service's `command`:

    ```yaml
          - soliplex_template.provisioner.wiring:create_app
    ```

    replacing `soliplex_template.provision_service:create_app`.

4. **Update `stack-init`'s provisioner step.** In
   `stack-init/init.d/20-provisioner-admin.py`, replace
   `from soliplex_template import provision` with:

    ```python
    from soliplex_template.provisioner import adapters
    ```

    and `provision.DEFAULT_SERVICE_SUBJECT` with
    `adapters.DEFAULT_SERVICE_SUBJECT`.

5. **Set the installation path.** In `docker-compose.yml`, add to the
   `backend` service's `environment` (the `&backend_environment` block),
   after `PYTHONPATH`:

    ```yaml
          # 'soliplex-cli' commands work w/o explicit environment argument
          SOLIPLEX_INSTALLATION_PATH: /environment
    ```

6. **Install `soliplex-template` 0.21 in `soliplex-dev`.** In
   `soliplex-dev/Dockerfile`, change the `SOLIPLEX_TEMPLATE` default:

    ```dockerfile
    ARG SOLIPLEX_TEMPLATE="soliplex-template[provisioner]>=0.21"
    ```

7. **Rebuild, run `stack-init`, then start.** The backend image rebuilds in
   a stack with the concierge, for its new pin; `soliplex-dev` and
   `soliplex-provisioner` rebuild in every stack:

    ```bash
    docker compose build backend soliplex-dev soliplex-provisioner
    docker compose run --rm --quiet-build stack-init
    docker compose up -d
    ```

8. **In a stack with the concierge, reinstall the about room** with the
   `soliplex-concierge-installer` skill from soliplex-concierge's `v0.14`
   release (installer 0.7.0 or later), passing `--force` and the Gitea
   owner and repository you installed it with. Its prompt now lets an admin
   request a room for someone else. `--force` replaces the room's files:
   compare `git diff` before you commit, and carry over any edits of your
   own. The reinstalled `room_config.yaml` starts with an
   `# Installed by soliplex-concierge-installer <version>` line.

9. **Reconcile, to see where the clone stands.** Rooms provisioned before
   this rung have no `authz.yaml`; reconcile names them and leaves their
   policies alone:

    ```bash
    docker compose run --rm --quiet-build soliplex-dev \
        /app/.venv/bin/soliplex-provision-room --reconcile
    ```

10. [**Check it**](index.md#check-it).

    Then check that the provisioner service answers (`GET /ok` through the
    compose network, or `docker compose ps soliplex-provisioner` reporting
    it healthy).

    Commit.
