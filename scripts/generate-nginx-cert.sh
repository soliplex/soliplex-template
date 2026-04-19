#!/usr/bin/env bash
# generate-nginx-cert.sh
#
# Generate the nginx self-signed server cert on the host, store it in
# .secrets/nginx-server.{crt,key}.gen (bind-mounted into the nginx
# container), and write the backend's CA bundle
# (backend/environment/oidc/cacert.pem — gitignored) by copying the
# template bundle (cacert.pem.in) and appending the public cert so
# soliplex trusts nginx when it hits Authelia's OIDC endpoints.
#
# Idempotent: the output cacert.pem is rebuilt from cacert.pem.in on
# every run. Any block in the template delimited by the BEGIN/END
# markers below (e.g. the `REPLACE ME` placeholder) is stripped before
# the current cert is appended.
#
# Run:
#   - once before the first `docker compose up`
#   - whenever the cert expires (365 days) or needs rotation
# After re-running, `docker compose restart nginx backend`.

set -eu

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR="${DOCKER_DIR}/.secrets"
CERT="${SECRETS_DIR}/nginx-server.crt.gen"
KEY="${SECRETS_DIR}/nginx-server.key.gen"
CACERT_IN="${DOCKER_DIR}/backend/environment/oidc/cacert.pem.in"
CACERT="${DOCKER_DIR}/backend/environment/oidc/cacert.pem"
BEGIN_MARKER="# >>> soliplex-template nginx self-signed cert >>>"
END_MARKER="# <<< soliplex-template nginx self-signed cert <<<"

if ! command -v openssl >/dev/null 2>&1; then
    echo -e "${RED}ERROR: openssl not found on PATH${NC}" >&2
    exit 1
fi

if [ ! -f "$CACERT_IN" ]; then
    echo -e "${RED}ERROR: ${CACERT_IN} not found${NC}" >&2
    exit 1
fi

mkdir -p "$SECRETS_DIR"

# If docker compose ran before this script, it bind-mounted the missing
# source paths and Docker auto-created each as an empty root-owned
# directory. openssl would later fail with a confusing "Expecting:
# TRUSTED CERTIFICATE" error on nginx startup. Detect and bail early.
for stub in "$CERT" "$KEY"; do
    if [ -d "$stub" ]; then
        echo -e "${RED}ERROR: ${stub} is a directory (likely created by 'docker compose up' before this script ran).${NC}" >&2
        echo -e "${RED}Remove it first (may need sudo because Docker created it as root):${NC}" >&2
        echo -e "${RED}  sudo rmdir '${stub}'${NC}" >&2
        exit 1
    fi
done

echo -e "${CYAN}Generating nginx self-signed cert (RSA-2048, 365 days)...${NC}"
# Primary SAN is 'soliplex.localhost', the canonical external URL of
# the stack: it resolves to 127.0.0.1 on the host (systemd-resolved /
# glibc auto-map '*.localhost') and to host-gateway inside the backend
# container (via docker-compose extra_hosts), so the browser and the
# backend's server-side OIDC calls hit the same URL and Authelia's
# issuer claim stays consistent. 'localhost' and IP:127.0.0.1 are kept
# as fallback SANs for direct-access tooling / debugging.
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=soliplex.localhost" \
    -addext "subjectAltName=DNS:soliplex.localhost,DNS:localhost,IP:127.0.0.1" \
    2>/dev/null
chmod 600 "$KEY"
chmod 644 "$CERT"

subject=$(openssl x509 -in "$CERT" -noout -subject)
notAfter=$(openssl x509 -in "$CERT" -noout -enddate)
echo -e "${GREEN}  ${subject}${NC}"
echo -e "${GREEN}  ${notAfter}${NC}"

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo -e "${CYAN}Rebuilding ${CACERT} from ${CACERT_IN} (stripping any marker block)...${NC}"
awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
    $0 == b {skip=1; next}
    $0 == e {skip=0; next}
    !skip   {print}
' "$CACERT_IN" > "${TMPDIR}/cacert.pem"

echo -e "${CYAN}Appending current cert with marker block...${NC}"
{
    cat "${TMPDIR}/cacert.pem"
    echo "$BEGIN_MARKER"
    echo "# Regenerate via: scripts/generate-nginx-cert.sh"
    echo "# Valid until: ${notAfter#notAfter=}"
    cat "$CERT"
    echo "$END_MARKER"
} > "${TMPDIR}/cacert.new"

mv "${TMPDIR}/cacert.new" "$CACERT"
echo -e "${GREEN}Wrote ${CACERT}${NC}"
echo ""
echo -e "${YELLOW}Next: docker compose restart nginx backend${NC}"
