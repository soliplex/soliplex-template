---
icon: lucide/server
---

# The provisioner service

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The `soliplex-provisioner` service runs the provisioner over HTTP, on
a user's behalf. It starts with `docker compose up`, and listens on the
compose network only, at `http://soliplex-provisioner:8090`:

- `POST /rooms`, with `{"name": ..., "description": ..., "slug": ...,
  "owner": ...}` (all but the name optional), answers `202` with
  `{"room_id": ..., "issue_url": ...}` once the request is admitted, its id
  is free, and its tracking issue is filed; it then creates the room in the
  background
- `GET /ok` answers `200` once the backend admits the provisioner, and
  `503`, saying why, until then
- `GET /rooms/{room_id}` answers the request's state: `pending`, `live`,
  `incomplete` (with the steps still to do) or `failed` (with the error)

Refusals:

- `400`: an unusable name, slug or owner, or an owner's predicate the
  installation does not register
- `401`: a missing or invalid token
- `403`: a requester who is not admitted, or who names an owner without
  being an admin
- `404`: a `GET` for a request that is not the caller's (unless they are
  an admin), or that the service does not know
- `409`: a room id already taken, or with a request already pending; the
  body names the room, `{"detail": ..., "room_id": ...}`
- `422`: a body that is not a request, for instance an `owner` in neither
  form below
- `500`: a check or the provisioner failed; the service's log has the
  detail

Every answer carries `X-Soliplex-Provisioner-API: 1`, the version of the API
it serves.

A caller authenticates *as the requester*: its bearer token is a service
token carrying the requester's claims, signed with the stack's
`url_safe_token_secret` (code in the backend can mint one with
`soliplex.token_mint.mint_service_token`). Keep its lifetime short: the
backend accepts the same token as that user.

A requester is admitted when they are an admin, or hold WRITE on the
reserved room id `__provision__`. With no policy for that id, only admins
are admitted. To admit every signed-in user:

```bash
docker compose run --rm --quiet-build --no-TTY soliplex-dev \
    /app/.venv/bin/soliplex-cli room-authz from-yaml /environment \
    __provision__ <<'EOF'
room_id: "__provision__"
default_allow_deny: DENY
acl_entries:
  - allow_deny: ALLOW
    permission: WRITE
    authenticated: true
EOF
```

The service keeps request state in memory: a restart loses it, while the
tracking issue remains the record. The service needs the same admin entry
as the command line (see [With OIDC on](configuration.md#with-oidc-on)).

[A request, end to end](index.md#a-request-end-to-end) follows one
request through the service, step by step.

## Rooms for someone else

An admin may request a room owned by someone other than themselves, with an
`owner` in one of two forms:

- one person, by a claim and its exact value:

    ```json
    {"name": "Q3 Plans",
     "owner": {"owner": "bharney", "claim": "email",
               "value": "bharney@example.com"}}
    ```

- everyone a predicate recognizes: a function the installation registers
  under `meta.jsonpath_functions` in `backend/environment/installation.yaml`,
  applied to a user's claims as a whole:

    ```json
    {"name": "Quarry Notes",
     "owner": {"owner": "foremen", "predicate": "is_foreman"}}
    ```

`owner.owner` is the room id's owner part (`foremen--quarry-notes`). The
provisioner builds the room's access rule from the form; no caller writes a
JSONPath, and it refuses a predicate the installation does not register.
The requester is still recorded as the one who asked, and may see the
request.

The concierge's about room asks for such rooms on an admin's behalf. A stack
whose about room was installed before soliplex-concierge's installer skill
0.7.0 gets that only by reinstalling the room (`install_concierge.py …
--force`); a reinstalled room's `room_config.yaml` starts with an
`# Installed by soliplex-concierge-installer <version>` line.
