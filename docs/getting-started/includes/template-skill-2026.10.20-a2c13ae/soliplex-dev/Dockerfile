# syntax=docker/dockerfile:1
#
# The dev-mode 'soliplex-dev' service: the backend's own image (compose passes
# it as the 'backend' build context, 'service:backend'), plus the 'dev'
# dependency group of every project under src/ -- pytest and the like -- so a
# project's tests run against the very versions the backend imports it with.
#
# Those are listed in requirements-dev.txt, which src_projects.py generates
# from the projects' pyproject.toml files. They are installed with the backend
# image's own versions frozen as constraints, as backend/requirements.txt is: a
# dev requirement that needs a different version fails this build. Skipped
# while the file holds only comments and blank lines.
#
# The 'uv' tag is the one backend/Dockerfile mounts; keep the two in step.
FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM backend

# The backend image runs as the 'soliplex' user; installing needs root.
USER root

COPY --link requirements-dev.txt /tmp/requirements-dev.txt

RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    if grep -qv '^[[:space:]]*\(#\|$\)' /tmp/requirements-dev.txt; then \
      uv pip freeze --python /app/.venv/bin/python \
        > /tmp/base-constraints.txt && \
      uv pip install --python /app/.venv/bin/python \
        --constraints /tmp/base-constraints.txt \
        --requirements /tmp/requirements-dev.txt; \
    fi

USER soliplex
