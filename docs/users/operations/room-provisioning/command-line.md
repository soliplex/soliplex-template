---
icon: lucide/terminal
---

# From the command line

<!-- site-only -->
[:lucide-wand-sparkles: Ask the agent](../../../getting-started/using-the-skill.md#provision-a-private-room-for-a-user "The soliplex-template skill can do this for you")
{ .skill-link }

!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The provisioner also runs from the command line, in a one-off
`soliplex-dev` container, which carries `git` and the provisioner
(`soliplex-provision-room`). It needs the stack to be up; with OIDC on, the
backend must admit the provisioner (see
[With OIDC on](configuration.md#with-oidc-on)).

```bash
docker compose run --rm --quiet-build soliplex-dev \
    /app/.venv/bin/soliplex-provision-room \
    --claims '{"preferred_username": "alice", "email": "alice@example.com", "name": "Alice Example"}' \
    --name "Q3 Plans"
```

The room's id is `<owner>--<slug>`[^separator]: the owner comes from the requester's
`preferred_username` claim, the slug from the room's name (`q3-plans`), or
`--slug`. `--description` defaults to the name. The `soliplex-template`
skill's `provision_room.py` runs the same command, taking `--username` /
`--email` / `--display-name` as an alternative to `--claims`.

[^separator]: In general, a room's id is `<owner><separator><slug>`, where
    the separator defaults to `--`. Set `PROVISIONER_ROOM_ID_SEPARATOR` in
    the stack's `.env` to change it, for instance to keep the legacy form
    (`<owner>:<slug>`):

    ```bash
    PROVISIONER_ROOM_ID_SEPARATOR=:
    ```

    See [Settings](configuration.md#settings).

It exits `0` when the room is live, `1` when steps after its commit
remain (each is printed), and `2` on an error.
