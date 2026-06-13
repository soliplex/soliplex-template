---
icon: lucide/concierge-bell
---

# Concierge room

Add self-service room requests to a stack: an `about-<project>` room where users
ask for new rooms, and each request is filed as a Gitea issue for an
administrator to act on. This builds on
[Work with Gitea](work-with-gitea.md) — you need the in-stack Gitea provisioned.

## Prerequisites

- A stack with Gitea provisioned (see [Work with Gitea](work-with-gitea.md)),
  including the tracking repo and an access token.[^1]
- The `soliplex-template` skill installed in your agent (see
  [First steps](01-first-steps.md)).

## 1. Install the concierge extension

Install the
[`soliplex-concierge-installer`](https://github.com/soliplex/soliplex-concierge/releases/tag/installer-skill-latest)
skill into your agent. (When you run it in the next step, it also fetches the
`soliplex-concierge-room` and `soliplex-docs` skills it depends on.)

## 2. Add the `about-<project>` room

Ask your agent — using the `soliplex-concierge-installer` skill — to wire
concierge into your stack, pointed at your Gitea owner and repo. The skill runs
its bundled `apply.py` (previewing with a dry run, then applying), which creates
the **`about-<project>`** room — hosting a `create_gitea_issue` tool and the
`soliplex-concierge-room` skill — and merges the matching `installation.yaml`
entries.

## 3. Rebuild the backend

`apply.py` edits `backend/pyproject.toml`, `backend/Dockerfile`, `.env`, and
`backend/environment/`, so rebuild and restart the backend to pick up the new
dependency and configuration:

```bash
docker compose up -d --build backend
```

The `about-<project>` room then appears in the room list.

## 4. Request a room

Open the `about-<project>` room and ask for a new one, e.g.:

> Please open a room called "research" for the research team.

The room's agent calls its `create_gitea_issue` tool and files the request.

## 5. Confirm the request in Gitea

In the Gitea web UI, open the tracking repo's **Issues** — your request is
there, labelled `new-room`, ready for an administrator to act on.

## Where next

An administrator reviews these requests, creates the room with the
`soliplex-template` skill, and resolves the issue — using the
`soliplex-concierge-admin` skill. See
[Resolve room requests](concierge-admin.md).

Back to [Next steps](04-next-steps.md).

[^1]: The concierge can file against any Gitea repository, so your stack need
    not run its own `gitea` service at all. If you use an **external** Gitea
    repository, ensure the token you supply has **Write** access to the repo:
    a read-only token can open issues but Gitea silently drops their labels,
    so requests would arrive untagged.
