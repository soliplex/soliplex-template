---
icon: lucide/door-open
---

# Room provisioning

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The room provisioner creates a private RAG room for a requester in one
step: the room's config, its own database, ingestion from the room's
uploads, and access for the requester alone. It runs in a one-off
`soliplex-dev` container, which carries `git` and the provisioner
(`soliplex-provision-room`), and needs the stack to be up.

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
2. files a `new-room` tracking issue, when a Gitea is configured and the
   concierge extension is installed
3. writes `room_config.yaml` and `REQUEST.md` (who asked for what, and
   what is set up) into `src/rooms-provisioned/<room-id>/`
4. creates the room's uploads directory, `uploads/rooms/<room-id>/`
5. registers the room's database in `haiku.rag/haiku.rag.yaml` (an `fs`
   source over the room's uploads, and its place in `lancedb.databases`),
   then has the ingester reload its config, which creates the database
6. makes the room private, granting READ and WRITE to the requester's
   claim value
7. audits the installation's rooms
8. commits the room in `src/rooms-provisioned` (the requester as author),
   and pushes it when a Gitea is configured
9. has the backend reload its room configs, which makes the room live
10. comments on and closes the tracking issue

A failure before the commit undoes what the run itself created, and only
that (and comments on the tracking issue, leaving it open). After the commit, the room is valid as
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
- an entry for it in either map of `haiku.rag/haiku.rag.yaml` (or another
  database placed at its location)
- a non-empty uploads directory
- a room authorization policy

The refusal names each one found, and changes nothing: remove them, or
choose another name.

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
minutes, with the subject `soliplex-provisioner`. Nothing is stored. Admit
those tokens as an admin once, by their subject:

```bash
docker compose run --rm --quiet-build soliplex-dev \
    /app/.venv/bin/soliplex-cli admin-users add /environment \
    --json-path '$[?$.sub == "soliplex-provisioner"]'
```

Until then, the provisioner stops before changing anything, naming the
entry it needs. A backend in no-auth mode admits it without one.

## Settings

The provisioner reads these from the stack's `.env`:

| Variable | Meaning |
| --- | --- |
| `PROVISIONER_SERVICE_SUBJECT` | The subject (`sub` claim) of the provisioner's service tokens (default `soliplex-provisioner`); the `admin_users` entry above must match it. |
| `PROVISIONER_OWNER_CLAIM` | The claim a room's owner comes from (default `preferred_username`). |
| `PROVISIONER_FLATTEN_OWNER` | `true` to turn `@` into `_` in the owner, for email-shaped claims. |
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
  serialized with it.
- Every room the provisioner creates is a conversational RAG room over its
  own database; suggestions are left out, for an edit once documents are
  ingested.
