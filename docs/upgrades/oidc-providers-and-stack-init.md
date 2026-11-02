# OIDC providers and stack-init

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `v0.21b1` skill release** —
its generator produces the rung's end state. The stack being upgraded names
the release that generated it in its own `[tool.soliplex-template]` manifest —
use that one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py upgrade v0.21b1
```

This rung changes no releases: soliplex, `haiku.rag`-slim and the web client
stay where rung 6 left them. It changes how the stack decides whether to
require sign-in, and adds a setup step that runs before `up`. What changes:

- the backend never runs with `--no-auth-mode`: with no `auth_systems` in
  `backend/environment/oidc/config.yaml` it runs without authentication
  anyway, and the first provider turns authentication on
- the placeholder `keycloak` provider and the `keycloak/` directory go away;
  the skill's `scripts/oidc_providers.py` adds and removes real providers
- nginx includes fragments from `nginx/conf.d/`, one of which names the
  provider the `/ingester/` gate signs in with
- the `/ingester/` gate is in every stack, not only in stacks with a provider
- a `stack-init` service brings the Soliplex databases to their current
  schema and runs the setup steps in `stack-init/init.d/`; its first step
  makes Soliplex's no-auth user an admin while the stack has no provider,
  which `--no-auth-mode` used to do implicitly

Where a step differs, it says which kind of stack it means:

- a **stack without a provider** runs the backend with `--no-auth-mode`, and
  its `oidc/config.yaml` holds only the placeholder `keycloak` entry
- a **stack with a provider** runs the backend without `--no-auth-mode`, and
  its `oidc/config.yaml` holds the provider(s) it signs in with

Skip the steps that do not apply. Each names what the current template has;
compare against a stack the rung's release generates.

1. **Stop the stack** with `docker compose down`, and back up the Postgres
   databases (see [Before you start](index.md#before-you-start)).

2. **Add the nginx fragment directory.** Copy `nginx/conf.d/README.md` from
   the reference stack, so the directory exists in git. Then, in
   `docker-compose.yml`, mount it into the `nginx` service, after the
   `mime.types` bind:

    ```yaml
          # Stack fragments (see nginx/conf.d/README.md): nginx reads them at
          # start, so restart nginx after adding or removing one.
          - type: bind
            source: ./nginx/conf.d
            target: /etc/nginx/conf.d
            read_only: true
    ```

3. **Include the fragments in both nginx servers.** In `nginx/nginx.conf`,
   after the `proxy_cookie_path / /;` line in **each** `server` block (the
   HTTP one on `9000` and the HTTPS one on `9443`):

    ```nginx
            # Stack fragments: every '*.conf' under the stack's 'nginx/conf.d/'
            # (mounted read-only at /etc/nginx/conf.d/), included by both
            # servers. Skill scripts add and remove them, e.g. a route for an
            # added service. The variable below names the provider the
            # '/ingester/' gate signs in with: none until 'oidc_providers.py'
            # adds one and writes it into 'conf.d/signin-provider.conf'.
            set $soliplex_signin_provider "";
            include /etc/nginx/conf.d/*.conf;
    ```

4. **Gate `/ingester/` in every stack.**

    Compare `nginx/nginx.conf` with the reference stack's; the gate's parts
    are:

    - the `map $http_sec_fetch_mode $soliplex_navigate` block, before the
      first `server`
    - in each server, the `location @ingester_signin` and
      `location = /_authz_check` blocks
    - in each server's `location /ingester/`, the `auth_request`,
      `auth_request_set` and `error_page 401 = @ingester_signin` lines, and
      the `X-Soliplex-User` header

    In a **stack without a provider**, add them all, as the reference has
    them. In a **stack with a provider**, they are already there: change
    each `@ingester_signin` to the reference's, which redirects to the
    provider `$soliplex_signin_provider` names (not a fixed id such as
    `keycloak`) and answers `signin_unavailable` when none is set.

5. **Stop passing `--no-auth-mode`.** In `docker-compose.yml`, delete the
   `--no-auth-mode` line from the `backend` service's `command` (in a
   **stack without a provider**; a stack with one has no such line), and
   replace the `# Temporary: run with '--no-auth-mode'` comment above it
   with the reference's.

6. **In a stack without a provider, remove the placeholder provider.** Run
   the skill's `oidc_providers.py` from the stack directory; it leaves
   `auth_systems: []` in `backend/environment/oidc/config.yaml`:

    ```bash
    uv run <skill>/scripts/oidc_providers.py remove keycloak
    ```

    Delete the `keycloak/` directory too.

7. **In a stack with a provider, name the one `/ingester/` signs in with:**
   the id its old `@ingester_signin` redirected to. Run the skill's
   `oidc_providers.py` from the stack directory; it writes
   `nginx/conf.d/signin-provider.conf`:

    ```bash
    uv run <skill>/scripts/oidc_providers.py signin <id>
    ```

    Delete the `keycloak/` directory if the stack has one.

8. **Add the `stack-init` service.** Copy the `stack-init/` directory from
   the reference stack. In `docker-compose.yml`, add the service after
   `soliplex-provisioner`; it reuses `soliplex-dev`'s build, secrets and
   environment through their YAML anchors:

    ```yaml
      # Prepares the stack before it first serves anyone, and again after a part
      # is added: brings the Soliplex databases to their current schema, then
      # runs every step in 'stack-init/init.d/' (see stack-init/README.md).
      # Every step is safe to re-run. Run it before 'docker compose up':
      #
      #   docker compose run --rm --quiet-build stack-init
      #
      # The 'soliplex-dev' image, secrets and environment (YAML aliases), so the
      # steps reach the databases as the backend does.
      stack-init:

        profiles: ["init"]

        build: *soliplex_dev_build

        env_file: ".env"
        user: "${PUID:-1000}:${PGID:-1000}"

        command: ["sh", "/stack-init/run.sh"]

        depends_on:
          postgres:
            condition: service_healthy

        secrets: *soliplex_dev_secrets

        environment: *backend_environment

        volumes:
          - ./backend/environment:/environment/
          - ./src:/app/src:ro
          - ./stack-init:/stack-init:ro
    ```

9. **Run `stack-init`, then start.**

    ```bash
    docker compose run --rm --quiet-build stack-init
    docker compose up -d
    ```

    In a **stack without a provider**, `--no-auth-mode` already made the
    no-auth user an admin, so `stack-init` reports that it is one. In a
    **stack with a provider**, it adds no admin.

10. [**Check it**](index.md#check-it).

    Then open `/ingester/` through nginx. In a **stack without a provider**,
    the dashboard opens with no sign-in.

    In a **stack with a provider**, it redirects to that provider's sign-in,
    and admits an admin after it.

    Commit.
