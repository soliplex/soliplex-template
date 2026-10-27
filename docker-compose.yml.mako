# Usage:
# docker compose up
name: ${project_name}

services:

  nginx:

    build:
      context: nginx
      dockerfile: Dockerfile
      args:
        PUID: <%text>${PUID:-1000}</%text>
        PGID: <%text>${PGID:-1000}</%text>

    depends_on:
      - backend
      - haiku-ingester
% if include_tui:
      - tui
% endif
% if include_gitea:
      - gitea
% endif

    ports:
      - "${nginx_http}:9000"
      - "${nginx_https}:9443"

    volumes:
      - type: bind
        source: ./nginx/nginx.conf
        target: /etc/nginx/nginx.conf
        read_only: true
      - type: bind
        source: ./nginx/mime.types
        target: /etc/nginx/mime.types
        read_only: true
      - type: bind
        source: ./nginx/error-pages
        target: /app/error-pages
        read_only: true

    secrets:
      - ingester_bearer

    #
    # '$$' is Compose's escape for a literal '$': the container's shell sees
    # '$(cat ...)'.
    #
    # Tokens are generated as [A-Za-z0-9]{32} (scripts/generate_secrets.py),
    # so the sed below cannot trip on metacharacters in its replacement.
    command:
      - sh
      - -ec
      - |
        # Fail loudly on a missing or empty token.
        test -s /run/secrets/ingester_bearer || {
            echo "error: /run/secrets/ingester_bearer missing or empty; run 'uv run scripts/generate_secrets.py'" >&2
            exit 1
        }
        # nginx cannot read a secret file inside 'location' blocks, nor
        # environment references in its configuration: substitute the
        # ingester bearer into a copy of the config instead.
        sed "s|@@INGESTER_BEARER@@|$$(cat /run/secrets/ingester_bearer)|g" /etc/nginx/nginx.conf > /tmp/nginx.conf
        exec nginx -c /tmp/nginx.conf -g 'daemon off;'

% if include_tui:
  tui:

    build:
      context: tui
      dockerfile: Dockerfile
      args:
        PUID: <%text>${PUID:-1000}</%text>
        PGID: <%text>${PGID:-1000}</%text>

    depends_on:
      - backend

    user: "<%text>${PUID:-1000}</%text>:<%text>${PGID:-1000}</%text>"

    # No host port mapping: the TUI is reached only through nginx so that the
    # '--public-url' baked into the served HTML (used for static assets and
    # the websocket) stays consistent with what the browser sees.
    command: >-
      /app/.venv/bin/soliplex-tui-serve
      --backend-url http://backend:8000
      --host 0.0.0.0
      --port 8002
      --public-url https://${server_name}:${nginx_https}/tui

% endif
  backend:

    build:
      context: backend
      dockerfile: Dockerfile
      args:
        PUID: <%text>${PUID:-1000}</%text>
        PGID: <%text>${PGID:-1000}</%text>

    env_file: ".env"
    user: "<%text>${PUID:-1000}</%text>:<%text>${PGID:-1000}</%text>"

    # Lets the 'bubble-sandbox' skill run code (generator: 'enable_sandbox').
    # 'bwrap' needs user namespaces and a fresh '/proc', which Docker's default
    # seccomp profile, AppArmor profile and '/proc' masking each block, so all
    # three are lifted. That relaxes this container's own hardening in
    # exchange for 'bwrap' isolating each execution inside it. Without it, a
    # room that configures the skill fails when used ("Sandbox unavailable").
    # Keep 'soliplex-dev' below in step.
% if enable_sandbox:
    security_opt: &sandbox_security_opt
      - seccomp=unconfined
      - apparmor=unconfined
      - systempaths=unconfined
% else:
    # security_opt: &sandbox_security_opt
      # - seccomp=unconfined
      # - apparmor=unconfined
      # - systempaths=unconfined
% endif

    depends_on:
      - postgres

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

    secrets:
      - agui_db_password
      - authz_db_password
      - url_safe_token_secret

    environment: &backend_environment
      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>
      # Put this project's own package directory on the
      # backend's import path so Soliplex can resolve
      # dotted tool / router names (see './src' below).
      # Append ':/app/src/<other>/src' for each further
      # project directory whose code you want importable.
      PYTHONPATH: /app/src/${package_name}/src

    volumes:
      - type: bind
        source: "backend/environment"
        target: "/environment/"

      - type: bind
        source: "backend/sandbox/workdirs"
        target: "/sandbox/workdirs"

      - type: bind
        source: "backend/uploads"
        target: "/uploads/"

      - type: bind
        source: "rag/db/"
        target: "/db"

      # Every project directory under src/: this
      # project's own, plus any repo cloned alongside
      # it. Only the package dirs named in PYTHONPATH
      # are importable.
      - type: bind
        source: "./src"
        target: "/app/src"
        read_only: true

% if backend_port:
    # Direct access to the backend API.
    ports:
      - "${backend_port}:8000"
% endif

    # Temporary: run with '--no-auth-mode'
    command: >-
      /app/.venv/bin/soliplex-cli serve
% if backend_auth_flag:
      --no-auth-mode
% endif
      --reload=config
      --reload-dirs=/app/src/${package_name}/src
      --host=0.0.0.0
      --proxy-headers
      --forwarded-allow-ips=*
      /environment

  # One-shot dev task runner. 'profiles' keeps it out of 'docker compose up';
  # it exists for commands that need the stack's config, secrets and databases
  # *and* write access to the project directories under src/.
  #
  # It is built from the backend image and mirrors that service's secrets,
  # mounts and environment -- the same '/app/src' paths and, via the YAML
  # alias below, the very same PYTHONPATH -- so 'soliplex-cli audit' here
  # validates the installation the backend actually serves, and the two cannot
  # drift. The one difference is that src/ is mounted read-WRITE.
  #
  #   docker compose run --rm soliplex-dev \
  #       /app/.venv/bin/soliplex-cli audit /environment
  #
  #   docker compose run --rm -w /app/src/soliplex soliplex-dev \
  #       /app/.venv/bin/alembic -x soliplex.installation_path=/environment \
  #       revision --autogenerate -m "add a column"
  #
  # Pass '-w /app/src/<project>' for anything that must run from a project's
  # own root: alembic writes into its versions/ directory and prepends '.' to
  # sys.path. Any project directory under src/ works, not just a soliplex
  # checkout.
  soliplex-dev:

    profiles: ["devmode"]

    # The backend's image plus every src/ project's 'dev' dependency group
    # (see soliplex-dev/Dockerfile): 'service:backend' is the image compose
    # builds for that service, so this one never drifts from it.
    build:
      context: soliplex-dev
      additional_contexts:
        backend: "service:backend"

    env_file: ".env"
    user: "<%text>${PUID:-1000}</%text>:<%text>${PGID:-1000}</%text>"

    working_dir: /app/src

    # The backend's 'security_opt' (see there), so code runs here too.
% if enable_sandbox:
    security_opt: *sandbox_security_opt
% else:
    # security_opt: *sandbox_security_opt
% endif

    depends_on:
      postgres:
        condition: service_healthy

    secrets:
      - agui_db_password
      - authz_db_password
      - url_safe_token_secret

    environment: *backend_environment

    # Short volume form throughout, deliberately: the long form of the
    # 'rag/db/' bind is the anchor scripts/refresh_skill_template.py uses to
    # find the *backend* service, and that anchor has to stay unique.
    volumes:
      - ./backend/environment:/environment/
      - ./backend/sandbox/workdirs:/sandbox/workdirs
      - ./backend/uploads:/uploads/
      - ./rag/db:/db
      # src/ read-WRITE; the backend mounts the same tree read-only.
      - ./src:/app/src

  haiku-ingester:

    image: &haiku_rag_image ghcr.io/ggozad/haiku.rag-slim:${haiku_rag_version}

    # To run a locally-built ingester image instead of the published
    # tag, see 'haiku.rag/README.md'.
    #build:
    #  context: haiku.rag
    #  dockerfile: Dockerfile

    command:
      - sh
      - -ec
      - |
        # haiku.rag interpolates environment references in its own config
        # (0.57+), so the ingester reads the bind-mounted yaml directly -- no
        # rendered copy. Pending a possible direct read (ggozad/haiku-rag#749),
        # read secrets files into the environment variables expected by its
        # interpolation.
        # - queue password:
        export INGESTER_DB_PASSWORD="$$(cat /run/secrets/ingester_db_password)"
        # - control plane bearer token:
        export INGESTER_TOKEN="$$(cat /run/secrets/ingester_bearer)"
        # The queue role owns its schema; prepare it before serving.
        haiku-ingester --config /app/haiku.rag.yaml queue migrate
        # '--root-path /ingester' mounts the API (and the dashboard's
        # <base href>) under the public prefix so nginx's '/ingester/'
        # locations strip it before proxying.
        exec haiku-ingester --config /app/haiku.rag.yaml serve --root-path /ingester

% if ingester_port:
    # Direct control-plane access; every request carries the bearer.
    ports:
      - "${ingester_port}:8765"  # control plane: /health, /jobs, /sources, /dlq, /stats
% endif

    secrets:
      - ingester_db_password
      - ingester_bearer

    volumes: &haiku_rag_volumes
      - ./rag/db:/data
      - ./${docs_dir}:/docs
      - ./haiku.rag/haiku.rag.yaml:/app/haiku.rag.yaml:ro

    environment: &haiku_rag_environment
      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>
      # API keys (set as needed)
      #- OPENAI_API_KEY=<%text>${OPENAI_API_KEY}</%text>
      #- ANTHROPIC_API_KEY=<%text>${ANTHROPIC_API_KEY}</%text>
      #- VOYAGE_API_KEY=<%text>${VOYAGE_API_KEY}</%text>
      #- CO_API_KEY=<%text>${CO_API_KEY}</%text>

    user: "<%text>${PUID:-1000}</%text>:<%text>${PGID:-1000}</%text>"

    # LanceDB's full-text-search (content_fts) index merge opens many
    # files at once; the default soft limit of 1024 trips EMFILE
    # ("Too many open files") and the merge is skipped. Raise it.
    ulimits: &haiku_rag_ulimits
      nofile:
        soft: 65536
        hard: 65536

    depends_on:
      docling-serve:
        condition: service_healthy
      postgres:
        condition: service_healthy

    # shutdown_grace_s defaults to 60s — give Docker enough to drain
    # in-flight jobs before SIGKILL.
    stop_grace_period: 180s

    healthcheck:
      # slim image has no curl; use stdlib instead against localhost,
      # no proxy-only `ingester/' prefix.
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8765/health', timeout=2).status == 200 else 1)"
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 30s

    restart: unless-stopped

  # One-shot 'haiku-rag' CLI runner: the same image, mounts, environment and
  # secret as the ingester, held out of 'docker compose up' by its profile
  # ('docker compose run' enables the profile itself). Everything after the
  # service name is passed straight to 'haiku-rag':
  #
  #   docker compose run --rm haiku-rag info --db /data/haiku.rag.lancedb
  #   docker compose run --rm haiku-rag migrate --db /data/haiku.rag.lancedb
  #
  # The entrypoint exports INGESTER_DB_PASSWORD from the Docker secret before
  # exec'ing the CLI, which every call needs and no caller should have to
  # arrange: haiku.rag.yaml interpolates that variable into the queue dburi
  # and the loader expands the whole file up front, so an unset value fails
  # before the subcommand runs. The trailing 'haiku-rag' is the inner shell's
  # '$$0'; '"$$@"' is the arguments Compose appends.
  #
  # Reads are safe while the stack is up; anything that WRITES a store needs
  # 'docker compose stop haiku-ingester' first (LanceDB allows one writer).
  haiku-rag:

    profiles: ["tools"]

    image: *haiku_rag_image

    # To run a locally-built one-shot image instead of the published
    # tag, see 'haiku.rag/README.md'.
    #build:
    #  context: haiku.rag
    #  dockerfile: Dockerfile

    entrypoint:
      - sh
      - -c
      - |
        export INGESTER_DB_PASSWORD="$$(cat /run/secrets/ingester_db_password)"
        # The shared haiku.rag.yaml interpolates INGESTER_TOKEN as well (it
        # describes the 'haiku-ingester' service's API), so the CLI needs it
        # in the environment even though it never calls the API itself.
        export INGESTER_TOKEN="$$(cat /run/secrets/ingester_bearer)"
        exec haiku-rag --config /app/haiku.rag.yaml "$$@"
      - haiku-rag

    volumes: *haiku_rag_volumes

    environment: *haiku_rag_environment

    user: "<%text>${PUID:-1000}</%text>:<%text>${PGID:-1000}</%text>"

    secrets:
      - ingester_db_password
      - ingester_bearer

    ulimits: *haiku_rag_ulimits

  docling-serve:

    # The image is chosen at generation ('docling_serve_variant' /
    # 'docling_serve_version');  re-point by editing this line and
    # committing — see docling-serve/README.md (variants, tagging
    # policy, pairing with the pinned haiku.rag, a local-build recipe).
    image: ${docling_serve_image}

    # To run a locally-built converter image instead of the published
    # tag, see 'docling-serve/README.md'.
    #build:
    #  context: docling-serve
    #  dockerfile: Dockerfile

    environment:
      DOCLING_SERVE_ENABLE_UI: 1

% if docling_port:
    ports:
      - "${docling_port}:5001"
% endif

    restart: unless-stopped

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
      start_interval: 5s

  postgres:

    build:
      context: postgres
      dockerfile: Dockerfile
      args:
        PUID: <%text>${PUID:-1000}</%text>
        PGID: <%text>${PGID:-1000}</%text>

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

    environment:
      AUTO_CREATE_DATABASE: <%text>${AUTO_CREATE_DATABASE:-1}</%text>
      AGUI_DB_PASS_FILE: /run/secrets/agui_db_password
      AUTHZ_DB_PASS_FILE: /run/secrets/authz_db_password
% if include_gitea:
      GITEA_DB_PASS_FILE: /run/secrets/gitea_db_password
% endif
      INGESTER_DB_PASS_FILE: /run/secrets/ingester_db_password
      POSTGRES_INITDB_ARGS: "-A scram-sha-256"
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_USER: postgres

    volumes:
      - type: "bind"
        source: "./postgres/config"
        target: "/docker-entrypoint-initdb.d"
        read_only: true
        bind:
          selinux: "z"  # mark as shared

      - type: "volume"
        source: "postgres_data"
        target: "/var/lib/postgresql"

    tmpfs:
      - /var/run/postgresql:uid=<%text>${PUID:-1000}</%text>,gid=<%text>${PGID:-1000}</%text>
      - /tmp
    secrets:
      - source: agui_db_password
      - source: authz_db_password
% if include_gitea:
      - source: gitea_db_password
% endif
      - source: ingester_db_password
      - source: postgres_password
% if postgres_port:
    ports:
      - "${postgres_port}:5432"
% endif

% if include_gitea:
  gitea:

    # See: https://docs.gitea.com/installation/install-with-docker-rootless
    image: docker.gitea.com/gitea:1.26.0-rootless

    depends_on:
      postgres:
        condition: service_healthy

    environment:
      GITEA__database__DB_TYPE: postgres
      GITEA__database__HOST: "postgres:5432"
      GITEA__database__NAME: soliplex_gitea
      GITEA__database__USER: soliplex_gitea
      GITEA__database__PASSWD__FILE: /run/secrets/gitea_db_password
      GITEA__database__SSL_MODE: disable
      GITEA__server__HTTP_PORT: "3000"
      GITEA__server__SSH_DOMAIN: "localhost"
      GITEA__server__SSH_PORT: "2222"
      GITEA__server__SSH_LISTEN_PORT: "2222"
      # Reverse-proxied under /gitea/ by nginx on the HTTPS port (9443);
      # trailing slash is REQUIRED.  Override in .env if you front the
      # stack with another proxy or use a different hostname.
      GITEA__server__ROOT_URL: "<%text>${GITEA_ROOT_URL:-https://</%text>${server_name}:${nginx_https}<%text>/gitea/}</%text>"
      # Boot ready (skip the web installer); the DB is configured above.
      GITEA__security__INSTALL_LOCK: "true"

    secrets:
      - gitea_db_password

    volumes:
      - type: volume
        source: gitea_data
        target: /var/lib/gitea
      - type: volume
        source: gitea_config
        target: /etc/gitea
      - type: bind
        source: /etc/timezone
        target: /etc/timezone
        read_only: true
      - type: bind
        source: /etc/localtime
        target: /etc/localtime
        read_only: true

% if gitea_http_port and gitea_ssh_port:
    ports:
      - "${gitea_http_port}:3000"
      - "${gitea_ssh_port}:2222"
% elif gitea_http_port:
    ports:
      - "${gitea_http_port}:3000"
% elif gitea_ssh_port:
    ports:
      - "${gitea_ssh_port}:2222"
% endif

    restart: unless-stopped

% endif
volumes:
  postgres_data:
  lancedb_data:
% if include_gitea:
  gitea_data:
  gitea_config:
% endif

secrets:

#------------------------------------------------------------------------------
#  Secrets in '.secrets/*' files
#------------------------------------------------------------------------------
#
#  Generate those with '.gen' suffixes using 'scripts/generate_secrets.py'
#------------------------------------------------------------------------------
    agui_db_password:
      file: ./.secrets/agui_db_password.gen
    authz_db_password:
      file: ./.secrets/authz_db_password.gen
% if include_gitea:
    gitea_db_password:
      file: ./.secrets/gitea_db_password.gen
% endif
    ingester_db_password:
      file: ./.secrets/ingester_db_password.gen
    ingester_bearer:
      file: ./.secrets/ingester_bearer.gen
    postgres_password:
      file: ./.secrets/postgres_password.gen
    url_safe_token_secret:
      file: ./.secrets/url_safe_token_secret.gen
#
#------------------------------------------------------------------------------
#  Secrets in '.env' file / environment
#------------------------------------------------------------------------------
#  See above for using file-based secrets instead.
#------------------------------------------------------------------------------
#   agui_db_password:
#     environment: "SOLIPLEX_AGUI_DB_PASSWORD"
#   authz_db_password:
#     environment: "SOLIPLEX_AUTHZ_DB_PASSWORD"
#   postgres_password:
#     environment: "SOLIPLEX_POSTGRES_PASSWORD"
#   url_safe_token_secret:
#     environment: "SOLIPLEX_URL_SAFE_TOKEN_SECRET"
