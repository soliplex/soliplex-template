# The built Soliplex web client, published by soliplex/frontend as an image
# holding only 'build/web', already cache-busted: everything but index.html,
# the manifest, favicon and icons sits under a 'v<release-tag>/' directory,
# with relative URLs, so the same tree serves under any '<base href>'. Its tag
# picks the release: a moving '<major>.<minor>' tag follows patch releases on
# 'docker compose build --pull nginx'; an exact '<major>.<minor>.<patch>' (or
# '<major>.<minor>.<patch>-<build>') pins one.
FROM ghcr.io/soliplex/frontend-web:0.110 AS frontend

FROM debian:trixie

# UID/GID the service runs as (build.args from docker-compose.yml, sourced from
# PUID/PGID in .env; defaults to the historical 1000:1000). Used below to own
# the build-time self-signed TLS material and create the runtime user.
ARG PUID=1000
ARG PGID=1000

# Install:
# - iputils-ping for network debugging
# - openssl for certificate generation,
# - ca-certificates
# - curl for healthcheck
# - headers-more module to suppress the Server header
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        openssl ca-certificates curl \
        libnginx-mod-http-headers-more-filter \
        libnginx-mod-http-ndk \
        libnginx-mod-http-lua && \
    rm -rf /var/lib/apt/lists/*

# Generate self-signed SSL certificate
RUN mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/server.key \
    -out /etc/nginx/ssl/server.crt \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost" && \
    chown -R ${PUID}:${PGID} /etc/nginx/ssl

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_DIR=/etc/ssl/certs
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# The built web client, served from here (see nginx.conf)
COPY --from=frontend /build/web /app/build/web

# nginx configuration (nginx.conf, mime.types, error-pages) is bind-mounted
# from the host at runtime (see docker-compose.yml), so edits don't require
# an image rebuild.

EXPOSE 9000 9443
RUN groupadd -g ${PGID} soliplex && useradd -m -u ${PUID} -g ${PGID} soliplex
USER soliplex
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:9000/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
