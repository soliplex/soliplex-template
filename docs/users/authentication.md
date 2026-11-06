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

Authelia's OIDC signing key and the server certificate are generated once
and preserved across re-runs of `install_authelia.py`. To replace the
signing key, delete
`.secrets/authelia_oidc_issuer_private_key.gen` and
`.secrets/authelia_oidc_issuer_certificate.pem` and re-run the install: it
re-registers the provider entry and refreshes the backend's trust list. The
server certificate is valid for a year; rotate it the same way, deleting
`.secrets/authelia_tls_certificate.pem` and `.secrets/authelia_tls_key.gen`.

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

If a provider rotates its signing key, `soliplex` never fetches keys itself:
fetch the new signing key and run
`oidc_providers.py add` again with the same id and the new PEM, or remove and
re-add it (`--signin-successor` names the provider that takes over the
`/ingester/` sign-in while others remain).

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
