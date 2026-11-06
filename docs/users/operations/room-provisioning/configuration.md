---
icon: lucide/settings
---

# Configuration

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The provisioner's settings, what it needs from the backend with OIDC on,
how it commits, and the agent its rooms use.

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

## Git policy

The provisioner commits with the requester as author. The committer is
the git configuration's `user.name` / `user.email`, falling back to
`Soliplex provisioner <provisioner@soliplex.invalid>`. To set a committer
identity, hooks or signing, mount a git config file into `soliplex-dev`
(the commented line in `docker-compose.yml`) and set
`GIT_CONFIG_GLOBAL=/etc/provisioner/gitconfig` in `.env`. Signing needs
its tools in the image: it has no `gpg`.

## The provisioned rooms' agent

Every provisioned room uses the `provisioned_rag` agent config from
`backend/environment/installation.yaml` as its agent's template, adding no
prompt of its own. Edit that entry's `model_name` or `system_prompt` to
change the agent of every provisioned room at once. Without the entry,
provisioning fails its rooms audit and rolls back.
