---
icon: lucide/refresh-cw
---

# Reconciling the stack with the clone

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

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
