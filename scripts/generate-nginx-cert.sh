#!/usr/bin/env bash
# generate-nginx-cert.sh
#
# Generate the nginx self-signed server cert on the host, store it in
# .secrets/nginx-server.{crt,key}.gen (bind-mounted into the nginx
# container), and append the public cert to the backend's CA bundle
# (backend/environment/oidc/cacert.pem) so soliplex trusts nginx when
# it hits Authelia's OIDC endpoints.
#
# Idempotent: any previously-appended block (delimited by the BEGIN/END
# markers below) is stripped before the current cert is appended.
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
CACERT="${DOCKER_DIR}/backend/environment/oidc/cacert.pem"
BEGIN_MARKER="# >>> soliplex-template nginx self-signed cert >>>"
END_MARKER="# <<< soliplex-template nginx self-signed cert <<<"

if ! command -v openssl >/dev/null 2>&1; then
    echo -e "${RED}ERROR: openssl not found on PATH${NC}" >&2
    exit 1
fi

if [ ! -f "$CACERT" ]; then
    echo -e "${RED}ERROR: ${CACERT} not found${NC}" >&2
    exit 1
fi

mkdir -p "$SECRETS_DIR"

echo -e "${CYAN}Generating nginx self-signed cert (RSA-2048, 365 days)...${NC}"
# SAN entries match the Authelia requirements: 'localhost' for
# convenience, IP:127.0.0.1 because Authelia rejects a bare 'localhost'
# as a session cookie domain (needs a period or an IP).
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    2>/dev/null
chmod 600 "$KEY"
chmod 644 "$CERT"

subject=$(openssl x509 -in "$CERT" -noout -subject)
notAfter=$(openssl x509 -in "$CERT" -noout -enddate)
echo -e "${GREEN}  ${subject}${NC}"
echo -e "${GREEN}  ${notAfter}${NC}"

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo -e "${CYAN}Stripping any previous appended block from ${CACERT}...${NC}"
awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
    $0 == b {skip=1; next}
    $0 == e {skip=0; next}
    !skip   {print}
' "$CACERT" > "${TMPDIR}/cacert.pem"

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
echo -e "${GREEN}Updated ${CACERT}${NC}"
echo ""
echo -e "${YELLOW}Next: docker compose restart nginx backend${NC}"
