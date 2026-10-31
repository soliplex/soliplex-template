# Provision rooms on request

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.31-750fcbd` skill release** —
its generator produces the rung's end state. The stack being upgraded names
the release that generated it in its own `[tool.soliplex-template]` manifest —
use that one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.31-750fcbd
```

This rung moves the stack to soliplex `0.89` and `haiku.rag`-slim `0.104.0`,
and adds the room provisioner. What changes:

- the releases: soliplex `0.87.0` to the moving `0.89` image tag, and the
  ingester from `haiku.rag`-slim `0.101.0` to `0.104.0`; neither brings a
  RAG store migration, and the backend migrates its own databases on
  startup
- the web client: frontend `0.110` to `0.114`
- the image builds freeze the soliplex image's packages as version pins, so
  an extra package that depends on soliplex installs
- room uploads move from `backend/uploads/` to a top-level `uploads/`
- the room provisioner: a `soliplex-provisioner` service, the provisioned
  rooms' clone under `src/rooms-provisioned`, their shared
  `provisioned_rag` agent, and their database fragments, which the
  ingester includes
- healthchecks check starting services every 2 seconds
- in stacks with Gitea, its settings for the concierge's tracking issues

Skip the steps that do not apply. Each names what the current template has;
compare against a stack the rung's release generates.

1. **Stop the stack** with `docker compose down`, and back up the Postgres
   databases and `rag/db/` (see [Before you start](index.md#before-you-start)).

2. **Bump the releases.**

    - the `FROM` line in `backend/Dockerfile`: `soliplex-postgres:0.87.0`
      becomes `soliplex-postgres:0.89` (and in `tui/Dockerfile`, if the stack
      ships the TUI, `soliplex-tui:0.89`)
    - the ingester image in `docker-compose.yml` (the `&haiku_rag_image`
      anchor): `haiku.rag-slim:0.101.0` becomes `haiku.rag-slim:0.104.0`
    - the `FROM` line in `nginx/Dockerfile`: `frontend-web:0.110` becomes
      `frontend-web:0.114`
    - the manifest in `pyproject.toml`: `soliplex_image_tag = "0.89"`,
      `soliplex_dev_constraint = ">= 0.89, < 0.90"`,
      `haiku_rag_version = "0.104.0"` and `frontend_version = "0.114"`
    - the `soliplex` requirement in the root and package `pyproject.toml`
      files: `>= 0.89, < 0.90`; then run `uv lock` in each project

3. **Freeze the image's packages as version pins.** In
   `backend/Dockerfile`, and twice in `soliplex-dev/Dockerfile`, the
   constraints step changes from

    ```dockerfile
          uv pip freeze --python /app/.venv/bin/python \
    ```

    to

    ```dockerfile
          uv pip list --format freeze --python /app/.venv/bin/python \
    ```

4. **Move room uploads to the top level.**

    ```bash
    mv backend/uploads uploads
    ```

    Then, in `docker-compose.yml`:

    - in the `backend` service, the uploads bind's `source: "backend/uploads"`
      becomes `source: "uploads"`
    - in the `soliplex-dev` service, `./backend/uploads:/uploads/` becomes
      `./uploads:/uploads/`

    And in `.gitignore`, the four `backend/uploads/...` lines become:

    ```gitignore
    uploads/rooms/*
    uploads/threads/*
    !uploads/rooms/.gitkeep
    !uploads/threads/.gitkeep
    ```

5. **Update `backend/environment/installation.yaml`.**

    - The `URL_SAFE_TOKEN_SECRET` secret reads the stack's
      `url_safe_token_secret` Docker secret first, so the backend and the
      provisioner sign and verify service tokens with one key. Add this
      source above its `env_var` source:

        ```yaml
              - kind: "file_path"
                file_path: /run/secrets/url_safe_token_secret
        ```

    - Add the provisioned rooms' agent to `agent_configs`, before the
      `title` entry (with the stack's own chat model):

        ```yaml
          - id: "provisioned_rag"
            model_name: "gpt-oss:latest"
            system_prompt: |
              You are a helpful agent.

              Answer from this room's knowledge base: use the 'rag' skill to ground
              every answer, and cite the documents you used. If the knowledge base
              does not contain the answer, say so plainly.
        ```

    - Add the provisioned rooms' clone to `room_paths`:

        ```yaml
        room_paths:
          - "./rooms"
          - "/app/src/rooms-provisioned"
        ```

6. **Let the ingester include the provisioned rooms' fragments.** In
   `haiku.rag/haiku.rag.yaml`, add, after the `ingester:` section:

    ```yaml
    lancedb:
      databases:
        haiku.rag: /data/haiku.rag.lancedb

    databases_include: rooms-provisioned/databases.d
    ```

    If the file has a `lancedb.databases` map already (see
    [Ingesting multiple databases](../users/operations/rag.md#ingesting-multiple-databases)),
    keep it, and make sure it lists every database in `ingester.databases`,
    `haiku.rag` included: haiku.rag requires that once the fragments place
    theirs.

7. **Give `soliplex-dev` the provisioner.** In `soliplex-dev/Dockerfile`,
   after `FROM backend`:

    ```dockerfile
    ARG SOLIPLEX_TEMPLATE="soliplex-template[provisioner]>=0.20"
    ```

    and, after `USER root`:

    ```dockerfile
    RUN apt-get update && \
        apt-get install -y --no-install-recommends git && \
        rm -rf /var/lib/apt/lists/*

    RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
        --mount=type=cache,target=/root/.cache/uv \
        uv pip list --format freeze --python /app/.venv/bin/python \
          > /tmp/base-constraints.txt && \
        uv pip install --python /app/.venv/bin/python \
          --constraints /tmp/base-constraints.txt \
          "$SOLIPLEX_TEMPLATE"
    ```

8. **Wire the provisioner into `docker-compose.yml`.**

    - In the `soliplex-dev` service, name its `build`, `secrets` and
      `volumes` with anchors, so the new service can share them; add the
      `ingester_bearer` secret; and mount the ingester's config read-only:

        ```yaml
            build: &soliplex_dev_build
              context: soliplex-dev
              additional_contexts:
                backend: "service:backend"

            secrets: &soliplex_dev_secrets
              - agui_db_password
              - authz_db_password
              - url_safe_token_secret
              # The room provisioner's credential for the ingester's '/reload'.
              - ingester_bearer

            volumes: &soliplex_dev_volumes
              - ./backend/environment:/environment/
              - ./backend/sandbox/workdirs:/sandbox/workdirs
              - ./uploads:/uploads/
              - ./rag/db:/db
              # src/ read-WRITE; the backend mounts the same tree read-only.
              - ./src:/app/src
              # The ingester's config: the room provisioner checks that it
              # includes the provisioned rooms' database fragments.
              - ./haiku.rag/haiku.rag.yaml:/app/haiku.rag.yaml:ro
        ```

    - Add the `soliplex-provisioner` service, after `soliplex-dev`:

        ```yaml
          soliplex-provisioner:

            build: *soliplex_dev_build

            env_file: ".env"
            user: "${PUID:-1000}:${PGID:-1000}"

            working_dir: /app/src

            command:
              - /app/.venv/bin/uvicorn
              - --factory
              - soliplex_template.provision_service:create_app
              - --host=0.0.0.0
              - --port=8090

            depends_on:
              postgres:
                condition: service_healthy

            healthcheck:
              test:
                - "CMD"
                - "python"
                - "-c"
                - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8090/ok', timeout=5).status == 200 else 1)"
              interval: 30s
              timeout: 10s
              retries: 4
              start_period: 60s
              start_interval: 2s

            restart: unless-stopped

            secrets: *soliplex_dev_secrets

            environment: *backend_environment

            volumes: *soliplex_dev_volumes
        ```

    - In the `haiku-ingester` service's volumes (the `&haiku_rag_volumes`
      anchor, which the one-shot `haiku-rag` service shares), add the
      provisioned rooms' clone and the room uploads:

        ```yaml
              # The provisioned-rooms clone: its 'databases.d' holds each
              # provisioned room's database fragment, which haiku.rag.yaml
              # includes.
              - ./src/rooms-provisioned:/app/rooms-provisioned:ro
              # Room uploads, the corpus of each room's own database.
              - ./uploads/rooms:/uploads/rooms:ro
        ```

    - Check starting services often: add to the `backend` healthcheck

        ```yaml
              start_period: 120s
              start_interval: 2s
        ```

        and to the `postgres` healthcheck

        ```yaml
              start_period: 30s
              start_interval: 2s
        ```

    - In stacks with Gitea, add to the `gitea` service's `environment`
      (after `GITEA__server__ROOT_URL`):

        ```yaml
              GITEA__server__PUBLIC_URL_DETECTION: "legacy"
              GITEA__attachment__ALLOWED_TYPES: ".avif,.cpuprofile,.csv,.dmp,.docx,.fodg,.fodp,.fods,.fodt,.gif,.gz,.jpeg,.jpg,.json,.jsonc,.log,.md,.mov,.mp4,.odf,.odg,.odp,.ods,.odt,.patch,.pdf,.png,.pptx,.svg,.tgz,.txt,.webm,.webp,.xls,.xlsx,.yaml,.yml,.zip"
        ```

        Gitea then builds its links from `ROOT_URL` whichever host a request
        came through, and accepts the concierge's `user-profile.yaml`
        attachments.

9. **Create the provisioned rooms' clone directory**, so Docker does not
   create the ingester's bind-mount source owned by root:

    ```bash
    mkdir -p src/rooms-provisioned
    ```

    The stack's `.gitignore` already ignores everything under `src/` but
    its own project; the provisioner makes the directory a git repository
    of its own.

10. **Rebuild and start.**

    ```bash
    docker compose build --pull backend soliplex-dev soliplex-provisioner nginx
    docker compose up -d
    ```

    (Add `tui` to the build in stacks that ship it.) The `soliplex-dev` and
    `soliplex-provisioner` builds install `soliplex-template[provisioner]`
    0.20 from PyPI.

    If the web client then stops at "probing providers", it is holding
    credentials stored by the old client: clear the browser's site data for
    the stack's address, and reload.

11. **In stacks with Gitea, provision its rooms repository.** Run the
    stack's Gitea setup again: it is idempotent, and it now also creates
    the private `soliplex-admin/rooms-provisioned` repository the
    provisioner pushes to. It rotates the service account's token, so
    recreate the services that read `.env` afterwards:

    ```bash
    uv run scripts/init_gitea.py
    docker compose up -d backend soliplex-provisioner
    ```

12. **Admit the provisioner, in auth-mode stacks.** The provisioner calls
    the backend with service tokens whose subject is `soliplex-provisioner`;
    admit them as an admin once (see
    [Room provisioning](../users/operations/room-provisioning.md#with-oidc-on)):

    ```bash
    docker compose run --rm --quiet-build soliplex-dev \
        /app/.venv/bin/soliplex-cli admin-users add /environment \
        --json-path '$[?$.sub == "soliplex-provisioner"]'
    ```

    Until this entry exists, `soliplex-provisioner` reports unhealthy: its
    `/ok` answers `503`, naming the problem, and its log names the
    `admin_users` entry it needs. It rechecks its access every 15 seconds,
    so it turns healthy without a restart.

    To let users other than admins request rooms through the provisioner
    service, write a policy for the reserved room id `__provision__` (see
    [The provisioner service](../users/operations/room-provisioning.md#the-provisioner-service)).

13. [**Check it**](index.md#check-it). Then provision a test room, and
    check that it is live:

    ```bash
    uv run <skill>/scripts/provision_room.py --project-dir . \
        --username alice --name "Upgrade check"
    ```

    Commit.
