# syntax=docker/dockerfile:1
#
# Built on the soliplex release image with PostgreSQL support: '/app/.venv'
# holds soliplex and its 'postgres' extra (pure-Python 'psycopg' over the
# image's system 'libpq5'). Its tag picks the release: a moving
# '<major>.<minor>' tag follows patch releases on
# 'docker compose build --pull backend'; an exact '<major>.<minor>.<patch>'
# pins one. Only what is particular to this stack is layered on below.
#
# The release image carries neither 'uv' nor 'bwrap': 'uv' is mounted for the
# build steps that need it (never copied into the image, as upstream does),
# and 'bwrap' is installed for the 'bubble-sandbox' skill.
FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM ghcr.io/soliplex/soliplex-postgres:${soliplex_image_tag}

WORKDIR /app

# Enable bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 'bwrap' for the 'bubble-sandbox' skill. Running code also needs the
# backend's 'security_opt' in docker-compose.yml (generator: enable_sandbox).
RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends \
    bubblewrap && \
    rm -rf /var/lib/apt/lists/*

# Extra PyPI dependencies for this stack, one requirement per line (see
# requirements.txt). The soliplex image's own versions are frozen as
# constraints, so an extra can add packages but never silently move one the
# release pinned; an extra that needs a different version fails the build
# instead. Skipped while the file holds only comments and blank lines.
COPY --link requirements.txt /tmp/requirements.txt

RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    if grep -qv '^[[:space:]]*\(#\|$\)' /tmp/requirements.txt; then \
      uv pip freeze --python /app/.venv/bin/python \
        > /tmp/base-constraints.txt && \
      uv pip install --python /app/.venv/bin/python \
        --constraints /tmp/base-constraints.txt \
        --requirements /tmp/requirements.txt; \
    fi

# Bootstrap sandbox environments
COPY --link sandbox/environments /sandbox/environments

RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    for env_dir in /sandbox/environments/*/; do \
      if [ -f "$env_dir/pyproject.toml" ]; then \
        uv --directory "$env_dir" sync --frozen; \
      fi; \
    done

# UID/GID the service runs as. Passed from docker-compose.yml (build.args),
# which sources PUID/PGID from .env; defaults match the historical 1000:1000.
# Aligning the image user with the host operator lets host-owned bind mounts
# and 0600 Docker secrets be read/written without a uid mismatch. (The
# soliplex image deliberately creates no user of its own.)
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} soliplex && useradd -m -u ${PUID} -g ${PGID} soliplex

USER soliplex

ENV PATH="/app/.venv/bin:$PATH"

CMD ["/app/.venv/bin/soliplex-cli", "serve", "--host=0.0.0.0", "--proxy-headers", "--forwarded-allow-ips=*", "/environment"]
