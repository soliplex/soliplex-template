<%text># syntax=docker/dockerfile:1
#
# Built on the soliplex-tui release image: soliplex plus the TUI's
# dependencies (textual, textual-serve, textual-fspicker) in '/app/.venv'. It
# talks to the backend over HTTP, so it needs no PostgreSQL driver. It shares
# its tag with the backend's soliplex-postgres image, so the two always run
# the same soliplex release.
FROM ghcr.io/soliplex/soliplex-tui:</%text>${soliplex_image_tag}<%text>

WORKDIR /app

# UID/GID the service runs as (build.args from docker-compose.yml, sourced from
# PUID/PGID in .env; defaults to the historical 1000:1000). The soliplex-tui
# image deliberately creates no user of its own.
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} soliplex && useradd -m -u ${PUID} -g ${PGID} soliplex

USER soliplex

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8002

CMD ["/app/.venv/bin/soliplex-tui-serve"]</%text>
