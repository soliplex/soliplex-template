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
docker compose run --rm soliplex-dev \
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

Holding a lock (`src/.rooms-provisioned.lock`) for the whole run, the
provisioner:

1. files a `new-room` tracking issue, when a Gitea is configured and the
   concierge extension is installed
2. writes `room_config.yaml` and `REQUEST.md` (who asked for what, and
   what is set up) into `src/rooms-provisioned/<room-id>/`
3. creates the room's uploads directory, `backend/uploads/rooms/<room-id>/`
4. registers the room's database in `haiku.rag/haiku.rag.yaml` (an `fs`
   source over the room's uploads, and its place in `lancedb.databases`),
   then has the ingester reload its config, which creates the database
5. makes the room private, granting READ and WRITE to the requester's
   claim value
6. audits the installation's rooms
7. commits the room in `src/rooms-provisioned` (the requester as author),
   and pushes it when a Gitea is configured
8. has the backend reload its room configs, which makes the room live
9. comments on and closes the tracking issue

A failure before the commit undoes the earlier steps (and comments on the
tracking issue, leaving it open). After the commit, the room is valid as
committed: a step that fails there is reported, with how to finish it.

`src/rooms-provisioned` is a git repository of its own (the stack's
`src/*` rule ignores it), listed in `room_paths` as
`/app/src/rooms-provisioned`. The backend reads it through its read-only
`src/` mount, outside its config watch.

## Settings

The provisioner reads these from the stack's `.env`:

| Variable | Meaning |
| --- | --- |
| `SOLIPLEX_ADMIN_TOKEN` | A bearer token the backend admits as an admin user, for the room-configs reload. Unneeded when the backend runs in no-auth mode; the provisioner checks it before changing anything. `provision_room.py` passes it through from its own environment, too. |
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
