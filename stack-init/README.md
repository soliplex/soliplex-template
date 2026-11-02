# stack-init

The `stack-init` service prepares the stack before it first serves anyone:

1. It brings the Soliplex databases to their current schema
   (`soliplex-cli database upgrade`).
2. It runs every step in `init.d/`, in name order: `*.sh` with `sh`, `*.py`
   with the backend image's Python. Other files, such as this README, are
   skipped.

Run it after generating the stack, before the first `docker compose up`,
and again after adding a part of the stack that brings its own step:

```bash
docker compose run --rm --quiet-build stack-init
```

Every step must be safe to re-run: check before changing anything, and
leave things alone when they are already in place.

The steps that ship with the stack:

- `10-no-auth-admin.py` makes Soliplex's no-auth user an admin while the
  stack has no OIDC provider, so admin-only routes such as `/ingester/`
  admit it. A stack with a provider gets no such entry: add your real
  admins with `soliplex-cli admin-users add`.
- `20-provisioner-admin.py` admits the room provisioner's service tokens as
  an admin's: an `admin_users` entry matching their subject
  (`PROVISIONER_SERVICE_SUBJECT`, default `soliplex-provisioner`).
