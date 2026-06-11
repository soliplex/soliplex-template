FROM postgres:18.1-trixie
USER root
# UID/GID the postgres user is remapped to (build.args from docker-compose.yml,
# sourced from PUID/PGID in .env; defaults to the historical 1000:1000). This
# lets the server read its 0600 password secret, which Compose bind-mounts
# owned by the host operator running generate_secrets.py.
ARG PUID=1000
ARG PGID=1000
RUN groupmod -g ${PGID} postgres && usermod -u ${PUID} -g ${PGID} postgres && chown -R postgres:postgres /run/postgresql
USER postgres
