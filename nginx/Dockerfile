FROM ghcr.io/cirruslabs/flutter:3.38.4 AS builder

RUN \
  latest=$(curl -s -L https://api.github.com/repos/soliplex/frontend/releases/latest -H "Accept: application/json" | jq -r ".tarball_url") && \
  cd /tmp/ && \
  curl -L $latest -o soliplex-frontend-latest.tgz && \
  topdir=$(tar tf soliplex-frontend-latest.tgz | head -1) && \
  tar xf soliplex-frontend-latest.tgz && \
  mv $topdir /app && \
  wo_prefix=${topdir#soliplex-frontend-} && \
  release_hash=${wo_prefix%/} && \
  echo $release_hash > /tmp/soliplex-frontend-release-hash

# Copy post-build cache busting script (needed before flutter build completes)
COPY ./post-build-cache-bust.sh /tmp/post-build-cache-bust.sh

RUN chmod +x /tmp/post-build-cache-bust.sh

WORKDIR /app

RUN \
    flutter --disable-analytics && \
    flutter clean && \
    flutter pub get && \
    flutter build web --release --no-tree-shake-icons --no-web-resources-cdn --pwa-strategy=none && \
    RELEASE_HASH=$(cat /tmp/soliplex-frontend-release-hash) \
    /tmp/post-build-cache-bust.sh

FROM debian:trixie

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

# Generate self-signed SSL certificate.
# SAN entries: 'localhost' for convenience, 'IP:127.0.0.1' because
# Authelia rejects a bare 'localhost' as a session cookie domain
# (must contain a period or be an IP), so the stack is accessed via
# https://127.0.0.1:9443/ and the cert needs a matching SAN.
RUN mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/server.key \
    -out /etc/nginx/ssl/server.crt \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" && \
    chown -R 1000:1000 /etc/nginx/ssl

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_DIR=/etc/ssl/certs
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Copy built flutter web app to nginx html directory
COPY --from=builder /app/build/web /app/build/web

# nginx configuration (nginx.conf, mime.types, error-pages) is bind-mounted
# from the host at runtime (see docker-compose.yml), so edits don't require
# an image rebuild.

EXPOSE 9000 9443
RUN groupadd -g 1000 soliplex && useradd -m  -u 1000 -g 1000 soliplex
USER soliplex
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:9000/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
