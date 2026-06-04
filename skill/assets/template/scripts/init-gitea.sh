#!/usr/bin/env bash
# init-gitea.sh
# Provision the local Gitea service: create an admin user, mint a scoped
# access token, create a tracking repository, and write GITEA_HOST /
# GITEA_ACCESS_TOKEN into '.env' for downstream consumers.
#
# Run AFTER 'docker compose up -d' (postgres + gitea healthy). Idempotent:
# re-running resets the admin password and reuses the existing repo.

set -eu

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
cd "$DOCKER_DIR"

# Host-published Gitea port (provisioning). The backend reaches Gitea over
# the compose network at http://gitea:3000, which is what lands in '.env'.
GITEA_HTTP="http://localhost:3000"
GITEA_INTERNAL_URL="http://gitea:3000"

ADMIN_USER="soliplex-admin"
ADMIN_EMAIL="admin@soliplex.localhost"
REPO_NAME="soliplex-requests"

# Random admin password (never persisted; only the token is written out).
ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9')Aa1!"

echo -e "${CYAN}=== Gitea provisioning ===${NC}"

echo -e "${CYAN}Waiting for Gitea at ${GITEA_HTTP} ...${NC}"
ready=0
for _ in $(seq 1 60); do
    if curl -fsS "${GITEA_HTTP}/api/v1/version" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    echo -e "${RED}ERROR: Gitea did not become ready at ${GITEA_HTTP}${NC}"
    echo -e "${YELLOW}Is the stack up? 'docker compose up -d gitea'${NC}"
    exit 1
fi

echo -e "${CYAN}Ensuring admin user '${ADMIN_USER}' ...${NC}"
if ! docker compose exec -T -u git gitea \
        gitea admin user create \
        --admin \
        --username "$ADMIN_USER" \
        --email "$ADMIN_EMAIL" \
        --password "$ADMIN_PASSWORD" \
        --must-change-password=false >/dev/null 2>&1; then
    echo -e "${YELLOW}  user exists; resetting password${NC}"
    docker compose exec -T -u git gitea \
        gitea admin user change-password \
        --username "$ADMIN_USER" \
        --password "$ADMIN_PASSWORD" \
        --must-change-password=false >/dev/null
fi

echo -e "${CYAN}Minting access token ...${NC}"
token_name="concierge-$(date +%s)"
token_resp="$(curl -fsS -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -X POST "${GITEA_HTTP}/api/v1/users/${ADMIN_USER}/tokens" \
    -d "{\"name\":\"${token_name}\",\"scopes\":[\"write:repository\",\"write:issue\"]}")"
token="$(printf '%s' "$token_resp" \
    | grep -o '"sha1":"[0-9a-f]*"' | head -1 | sed 's/.*:"//; s/"$//')"
if [ -z "$token" ]; then
    echo -e "${RED}ERROR: could not parse token from response:${NC}"
    echo "$token_resp"
    exit 1
fi

echo -e "${CYAN}Ensuring repository '${ADMIN_USER}/${REPO_NAME}' ...${NC}"
repo_code="$(curl -s -o /dev/null -w '%{http_code}' \
    -u "${ADMIN_USER}:${ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -X POST "${GITEA_HTTP}/api/v1/user/repos" \
    -d "{\"name\":\"${REPO_NAME}\",\"auto_init\":true,\"private\":false}")"
if [ "$repo_code" != "201" ] && [ "$repo_code" != "409" ]; then
    echo -e "${RED}ERROR: repo creation failed (HTTP ${repo_code})${NC}"
    exit 1
fi

set_env() {
    local key="$1" val="$2" file="${DOCKER_DIR}/.env"
    touch "$file"
    if grep -q "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}
set_env "GITEA_HOST" "$GITEA_INTERNAL_URL"
set_env "GITEA_ACCESS_TOKEN" "$token"

echo ""
echo -e "${GREEN}=== Gitea provisioned ===${NC}"
echo -e "  admin user : ${ADMIN_USER}"
echo -e "  repository : ${ADMIN_USER}/${REPO_NAME}"
echo -e "  GITEA_HOST / GITEA_ACCESS_TOKEN written to .env"
echo ""
echo -e "${YELLOW}Restart the backend so it picks up the new '.env':${NC}"
echo -e "  docker compose up -d backend"
echo ""
echo -e "${CYAN}View the repo at: https://localhost:9443/gitea/${ADMIN_USER}/${REPO_NAME}${NC}"
