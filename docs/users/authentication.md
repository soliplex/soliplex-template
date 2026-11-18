---
icon: lucide/user-round-cog
---

# Sign-in: choosing and running an identity provider

The stack starts with no OIDC provider, and so **without authentication**:
anyone who can reach nginx uses it as Soliplex's fixed no-auth user. That is
fine on a workstation. To require sign-in, add a provider — either one the
stack runs itself, or one operated elsewhere. Both are added *after* the stack
is generated, by scripts the `soliplex-template` skill bundles; the backend
picks up a change by itself (`--reload=config`), and the scripts keep nginx's
`/ingester/` sign-in in step.

| Choice | Provider runs | Fit |
|--------|---------------|-----|
| [No sign-in](#staying-without-sign-in) | — | workstation use; add one before exposing the stack |
| [In-stack Authelia](#in-stack-authelia) | inside the stack | one place to give accounts to; no IdP operator to deal with |
| [External provider](#external-provider) | Keycloak, Auth0, Entra ID, ... | users already live there; your org runs it |

## Staying without sign-in

With no `auth_systems` entry in `backend/environment/oidc/config.yaml`, the
backend treats every request as the fixed no-auth user, and `stack-init`
makes that user an admin (only while no provider is configured), so
admin-only routes such as `/ingester/` keep working. If you later add and
then remove all providers, re-run `stack-init` to hand the no-auth user its
admin rights back.

## In-stack Authelia

Authelia (pinned image) lives inside the stack, is reverse-proxied at
`/authelia/`, and stores its users and state in the stack's Postgres
(`soliplex_authelia` database). The stack owns both ends of the sign-in
exchange, so nothing has to be registered with an outside operator; the
backend already trusts the provider's own TLS certificate.

### Installing

From the stack directory, after `docker compose build`:

```bash
python3 <skill>/scripts/install_authelia.py \
    --project-dir <stack> --admin-email <you>@<domain>
```

The script is idempotent. It writes `compose.authelia.yaml` (pulled in by a
top-level `include:` in `docker-compose.yml`), `authelia/configuration.yml`,
and `nginx/conf.d/authelia.conf`; generates and — on later runs — preserves
the Authelia secrets and the OIDC issuer key in `.secrets/`; registers the
`soliplex` OIDC client (public, PKCE, RS256 access tokens); creates the
`soliplex_authelia` role and database; starts Authelia; and creates the first
admin user. Its `--no-up` flag writes the configuration and stops before
touching Docker, for stacks not yet up. Finish then with `stack-init`,
`docker compose up -d`, and
`authelia_users.py add --admin --email <you>@<domain>` — the script prints
the commands.

The admin password is generated randomly and written to
`.secrets/authelia_user_<email>.gen` (mode `0600`); hand its contents to the
user out of band and change it in the portal afterward.

### Managing users

```bash
python3 <skill>/scripts/authelia_users.py --project-dir <stack> \
    add --email <user>@<domain>            # [--admin] also a soliplex admin
python3 <skill>/scripts/authelia_users.py --project-dir <stack> list
python3 <skill>/scripts/authelia_users.py --project-dir <stack> \
    reset <user>@<domain>                  # new password file
python3 <skill>/scripts/authelia_users.py --project-dir <stack> \
    remove <user>@<domain>                 # also drops a soliplex admin entry
```

Users change their own passwords in the Authelia portal: they confirm their
identity with a one-time validation link, which the operator relays — with
the filesystem notifier it lands in `authelia/notification.txt`, and

```bash
python3 <skill>/scripts/authelia_users.py --project-dir <stack> code
```

prints the latest notification for relaying. Each notification overwrites the
whole file, so two requests raised at the same moment keep only the later
one; a stack with the SMTP notifier (a configuration change) mails
notifications out and no file is written.

An admin user's rights in soliplex follow their Authelia sign-in, so
removing an admin's user entry also removes their `admin_users` entry. For
the provisioner, add its own service subject instead — see
[Room provisioning](operations/room-provisioning/service.md).

### Key rotation

Authelia's OIDC signing key and its server certificate are generated once,
and re-running `install_authelia.py` keeps them. To replace either one,
delete its files and re-run the installer with `--no-up`.

!!! warning "Always pass `--no-up` when re-running the installer"
    Without it, the installer also re-creates the admin user, which gives
    that user a new password. `--admin-email` is still required, but with
    `--no-up` it is not used.

**The signing key.** The installer generates a new key, but it keeps an
existing `authelia` provider entry, along with the old public key in it.
Remove the entry first, so the installer writes it again with the new key.
Stop the backend while you do this, so that it never loads the config
without the entry:

```bash
docker compose stop backend
rm .secrets/authelia_oidc_issuer_private_key.gen \
   .secrets/authelia_oidc_issuer_certificate.pem
python3 <skill>/scripts/oidc_providers.py remove authelia
python3 <skill>/scripts/install_authelia.py --admin-email <admin email> --no-up
docker compose up -d --force-recreate authelia
docker compose start backend
```

- `--force-recreate` is needed because the key reaches Authelia as a
  Compose secret, and a container keeps the file it was created with: a
  plain `restart` would keep signing with the old key.
- **If Authelia is the `/ingester/` sign-in provider and others remain**,
  `remove` needs `--signin-successor <another id>`. The installer makes
  Authelia the sign-in provider again.
- **If Authelia was not the sign-in provider**, the installer makes it one.
  Set the previous one back with `oidc_providers.py signin <id>`.
- If the sign-in provider ends up different from where it started,
  `docker compose restart nginx`.
- **The re-added entry goes to the end of `auth_systems`**, which moves its
  login button last.

**The server certificate** is valid for a year. Replacing it leaves the
provider entry alone; the installer replaces Authelia's block in the
backend's trust list:

```bash
rm .secrets/authelia_tls_certificate.pem .secrets/authelia_tls_key.gen
python3 <skill>/scripts/install_authelia.py --admin-email <admin email> --no-up
docker compose up -d --force-recreate authelia
docker compose restart backend
```

## External provider

For a provider your organization already runs, use the skill's
`scripts/oidc_providers.py`:

```bash
python3 <skill>/scripts/oidc_providers.py add --id <id> \
    --title "<button label>" --server-url <issuer URL> \
    --client-id <client id> --pem-file <token-signing public key, PEM>
```

The script writes a public client (no client secret; soliplex always sends
PKCE), so the provider just needs the callback registered:
`https://${server_name}:${nginx_https}/api/auth/<id>`.

Registering the client is done with whoever runs the IdP; the flow is
scripted end to end only from the provider's side. Soliplex requires the
provider to issue RS256 access tokens; until soliplex's web client stops
expecting a refresh token (`soliplex/frontend#671`), a provider that sends
none ends the session a few minutes before the access token expires.

### Rotating a provider's signing key

`soliplex` never fetches signing keys itself; the key is stored in the
provider's entry, as `token_validation_pem`. When a provider rotates its
key, fetch the new public key as a PEM file and replace the entry. `add`
refuses an id that already exists, so remove the entry and add it back:

1. Note the entry's `title`, `server_url`, `client_id` and `scope` in
   `backend/environment/oidc/config.yaml`; `add` needs them again.
2. Stop the backend, so it never loads the config while the entry is
   missing. If it is the only provider, the backend would otherwise reload
   into running *without* authentication until the entry is back.
3. Remove the entry, add it back with the new key, and start the backend:

    ```bash
    docker compose stop backend
    python3 <skill>/scripts/oidc_providers.py remove <id>
    python3 <skill>/scripts/oidc_providers.py add --id <id> \
        --title "<title>" --server-url <server_url> \
        --client-id <client_id> --pem-file <new key, PEM> \
        [--scope "<scope>"]
    docker compose start backend
    ```

- **If this provider is the `/ingester/` sign-in provider and others
  remain**, `remove` needs `--signin-successor <another id>`. Pass
  `--signin` to `add` to make it the sign-in provider again. The sign-in
  provider ends where it started, so nginx needs no restart.
- **The re-added entry goes to the end of `auth_systems`**, which moves
  its login button last.
- **An entry `oidc_providers.py` didn't write**, for example one with a
  `client_secret` or other hand edits, loses those fields this way. Replace
  its `token_validation_pem` in `config.yaml` by hand instead; the backend
  reloads the file by itself.

## How the pieces fit

- `backend/environment/oidc/config.yaml` — the `auth_systems` entries. With
  none, Soliplex runs the fixed no-auth user; the first entry turns
  authentication on. The backend reloads this file by itself.
- `nginx/conf.d/signin-provider.conf` — names the one provider the
  `/ingester/` gate sends browsers to. nginx reads fragments at start only:
  after a change, `docker compose restart nginx`.
- `backend/environment/oidc/cacert.pem` — the backend's extra trust list; the
  provider's certificate is appended when its TLS is self-signed (as the
  in-stack Authelia's is).
- Provider ids appear in the login and callback URLs: lowercase letters,
  digits, `_` and `-`. `${server_name}:${nginx_https}` on this page are this
  stack's resolved values on its generated copy.
