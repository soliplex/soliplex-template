# Usage:
# docker compose up
name: ${project_name}

services:

  nginx:

    build:
      context: nginx
      dockerfile: Dockerfile

    depends_on:
      - backend
      - tui

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

  tui:

    build:
      context: tui
      dockerfile: Dockerfile

    depends_on:
      - backend

    user: "1000:1000"

    # No host port mapping: the TUI is reached only through nginx so that the
    # '--public-url' baked into the served HTML (used for static assets and
    # the websocket) stays consistent with what the browser sees.
    command: "/app/.venv/bin/soliplex-tui-serve --backend-url http://backend:8000 --host 0.0.0.0 --port 8002 --public-url https://${server_name}:${nginx_https}/tui"

  backend:

    build:
      context: backend
      dockerfile: Dockerfile

    env_file: ".env"
    user: "1000:1000"

    depends_on:
      - postgres

    healthcheck:

      test: ["CMD-SHELL", "curl -f http://localhost:8000/api/ok || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 4

    secrets:
      - agui_db_password
      - authz_db_password
      - url_safe_token_secret

    environment:
      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>

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

    ports:
      - "8000:8000"

    # Temporary: run with '--no-auth-mode'
    command: "/app/.venv/bin/soliplex-cli serve ${backend_auth_flag}--reload=config --host=0.0.0.0 --proxy-headers --forwarded-allow-ips=* /environment"

  haiku-ingester:

    image: ghcr.io/ggozad/haiku.rag-slim:0.51.0

    build:
      context: haiku.rag
      dockerfile: Dockerfile

    # Render the bind-mounted yaml into /tmp with INGESTER_TOKEN
    # substituted, then exec the ingester against the rendered copy.
    # haiku.rag's yaml loader has no env-var interpolation so we do
    # the substitution before haiku-ingester reads the config.
    command:
      - sh
      - -c
      - |
        sed "s|__INGESTER_TOKEN__|$$INGESTER_TOKEN|" /app/haiku.rag.yaml > /tmp/haiku.rag.yaml
        exec haiku-ingester --config /tmp/haiku.rag.yaml serve

    ports:
      - "${ingester_port}:8765"  # control plane: /health, /jobs, /sources, /dlq, /stats

    volumes:
      - ./rag/db:/data
      - ./${docs_dir}:/docs
      - ./haiku.rag/haiku.rag.yaml:/app/haiku.rag.yaml:ro

    environment:
      OLLAMA_BASE_URL: <%text>${OLLAMA_BASE_URL}</%text>
      # Bearer token clients must send to /jobs, /sources, /dlq, etc.
      # Override in '.env'; the default 'secret' is intentionally weak
      # so unhardened deployments fail an obvious security review.
      INGESTER_TOKEN: <%text>${INGESTER_TOKEN:-secret}</%text>
      # API keys (set as needed)
      #- OPENAI_API_KEY=<%text>${OPENAI_API_KEY}</%text>
      #- ANTHROPIC_API_KEY=<%text>${ANTHROPIC_API_KEY}</%text>
      #- VOYAGE_API_KEY=<%text>${VOYAGE_API_KEY}</%text>
      #- CO_API_KEY=<%text>${CO_API_KEY}</%text>

    user: "1000:1000"

    depends_on:
      docling-serve:
        condition: service_healthy

    # shutdown_grace_s defaults to 60s — give Docker enough to drain
    # in-flight jobs before SIGKILL.
    stop_grace_period: 180s

    healthcheck:
      # slim image has no curl; use stdlib instead.
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

  docling-serve:

    #image: ghcr.io/docling-project/docling-serve:v1.16.1
    # Use CPU version for machines w/o compatible/ capable GPU
    image: ghcr.io/docling-project/docling-serve-cpu:v1.16.1

    build:
      context: docling-serve
      dockerfile: Dockerfile

    environment:
      DOCLING_SERVE_ENABLE_UI: 1

    ports:
      - "${docling_port}:5001"

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

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

    environment:
      AUTO_CREATE_DATABASE: <%text>${AUTO_CREATE_DATABASE:-1}</%text>
      AGUI_DB_PASS_FILE: /run/secrets/agui_db_password
      AUTHZ_DB_PASS_FILE: /run/secrets/authz_db_password
      POSTGRES_INITDB_ARGS: "-A scram-sha-256"
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_USER: postgres

    volumes:
      - type: "bind"
        source: "./postgres/config"
        target: "/docker-entrypoint-initdb.d"
        bind:
          selinux: "z"  # mark as shared

      - type: "volume"
        source: "postgres_data"
        target: "/var/lib/postgresql"

    tmpfs:
      - /var/run/postgresql:uid=999,gid=999
      - /tmp
    secrets:
      - source: agui_db_password
      - source: authz_db_password
      - source: postgres_password
    ports:
      - "${postgres_port}:5432"

volumes:
  postgres_data:
  lancedb_data:

secrets:

#------------------------------------------------------------------------------
#  Secrets in '.secrets/*' files
#------------------------------------------------------------------------------
#
#  Generate those with '.gen' suffixes using 'scripts/generate-secrets.sh'
#------------------------------------------------------------------------------
    agui_db_password:
      file: ./.secrets/agui_db_password.gen
    authz_db_password:
      file: ./.secrets/authz_db_password.gen
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
