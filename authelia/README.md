Configuration for 'authelia' service

Authelia fronts the nginx proxy using the [AuthRequest / Forwarded Auth](https://www.authelia.com/integration/proxies/nginx/)
pattern: nginx issues a sub-request to Authelia before letting requests reach
`/api/` or `/mcp/` on the backend. On success, Authelia returns `Remote-User`,
`Remote-Groups`, `Remote-Name`, and `Remote-Email` headers, which nginx injects
into the upstream request.

## Portal URL

`https://127.0.0.1:9443/authelia/` — served on the same origin as the Flutter
app via the `server.address` path prefix in `configuration.yml`.

Access the whole stack via `https://127.0.0.1:9443/` (not `localhost`):
Authelia rejects `localhost` as a cookie domain (the config validator requires
a period or an IP), and the nginx cert carries a `IP:127.0.0.1` SAN to match.

## Default credentials (dev only)

- Username: `admin`
- Password: `authelia`

The shipped argon2id hash is a widely-cited dev value. If login fails on first
boot, generate a fresh hash and replace the `password:` field in
`users_database.yml`:

```bash
docker compose run --rm --no-deps authelia \
  authelia crypto hash generate argon2 --password <your-pw>
```

## Secrets

All sensitive values are mounted from Docker secrets and referenced via
`AUTHELIA_*_FILE` environment variables in `docker-compose.yml`:

- `authelia_jwt_secret` — password-reset JWT signer
- `authelia_session_secret` — session cookie signer
- `authelia_storage_encryption_key` — at-rest encryption for the storage backend
- `authelia_db_password` — Postgres password for the `soliplex_authelia` role

Populate them by running `scripts/generate-secrets.sh` from the repo root; the
script auto-discovers every `.gen` file referenced in `docker-compose.yml`.

## Storage

Postgres database `soliplex_authelia`, owned by the `soliplex_authelia` role.
Created on first-boot by `postgres/config/init.sh`; wiping the `postgres_data`
volume (`docker compose down -v`) resets user state.

## Files in this directory

- `Dockerfile` — pins the upstream `authelia/authelia` image
- `configuration.yml` — main config (bind-mounted to `/config`)
- `users_database.yml` — file-based auth backend, referenced from `configuration.yml`
