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

# Generate the OIDC JWKS keypair used for signing ID tokens. Unlike
# other secrets, the private key is NOT mounted into Authelia as a
# Docker secret (Authelia doesn't support a '_FILE' env var for list
# entries like jwks[]). Instead it is injected directly into
# authelia/configuration.yml below. The public-key half is injected
# into backend/environment/oidc/config.yaml so the backend can verify
# tokens Authelia signs.
jwks_key_file="${SECRETS_DIR}/authelia_oidc_jwks_key.gen"
jwks_pubkey_file="${SECRETS_DIR}/authelia_oidc_jwks_pubkey.gen"
echo -e "${CYAN}Generating OIDC JWKS RSA-4096 keypair...${NC}"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 \
    -out "$jwks_key_file" 2>/dev/null
chmod 600 "$jwks_key_file"
openssl rsa -in "$jwks_key_file" -pubout -out "$jwks_pubkey_file" 2>/dev/null
chmod 644 "$jwks_pubkey_file"
echo -e "${GREEN}✓ Wrote: $(basename "$jwks_key_file"), $(basename "$jwks_pubkey_file")${NC}"

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

    # Branch on secret name: most are random passwords; the OIDC HMAC
    # secret gets a longer password per Authelia guidance.
    case "$secret_name" in
        authelia_oidc_hmac_secret.gen)
            # Authelia recommends >= 64 chars for the OIDC HMAC secret.
            password=$(generate_password 64)
            echo -n "$password" > "$secret_file"
            ;;
        *)
            password=$(generate_password 32)
            echo -n "$password" > "$secret_file"
            ;;
    esac
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

# If the JWKS public key was derived, rebuild
# backend/environment/oidc/config.yaml (gitignored) from its template
# (config.yaml.in), injecting the PEM under
# auth_systems[authelia].token_validation_pem and replacing whatever
# PEM block (placeholder or previously injected key) sits between the
# BEGIN/END PUBLIC KEY markers.
pubkey_file="${SECRETS_DIR}/authelia_oidc_jwks_pubkey.gen"
oidc_config_file_in="${DOCKER_DIR}/backend/environment/oidc/config.yaml.in"
oidc_config_file="${DOCKER_DIR}/backend/environment/oidc/config.yaml"
if [ -f "$pubkey_file" ] && [ -f "$oidc_config_file_in" ]; then
    tmp_config=$(mktemp)
    awk -v pubkey_file="$pubkey_file" -v indent="        " '
    BEGIN {
        while ((getline line < pubkey_file) > 0) {
            pem = pem (pem ? "\n" : "") indent line
        }
        close(pubkey_file)
    }
    /^[[:space:]]*-----BEGIN PUBLIC KEY-----/ {
        print pem
        in_block = 1
        next
    }
    in_block && /^[[:space:]]*-----END PUBLIC KEY-----/ {
        in_block = 0
        next
    }
    !in_block { print }
    ' "$oidc_config_file_in" > "$tmp_config"
    mv "$tmp_config" "$oidc_config_file"
    echo -e "${GREEN}✓ Wrote OIDC config with injected JWKS public key:${NC}"
    echo -e "  ${oidc_config_file#${DOCKER_DIR}/}"
    echo ""
elif [ -f "$pubkey_file" ]; then
    echo -e "${YELLOW}WARNING: ${oidc_config_file_in} not found — config not built.${NC}"
    echo -e "${YELLOW}Paste the contents of ${pubkey_file} into${NC}"
    echo -e "${YELLOW}auth_systems[authelia].token_validation_pem manually.${NC}"
    echo ""
fi

# Rebuild authelia/configuration.yml (gitignored) from its template
# (configuration.yml.in), injecting:
#   - the RSA-4096 JWKS private key between the BEGIN/END PRIVATE KEY
#     markers under identity_providers.oidc.jwks
#   - the PBKDF2-SHA512 digest of the OIDC client secret under
#     identity_providers.oidc.clients[soliplex].client_secret
# The digest is computed via the Authelia CLI. The backend needs the
# plaintext of the client secret (already written to
# .secrets/authelia_oidc_client_secret.gen); Authelia's YAML needs the
# digest, which must live inline since it isn't mounted as a Docker
# secret.
client_secret_file="${SECRETS_DIR}/authelia_oidc_client_secret.gen"
authelia_config_file_in="${DOCKER_DIR}/authelia/configuration.yml.in"
authelia_config_file="${DOCKER_DIR}/authelia/configuration.yml"
if [ -f "$client_secret_file" ]; then
    echo -e "${CYAN}=== OIDC Client Secret Digest ===${NC}"
    echo ""
    if command -v docker >/dev/null 2>&1; then
        client_secret_plain=$(cat "$client_secret_file")
        digest_output=$(docker run --rm docker.io/authelia/authelia:latest \
            authelia crypto hash generate pbkdf2 --variant sha512 \
            --password "$client_secret_plain" 2>&1 || true)
        digest=$(echo "$digest_output" | awk -F': ' '/Digest/ {print $2}')
        if [ -n "$digest" ] && [ -f "$authelia_config_file_in" ] \
                && [ -f "$jwks_key_file" ]; then
            tmp_config=$(mktemp)
            # Single awk pass:
            #  * replace the PEM block between BEGIN/END PRIVATE KEY
            #    markers with the freshly generated JWKS key
            #  * replace the client_secret line whose value begins
            #    with $pbkdf2-sha512$ (placeholder or previously
            #    injected digest), preserving leading whitespace. Char
            #    39 is '.
            awk -v digest="$digest" \
                -v key_file="$jwks_key_file" \
                -v pem_indent="          " '
            BEGIN {
                while ((getline line < key_file) > 0) {
                    pem = pem (pem ? "\n" : "") pem_indent line
                }
                close(key_file)
            }
            !key_done && /^[[:space:]]*-----BEGIN PRIVATE KEY-----/ {
                print pem
                in_pem = 1
                key_done = 1
                next
            }
            in_pem && /^[[:space:]]*-----END PRIVATE KEY-----/ {
                in_pem = 0
                next
            }
            in_pem { next }
            !digest_done && /^[[:space:]]*client_secret:[[:space:]]*.\$pbkdf2-sha512\$/ {
                match($0, /^[[:space:]]*/)
                indent = substr($0, RSTART, RLENGTH)
                printf "%sclient_secret: %c%s%c\n", indent, 39, digest, 39
                digest_done = 1
                next
            }
            { print }
            ' "$authelia_config_file_in" > "$tmp_config"
            mv "$tmp_config" "$authelia_config_file"
            echo -e "${GREEN}✓ Wrote Authelia config with injected JWKS key + client secret digest:${NC}"
            echo -e "  ${authelia_config_file#${DOCKER_DIR}/}"
            echo ""
        elif [ -n "$digest" ]; then
            echo -e "${YELLOW}WARNING: ${authelia_config_file_in} or ${jwks_key_file} missing — config not built.${NC}"
            echo -e "${YELLOW}Paste this digest into identity_providers.oidc.clients[soliplex].client_secret manually:${NC}"
            echo ""
            echo "  $digest"
            echo ""
        else
            echo -e "${RED}Failed to compute PBKDF2 digest via Authelia CLI.${NC}"
            echo -e "${YELLOW}Run manually:${NC}"
            echo "  docker run --rm docker.io/authelia/authelia:latest \\"
            echo "    authelia crypto hash generate pbkdf2 --variant sha512 \\"
            echo "    --password \"\$(cat ${client_secret_file})\""
            echo ""
        fi
    else
        echo -e "${YELLOW}Docker not available — hash the client secret manually:${NC}"
        echo "  docker run --rm docker.io/authelia/authelia:latest \\"
        echo "    authelia crypto hash generate pbkdf2 --variant sha512 \\"
        echo "    --password \"\$(cat ${client_secret_file})\""
        echo ""
    fi
fi

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

# Optional: Update .env file reminder for ingester
if grep -q "ingestion_db_password.gen" "$COMPOSE_FILE" 2>/dev/null; then
    echo -e "${YELLOW}NOTE: The ingester service may still need DOC_DB_PASS in .env:${NC}"
    echo -e "${YELLOW}Add to .env file:${NC}"
    ingestion_db_pass_file="${SECRETS_DIR}/ingestion_db_password.gen"
    echo -e "${YELLOW}  DOC_DB_PASS=\$(cat \"${ingestion_db_pass_file}\")${NC}"
    echo ""
fi
