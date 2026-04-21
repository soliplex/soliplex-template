# Soliplex Docker Compose Template

A starting point for running Soliplex and related services under Docker
Compose: the Soliplex backend, a Flutter web frontend served by nginx,
the haiku.rag document pipeline, docling-serve, Postgres, and Authelia
as the OpenID Connect provider.

## Prerequisites

- Docker + Docker Compose
- OpenSSL (for the secret and cert generation scripts)
- An Ollama server reachable from the stack (for the `gpt-oss:*` models
  referenced in `backend/environment/installation.yaml`)

## First-time setup

Run the two generator scripts, in either order, before the first
`docker compose up`:

```bash
./scripts/generate-secrets.sh       # populates .secrets/*.gen (gitignored)
./scripts/generate-nginx-cert.sh    # self-signed nginx cert + builds backend cacert.pem from cacert.pem.in
```

Both scripts are idempotent. Each one rebuilds a gitignored output file
from a version-controlled `*.in` template on every run:

- `generate-nginx-cert.sh` rebuilds `backend/environment/oidc/cacert.pem`
  from `cacert.pem.in` (the Mozilla CA bundle) and appends the freshly
  generated nginx public cert so the backend trusts the HTTPS path to
  Authelia.
- `generate-secrets.sh` also writes the `.secrets/*.gen` files and
  rebuilds two configs from their templates, injecting derived values
  in place of `REPLACE_ME` placeholders:
  - the OIDC JWKS **public key** PEM into
    `backend/environment/oidc/config.yaml` (from `config.yaml.in`) under
    `auth_systems[authelia].token_validation_pem`
  - the PBKDF2-SHA512 **digest** of the OIDC client secret into
    `authelia/configuration.yml` (from `configuration.yml.in`) under
    `identity_providers.oidc.clients[soliplex].client_secret`

No manual paste step is required. The plaintext OIDC client secret stays
in `.secrets/authelia_oidc_client_secret.gen` for the backend to mount.
Don't hand-edit the three gitignored outputs — edit the `.in` templates
and re-run the scripts.

Create a `.env` file defining `OLLAMA_BASE_URL`, pointing at the Ollama
server that will serve the models.

## Running the stack

```bash
docker compose up                # foreground
docker compose up -d             # detached
docker compose build <service>   # rebuild one service
docker compose logs -f backend
docker compose down              # stop (keeps postgres_data volume)
docker compose down -v           # stop AND wipe postgres volume
```

Access the UI at **`https://soliplex.localhost:9443/`** — not `localhost`
or `127.0.0.1`. The OIDC flow requires the same URL to be reachable from
both the browser (host side) and the backend container; `soliplex.localhost`
auto-resolves to 127.0.0.1 on the host (systemd-resolved / modern glibc
handle `*.localhost` per RFC 6761) and is routed to the host via
`extra_hosts` from inside the backend container. Authelia also accepts it
as a cookie domain (contains a period). You'll have to accept the
self-signed cert on first load.

If your host OS does not auto-resolve `*.localhost`, add this to
`/etc/hosts`:

```
127.0.0.1 soliplex.localhost
```

Default Authelia dev credentials: **`admin` / `authelia`**. Rotate the
argon2 hash in `authelia/users_database.yml` before any non-local use.

### Ports

| Port | Service |
|------|---------|
| 9000 | nginx HTTP (no TLS — for upstream-terminated deployments) |
| 9443 | nginx HTTPS (self-signed; **use this one**) |
| 8000 | backend direct |
| 8001 | haiku-rag MCP |
| 5001 | docling-serve |
| 5432 | Postgres |

### Re-running setup

- Rotating the nginx cert: re-run `./scripts/generate-nginx-cert.sh` then
  `docker compose restart nginx backend`. No rebuild needed.
- Rotating any secret: re-run `./scripts/generate-secrets.sh`. For secrets
  tied to the Postgres users created on first boot, you must also
  `docker compose down -v` to re-init the data volume, or the backend
  will fail to authenticate to the DB.

## Further reading

Architectural details (service graph, secret modes, Soliplex config
layout, sandbox, RAG pipeline, gotchas) live in [`CLAUDE.md`](./CLAUDE.md).
