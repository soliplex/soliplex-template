# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN \
  apt-get update && \
  apt-get install -y \
    ca-certificates \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --link constraints.txt constraints.txt

RUN \
    uv init --bare --name "soliplex-template-tui" --no-description && \
    uv add \
      --constraints constraints.txt \
      soliplex \
      textual \
      textual-fspicker \
      textual-serve \
      typer && \
    uv sync

RUN groupadd -g 1000 soliplex && useradd -m -u 1000 -g 1000 soliplex

USER soliplex

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8002

CMD ["/app/.venv/bin/soliplex-tui-serve"]
