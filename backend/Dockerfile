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

RUN groupadd -g 1000 soliplex && useradd -m  -u 1000 -g 1000 soliplex
RUN mkdir /app/environment

USER soliplex

ENV PATH="/app/.venv/bin:$PATH"

CMD ["/app/.venv/bin/soliplex-cli", "serve", "--host=0.0.0.0", "--proxy-headers", "--forwarded-allow-ips=*", "/environment"]
