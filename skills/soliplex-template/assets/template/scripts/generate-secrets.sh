#!/usr/bin/env bash
# generate-secrets.sh
# Generate random secrets for all .gen files referenced in docker-compose.yml

set -eu

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory and docker directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR="${DOCKER_DIR}/.secrets"
COMPOSE_FILE="${DOCKER_DIR}/docker-compose.yml"

echo -e "${CYAN}=== Docker Secrets Generator ===${NC}"
echo ""

# Check if docker-compose.yml exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}ERROR: Cannot find compose file at: $COMPOSE_FILE${NC}"
    exit 1
fi

# Create .secrets directory
echo -e "${GREEN}Creating secrets directory: ${SECRETS_DIR}${NC}"
mkdir -p "$SECRETS_DIR"

# Function to generate random password
generate_password() {
    local length="${1:-32}"
    # Use openssl for better compatibility across platforms
    openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "$length" || true
}

# Parse docker-compose.yml and generate secrets
echo -e "${CYAN}Scanning $COMPOSE_FILE for .gen secret files...${NC}"
echo ""

# Temporary file to store passwords for display
TEMP_PASSWORDS=$(mktemp)
trap "rm -f $TEMP_PASSWORDS" EXIT

secret_count=0

# Extract secret files and process them
grep -E '^\s+file:\s+' "$COMPOSE_FILE" | \
    awk '{print $2}' | \
    grep '\.gen$' | \
    sed 's/^.\///' | \
while IFS= read -r secret_file_path; do
    # Convert relative path to absolute
    secret_file_path="${secret_file_path#./}"

    secret_file="${DOCKER_DIR}/${secret_file_path}"
    secret_name=$(basename "$secret_file")

    # Generate password
    password=$(generate_password 32)

    # Write to file without newline
    echo -n "$password" > "$secret_file"
    # 0600: owner-only. Compose bind-mounts these as file-based secrets,
    # preserving the host owner/mode, and the in-container service users read
    # them at this mode -- which works because the images run as PUID:PGID
    # (see docker-compose.yml build.args / .env), and the block below ensures
    # the files are owned by PUID:PGID.
    chmod 600 "$secret_file" 2>/dev/null || true

    # Store for display later
    echo "${secret_name}|${password}" >> "$TEMP_PASSWORDS"

    echo -e "${GREEN}✓ Generated: ${secret_name}${NC}"
    secret_count=$((secret_count + 1))
done

# Read secret_count from temp file line count
secret_count=$(wc -l < "$TEMP_PASSWORDS" | tr -d ' ')

if [ "$secret_count" -eq 0 ]; then
    echo -e "${YELLOW}WARNING: No .gen secret files found in docker-compose.yml${NC}"
    echo -e "${YELLOW}Make sure the secrets section has entries like:${NC}"
    echo -e "${YELLOW}  secrets:${NC}"
    echo -e "${YELLOW}    my_secret:${NC}"
    echo -e "${YELLOW}      file: ./.secrets/my_secret.gen${NC}"
    exit 0
fi

# Align secret ownership with the UID/GID the containers run as.
#
# Compose bind-mounts these files preserving the host owner/mode, and the
# in-container service users read them at mode 0600 -- so the files must be
# owned by PUID:PGID (the build/runtime uid:gid, read from .env; default 1000).
# When the operator running this script already has that uid/gid -- the common
# single-developer case -- nothing more is needed. Otherwise (e.g. a deploy
# host whose login uid differs from the service account) re-own via a
# throwaway root container, since chowning to an arbitrary uid needs privilege
# the operator may lack. If docker is unavailable, warn and leave it to the
# operator.
ENV_FILE="${DOCKER_DIR}/.env"
PUID=1000
PGID=1000
if [ -f "$ENV_FILE" ]; then
    puid_val=$(grep -E '^PUID=' "$ENV_FILE" | tail -n1 | cut -d= -f2)
    pgid_val=$(grep -E '^PGID=' "$ENV_FILE" | tail -n1 | cut -d= -f2)
    [ -n "$puid_val" ] && PUID="$puid_val"
    [ -n "$pgid_val" ] && PGID="$pgid_val"
fi
cur_uid=$(id -u)
cur_gid=$(id -g)
if [ "${PUID}:${PGID}" != "${cur_uid}:${cur_gid}" ]; then
    if command -v docker >/dev/null 2>&1; then
        echo -e "${CYAN}Re-owning secrets to ${PUID}:${PGID} (container uid/gid)...${NC}"
        docker run --rm -u 0:0 -v "${SECRETS_DIR}:/secrets" busybox \
            chown -R "${PUID}:${PGID}" /secrets
    else
        echo -e "${YELLOW}WARNING: secrets are owned by ${cur_uid}:${cur_gid}, but services run as ${PUID}:${PGID}.${NC}"
        echo -e "${YELLOW}Install docker, or chown ${SECRETS_DIR}/*.gen to ${PUID}:${PGID} manually, before 'up'.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}=== Successfully Generated $secret_count Secret(s) ===${NC}"
echo ""
echo -e "${CYAN}Secret files created in: ${SECRETS_DIR}${NC}"
echo ""

# Display generated passwords
echo -e "${CYAN}=== Generated Passwords ===${NC}"
echo ""
while IFS='|' read -r secret_name password; do
    # Remove .gen suffix for display
    display_name="${secret_name%.gen}"
    echo -e "${YELLOW}${display_name}:${NC}"
    echo -e "  ${password}"
    echo ""
done < "$TEMP_PASSWORDS"

echo -e "${RED}IMPORTANT: Save these passwords securely!${NC}"
echo -e "${YELLOW}They are stored in the secret files but will not be displayed again.${NC}"
echo ""

# Provide next steps
echo -e "${CYAN}=== Next Steps ===${NC}"
echo ""
echo "1. Build services that need secrets (if applicable):"
echo "   cd \"${DOCKER_DIR}\""
echo "   docker compose build postgres"
echo ""
echo "2. Start services with secrets:"
echo "   docker compose up -d"
echo ""
echo "3. Verify secrets are working:"
echo "   docker compose exec postgres env | grep PASSWORD_FILE"
echo ""
