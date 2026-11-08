# Authelia, the in-stack provider

Loads when: Load this reference to install Authelia in a stack, or to manage its users, passwords, validation links, or key/TLS rotation.

## Installing and managing Authelia, the in-stack provider

For stacks without an external IdP, install the pinned in-stack Authelia
provider with `scripts/install_authelia.py`. It owns the stack's own sign-in
end to end: the provider runs inside the stack (reverse-proxied at
`/authelia/`), stores users and state in the `soliplex_authelia` Postgres
database, and the backend already trusts its self-signed certificate.

Run it from the stack directory (or pass `--project-dir`); its PEP 723
header pulls in `pyyaml` and `mako` under `uv run`:

```bash
uv run scripts/install_authelia.py --admin-email <you>@<domain>
```

The script is idempotent and never prompts. It writes
`compose.authelia.yaml` (pulled in by a top-level `include:` in
`docker-compose.yml`), `authelia/configuration.yml`, and
`nginx/conf.d/authelia.conf`; generates — and on later runs preserves — the
Authelia secrets and OIDC keys under `.secrets/`; registers the `soliplex`
OIDC client (public, PKCE S256, RS256 access tokens); creates the
`soliplex_authelia` role and database; runs `stack-init`; starts Authelia
(`up -d --wait authelia`); and creates the first admin user
(`--no-up` stops before everything that touches Docker — finish with
`stack-init`, `up -d`, and `authelia_users.py add --admin`).

Relay what it prints:

- the **admin password file** (`.secrets/authelia_user_<email>.gen`): hand
  its contents to the user out of band; the password is never printed.
- the **restart hint**: nginx reads `nginx/conf.d/authelia.conf` at start
  only — `docker compose restart nginx`.

After the install, manage Authelia's users with
`scripts/authelia_users.py` (run from the stack, or pass `--project-dir`;
the stack must be up):

```bash
uv run scripts/authelia_users.py add --email <user>@<domain>
uv run scripts/authelia_users.py list
uv run scripts/authelia_users.py reset <email>
uv run scripts/authelia_users.py remove <email>
uv run scripts/authelia_users.py code
```

Passwords are generated, hashed with the running Authelia, and written to
gitignored `.secrets/` files at mode `0600`; only the hash reaches
`authelia/users.yml`. `add --admin` also adds the matching soliplex admin
entry, matched on the user's opaque `sub` — `stack-init` and room
provisioning admit admins this way. Removing a user removes that entry too
(their identifier stays in storage, so re-adding the same email later
reassigns the same `sub`, and with it their admin entries and room ACLs).

Users change their own passwords in the Authelia portal: they confirm their
identity with a one-time **validation link**. Authelia writes it to
`authelia/notification.txt` (mode `0600`, gitignored), and each notification
**overwrites** the whole file, so two requests raised at the same moment keep
only the later one. `authelia_users.py code` prints that latest notification for the
operator to relay — normally a reset link, occasionally a revocation notice
that supersedes one. A stack with the SMTP notifier (a configuration
change) mails them instead, and no file is written.

Key rotation: the OIDC issuer key and TLS certificate are preserved across
re-runs. To rotate either, delete its files under `.secrets/`
(`authelia_oidc_issuer_private_key.gen` and
`authelia_oidc_issuer_certificate.pem`, or
`authelia_tls_certificate.pem` and `authelia_tls_key.gen`) and re-run the
install: it re-registers the provider entry and refreshes the backend's
trust list. With external providers, the equivalent is re-running
`scripts/oidc_providers.py add` with the provider's new signing key.
