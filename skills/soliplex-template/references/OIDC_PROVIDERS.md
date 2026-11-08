# OIDC providers (external)

Loads when: Load this reference when the user wants to add, retitle, or remove an external OIDC provider, or choose which provider signs users in.

## Adding or removing an OIDC provider

A stack's OIDC providers are the `auth_systems` entries in
`backend/environment/oidc/config.yaml`. With none, the backend runs without
authentication and treats every request as Soliplex's no-auth user
(`phreddy@example.com`); the first provider turns authentication on. Run
`scripts/oidc_providers.py` from the stack directory (or pass
`--project-dir`); it never prompts, and its PEP 723 header pulls in
`pyyaml` under `uv run`:

```bash
uv run scripts/oidc_providers.py list
uv run scripts/oidc_providers.py add --id <id> --title "<button label>" \
    --server-url <issuer URL, no trailing slash> --client-id <client id> \
    --pem-file <the provider's token-signing public key, PEM> \
    [--scope "openid email profile"] [--signin]
uv run scripts/oidc_providers.py remove <id> [--signin-successor <id>]
uv run scripts/oidc_providers.py signin <id>
```

`add` writes a public client (no client secret). The id must be lowercase
letters, digits, `_` and `-`: it appears in the login and callback URLs.
Discovery, client secrets and key refresh are not supported yet; ask the
user for the issuer URL, client id and signing key.

The script also keeps nginx's `/ingester/` sign-in in step: the first
provider becomes the one it signs in with, a later one only with `--signin`,
and removing that provider while others remain needs `--signin-successor`.
Relay what it prints, and act on it:

- **The first provider was added** (authentication is now on): no account is
  an admin yet. Ask for the user's email and give it admin rights:
  `docker compose run --rm --quiet-build soliplex-dev soliplex-cli admin-users add /environment <email>`.
- **The last provider was removed** (authentication is now off): admin-only
  routes admit only the no-auth user, once it is an admin. Offer to re-run
  `docker compose run --rm --quiet-build stack-init`, which makes it one if it
  is not already; don't make the user type the `admin-users` command.
- **The sign-in provider changed**: `docker compose restart nginx`. nginx
  reads `nginx/conf.d/signin-provider.conf` only at start.

The backend picks up every change by itself (`--reload=config`): no restart.
