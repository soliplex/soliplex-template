---
icon: lucide/clipboard-check
---

# Resolve room requests

The administrator's side of the concierge: review the room requests filed as
Gitea issues, create the requested room, and close the request. Start with
[Concierge room](concierge-room.md) so you have a request to resolve.

## Prerequisites

- A stack with the concierge installed and at least one open request (see
  [Concierge room](concierge-room.md)).
- The `soliplex-template` skill, plus the
  [`soliplex-concierge-admin`](https://github.com/soliplex/soliplex-concierge/releases/tag/admin-skill-latest)
  skill, installed in your agent.
- Access to the tracking repo with a token that can comment on and close issues
  (typically more privileged than the room's issue-filing token).

## 1. Install the admin skill

Install the
[`soliplex-concierge-admin`](https://github.com/soliplex/soliplex-concierge/releases/tag/admin-skill-latest)
skill into your agent. It drives `gitea_issues.py` — the installer dropped a
copy at `scripts/gitea_issues.py` in your stack, so the same CLI runs from the
stack directory, configured with your Gitea host and admin token (via flags or
`GITEA_HOST` / `GITEA_ACCESS_TOKEN`).

## 2. Review the open requests

Ask your agent — using the `soliplex-concierge-admin` skill — to list the open
requests:

```bash
uv run scripts/gitea_issues.py list --owner <owner> --repo <repo>
```

The `research` request from [Concierge room](concierge-room.md) appears, tagged
`new-room`. Read its details — name, purpose, knowledge sources, access — with:

```bash
uv run scripts/gitea_issues.py show <number> --owner <owner> --repo <repo>
```

(If the request labels were never created, run `gitea_issues.py init` once; it
is idempotent.)

## 3. Create the requested room

Ask your agent — using the `soliplex-template` skill — to create the room the
issue describes: its name, prompt, and any requested tools, plus a RAG database
if knowledge sources were named. Then commit the new room to `main` and push it
to Gitea:

```bash
git add backend/environment/rooms/research/
git commit -m "Add the 'research' room (concierge request #<number>)"
git push
```

Unlike the [developer pull-request flow](work-with-gitea.md), an administrator
fulfilling a request commits straight to `main`.[^1]

## 4. Resolve the request

Once the room is live, ask your agent (admin skill) to record the outcome and
close the issue:

```bash
uv run scripts/gitea_issues.py close <number> --owner <owner> --repo <repo> \
    --body "Created the 'research' room and pushed it to main."
```

The requester sees the new room in their room list, and the issue closes with a
note of what was done.

## Where next

The admin skill also resolves *private-room access* requests — granting or
denying a user access to an existing room — which work the same way: review,
act, and close.

Back to [Next steps](04-next-steps.md).

[^1]: You can let the push close the request instead of doing step 4: add a
    closing keyword to the commit message — e.g. a `Closes #<number>` line — and
    Gitea closes the referenced issue when the commit lands on `main`. The
    skill's explicit `close` is still worth it when you want to record an
    outcome comment alongside the closure.
