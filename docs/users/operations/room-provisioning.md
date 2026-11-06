---
icon: lucide/door-open
---

# Room provisioning

<!-- site-only -->
[:lucide-wand-sparkles: Ask the agent](../../getting-started/using-the-skill.md#provision-a-private-room-for-a-user "The soliplex-template skill can do this for you")
{ .skill-link }

!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The room provisioner creates a private RAG room for a requester in one
step: the room's config, its own database, ingestion from the room's
uploads, and access for its owner alone: the requester, unless an admin
names another (see [Rooms for someone else](#rooms-for-someone-else)). It
runs in a one-off `soliplex-dev` container, which carries `git` and the
provisioner (`soliplex-provision-room`), and needs the stack to be up.

The provisioner is the template's own adapters (its room config, records,
ingester fragments and advice) over soliplex-concierge's provisioner
framework, which runs the steps below and serves the HTTP API.

```bash
docker compose run --rm --quiet-build soliplex-dev \
    /app/.venv/bin/soliplex-provision-room \
    --claims '{"preferred_username": "alice", "email": "alice@example.com", "name": "Alice Example"}' \
    --name "Q3 Plans"
```

The room's id is `<owner>:<slug>`: the owner comes from the requester's
`preferred_username` claim, the slug from the room's name (`q3-plans`), or
`--slug`. `--description` defaults to the name. The `soliplex-template`
skill's `provision_room.py` runs the same command, taking `--username` /
`--email` / `--display-name` as an alternative to `--claims`.

It exits `0` when the room is live, `1` when steps after its commit
remain (each is printed), and `2` on an error.

## What it does

Holding a lock (`src/.rooms-provisioned.lock`) through step 8, the
provisioner:

1. refuses a room id that anything already uses (see
   [Before anything changes](#before-anything-changes))
2. files a `new-room` tracking issue tagged `provision-start`, when a
   Gitea is configured and the concierge extension is installed
3. writes `room_config.yaml`, `REQUEST.md` (who asked for what, and
   what is set up) and `provision.yaml` (the same, as data) into
   `src/rooms-provisioned/<room-id>/`
4. creates the room's uploads directory, `uploads/rooms/<room-id>/`
5. registers the room's database with the ingester, in a fragment of its
   own, `src/rooms-provisioned/databases.d/<room-id>.yaml` (an `fs` source
   over the room's uploads, and its place in `lancedb.databases`), then has
   the ingester reload its config, which creates the database
6. makes the room private, granting READ and WRITE to its owner: by
   default, users whose `preferred_username` (`PROVISIONER_OWNER_CLAIM`)
   is the requester's; the policy is also recorded as the room's
   `authz.yaml`
7. audits the installation's rooms
8. commits the room (its directory, with all three records) and its
   fragment in `src/rooms-provisioned` (the requester as author), and
   pushes it when a Gitea is configured
9. has the backend reload its room configs, which makes the room live
10. tags the tracking issue `provision-complete`, or
    `provision-incomplete` when the steps after the commit left work for an
    operator (listed in the comment), then comments on and closes it

A failure before the commit undoes what the run itself created, and only
that (and tags the issue `provision-failed`, comments on it, and leaves it
open). After the commit, the room is valid as
committed: a step that fails there is reported, with how to finish it.

Two steps have time limits:

- the ingester's reload (120 seconds): one that times out may still apply
  after the run has undone its changes, so the failure says to reload the
  ingester again once it answers, and to remove the room's database
  directory if one appears
- the push (60 seconds): one that times out is reported as a step to finish

`src/rooms-provisioned` is a git repository of its own (the stack's
`src/*` rule ignores it), listed in `room_paths` as
`/app/src/rooms-provisioned`. The backend reads it through its read-only
`src/` mount, outside its config watch.

### Before anything changes

The provisioner refuses a room id when any resource named for it already
exists:

- its directory in the clone
- its database directory (`rag/db/<room-id>.lancedb`)
- its fragment in the clone (`databases.d/<room-id>.yaml`)
- an entry for it in either map of `haiku.rag/haiku.rag.yaml` or of a
  fragment (or another database placed at its location)

It also refuses to start when `haiku.rag/haiku.rag.yaml` cannot take the
fragments:

- its `databases_include` must name `rooms-provisioned/databases.d`, where
  the ingester mounts the clone's fragments
- every database in its `ingester.databases` must be placed in its
  `lancedb.databases`, as haiku.rag requires once fragments place theirs
- a non-empty uploads directory
- a room authorization policy

The refusal names each one found, and changes nothing: remove them, or
choose another name.

## Reconciling the stack with the clone

The clone records everything provisioning sets up for each room: its
config, its database's fragment (`databases.d/<room-id>.yaml`), and its
policy (`<room-id>/authz.yaml`). The policies themselves live in the
Postgres authz database, and the ingester's view of its databases in its
own process, so either can drift from the clone. Reconcile replays the
records:

```bash
docker compose run --rm --quiet-build soliplex-dev \
    /app/.venv/bin/soliplex-provision-room --reconcile
```

Holding the provisioner's lock, it:

1. applies each provisioned room's `authz.yaml`, where the stored policy
   differs, and creates its uploads directory where it is missing
2. has the ingester reload its config, which opens the database of each
   room whose fragment is in `databases.d/`
3. has the backend reload its room configs, which loads each room in the
   clone

It prints each change, and exits `0` when nothing needed changing, `1`
when it changed something, and `2` on an error. A second run changes
nothing.

!!! warning "After `docker compose down -v`"
    `down -v` deletes the authz database's volume, and with it every
    provisioned room's policy: a room with no policy is open to every user
    for READ. Run reconcile before letting users back in; the same goes for
    restoring the Postgres databases from a backup.

Rooms provisioned before the clone recorded policies (by template 0.20 or
0.21b1) have no `authz.yaml`. Reconcile names them, and leaves their
policies as they are.

Reconcile does not deprovision rooms. A room that is no longer in the clone
(its commit reverted, or its directory removed) is not reconciled: reconcile
leaves its policy, its database directory under `rag/db/` and its uploads
in place. Its reloads still follow the clone, though:

- the ingester retires the room's database, when its fragment is gone from
  `databases.d/`
- the backend stops serving the room, when its directory is gone

The policy left behind shows as stale in `soliplex-cli audit room-authz`,
and makes the provisioner refuse that room id until it is removed.

## The provisioned rooms' agent

Every provisioned room uses the `provisioned_rag` agent config from
`backend/environment/installation.yaml` as its agent's template, adding no
prompt of its own. Edit that entry's `model_name` or `system_prompt` to
change the agent of every provisioned room at once. Without the entry,
provisioning fails its rooms audit and rolls back.

## With OIDC on

The provisioner authenticates to the backend with service tokens it mints
for itself (`soliplex-cli service-token mint`, signed with the stack's
`url_safe_token_secret`): a fresh one for each call, valid for five
minutes, with the subject `soliplex-provisioner`. Nothing is stored. The
backend admits those tokens as an admin's through an `admin_users` entry
matching their subject, which `stack-init` adds (its
`init.d/20-provisioner-admin.py` step):

```bash
docker compose run --rm --quiet-build stack-init
```

Without the entry, the provisioner stops before changing anything, naming
the entry it needs, and the `soliplex-provisioner` service reports itself
unhealthy (`docker compose ps`): its log, and its `/ok`, name the entry
too. It keeps checking, so running `stack-init` is enough. To add the entry
by hand instead:

```bash
docker compose run --rm --quiet-build soliplex-dev \
    /app/.venv/bin/soliplex-cli admin-users add /environment \
    --json-path '$[?$.sub == "soliplex-provisioner"]'
```

A stack with no OIDC provider runs without authentication: it treats the
provisioner's calls as its no-auth user, which `stack-init` also makes an
admin.

## The provisioner service

The `soliplex-provisioner` service runs the same provisioner over HTTP, on
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
as the command line (see [With OIDC on](#with-oidc-on)).

### Rooms for someone else

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

## Settings

The provisioner reads these from the stack's `.env`:

| Variable | Meaning |
| --- | --- |
| `PROVISIONER_SERVICE_SUBJECT` | The subject (`sub` claim) of the provisioner's service tokens (default `soliplex-provisioner`); the `admin_users` entry above must match it, as `stack-init` makes it do. |
| `PROVISIONER_OWNER_CLAIM` | The claim a room's owner comes from (default `preferred_username`). |
| `PROVISIONER_FLATTEN_OWNER` | `true` to turn `@` into `_` in the owner, for email-shaped claims. |
| `PROVISIONER_LOWERCASE_OWNER` | `false` to keep the owner part's case from the claim (default `true`: lowercased). |
| `PROVISIONER_ROOM_ID_SEPARATOR` | What joins the owner part and the slug in a room id (default `--`, the concierge's; `:` keeps the legacy form). |
| `GITEA_HOST` / `GITEA_ACCESS_TOKEN` | The Gitea for tracking issues and the provisioned-rooms repository; unset means none. `scripts/init_gitea.py` sets both for the stack's own Gitea. |
| `GITEA_OWNER` / `GITEA_REPO` | The tracking-issue repository (default `soliplex-admin/soliplex-requests`, the concierge's). |
| `GITEA_ROOMS_REPO` | The provisioned-rooms repository, under `GITEA_OWNER` (default `rooms-provisioned`). |

For the stack's own Gitea, `scripts/init_gitea.py` creates both
repositories. On a Gitea hosted elsewhere, create the provisioned-rooms
repository before the first run; until then, each run reports the push as
a step to finish.

## Git policy

The provisioner commits with the requester as author. The committer is
the git configuration's `user.name` / `user.email`, falling back to
`Soliplex provisioner <provisioner@soliplex.invalid>`. To set a committer
identity, hooks or signing, mount a git config file into `soliplex-dev`
(the commented line in `docker-compose.yml`) and set
`GIT_CONFIG_GLOBAL=/etc/provisioner/gitconfig` in `.env`. Signing needs
its tools in the image: it has no `gpg`.

## Limits

- The lock serializes provisioner runs only. Hand edits of
  `haiku.rag/haiku.rag.yaml`, or `rag_db.py` / `add_room.py` runs, are not
  serialized with it; an ingester reload applies whatever the file says
  then.
- Every room the provisioner creates is a conversational RAG room over its
  own database; suggestions are left out, for an edit once documents are
  ingested.
