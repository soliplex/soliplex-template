---
icon: lucide/git-pull-request
---

# Work with Gitea

Ship a change through a pull request — hosted by the Gitea that runs inside the
stack itself. This builds on [Add a custom tool](03-add-a-custom-tool.md).

## Prerequisites

- The `soliplex-template` skill installed in your agent (see
  [First steps](01-first-steps.md)).
- `git` and an SSH key available (in your ssh-agent, or `~/.ssh/*.pub`) —
  `--push-to-gitea` registers it and pushes over SSH.

## 1. Generate a stack with Gitea

Ask your agent to generate a stack **with the Gitea service included**
(`include_gitea`). Then bring it up from the project directory:

```bash
docker compose up
```

## 2. Provision Gitea and back the repo

With the stack up, provision Gitea and point the project's git at it:

```bash
uv run scripts/init_gitea.py --admin-user yourname --push-to-gitea
```

This creates a web-UI admin user `yourname` (prompting for its password),
registers your SSH key, creates a repo, sets the project's git `origin` to it,
and pushes the initial commit. nginx serves the Gitea web UI at `/gitea/` on the
HTTPS port.

## 3. Make a change on a branch

Work on a branch so the change can land through review:

```bash
git checkout -b add-farewell
```

Now make a change following [Add a custom tool](03-add-a-custom-tool.md): add
the `farewell` tool, wire it into the `custom` room, test it, and commit. That
commit lands on this branch.

## 4. Push the branch

```bash
git push -u origin add-farewell
```

The push goes to the in-stack Gitea over SSH (the remote `init_gitea.py` set).

## 5. Open a pull request

Sign in to the Gitea web UI (the `/gitea/` path on the HTTPS port) as the admin
user from step 2. Gitea shows the freshly pushed `add-farewell` branch and
offers to open a pull request against `main`; create it.

## 6. Merge it

Review the diff in Gitea and merge the pull request from the web UI. Back in the
project, `git checkout main && git pull` brings the merged change down — your
stack now runs code you shipped through its own review workflow.

## Where next

The [Concierge room](concierge-room.md) walkthrough builds on this one — adding
an `about-<project>` room where users request new rooms that land as Gitea
issues. See also the other [Additional topics](04-additional-topics.md).
