# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

RUN \
  apt-get update && \
  apt-get install -y \
    ca-certificates \
    curl \
    gpg \
    apt-transport-https \
    git \
    jq && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --link constraints.txt constraints.txt

RUN \
    uv init --bare --name "soliplex-template" --no-description && \
    uv add \
      --constraints constraints.txt \
      soliplex \
      psycopg[binary] \
      asyncpg \
      textual \
      textual-fspicker \
      textual-serve && \
    uv sync

# Bootstrap sandbox environments
COPY --link sandbox/environments /sandbox/environments

RUN --mount=type=cache,target=/root/.cache/uv \
    for env_dir in /sandbox/environments/*/; do \
      if [ -f "$env_dir/pyproject.toml" ]; then \
        uv --directory "$env_dir" sync --frozen; \
      fi; \
    done

# UID/GID the service runs as. Passed from docker-compose.yml (build.args),
# which sources PUID/PGID from .env; defaults match the historical 1000:1000.
# Aligning the image user with the host operator lets host-owned bind mounts
# and 0600 Docker secrets be read/written without a uid mismatch.
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} soliplex && useradd -m -u ${PUID} -g ${PGID} soliplex

USER soliplex

ENV PATH="/app/.venv/bin:$PATH"

CMD ["/app/.venv/bin/soliplex-cli", "serve", "--host=0.0.0.0", "--proxy-headers", "--forwarded-allow-ips=*", "/environment"]
