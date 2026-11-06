---
icon: lucide/list-ordered
---

# What it does

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The provisioner is the template's own adapters (its room config, records,
ingester fragments and advice) over soliplex-concierge's
[provisioner framework](https://soliplex.github.io/soliplex-concierge/tasks/provisioners/),
which runs the steps below and serves the HTTP API.

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

## Before anything changes

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

## Limits

- The lock serializes provisioner runs only. Hand edits of
  `haiku.rag/haiku.rag.yaml`, or `rag_db.py` / `add_room.py` runs, are not
  serialized with it; an ingester reload applies whatever the file says
  then.
- Every room the provisioner creates is a conversational RAG room over its
  own database; suggestions are left out, for an edit once documents are
  ingested.
