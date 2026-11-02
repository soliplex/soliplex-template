---
icon: lucide/wand-sparkles
---

# Using the skill

The `soliplex-template` [Agent Skill](https://agentskills.io/) does more than
[generate a project](generator.md): once a stack exists, a coding agent (e.g.
Claude Code) can use the skill to inspect it, extend it, and keep it current.
You describe what you want in plain language; the agent asks for whatever it
still needs, runs the skill's scripts, and reports back.

This page is for the person working *with* that agent: what you can ask for,
what the agent will ask you, and which decisions it leaves to you. Every task
is also a script you can run yourself; each section links to the page that
covers the manual route.

## Before you start

- Start the agent in the stack's directory, or tell it where the stack is.
- Most tasks run a one-off container from the stack's backend image, so build
  the images first (`docker compose build`). Adding a room and migrating the
  layout touch only files.
- Provisioning a room for a user needs the stack running.
- In the "by hand" commands below, `<skill>` is wherever the skill is
  installed, e.g. `~/.claude/skills/soliplex-template`.

## Inspect a running stack's configuration

*"Which rooms does this stack load?"*, *"What is room_paths set to?"*,
*"Show me the chat room's config."*

The agent asks the backend for its **resolved** configuration (`soliplex-cli
config`), after defaults, secret and environment substitution, and
`room_paths` are applied. The answer reflects what the backend actually runs,
not what the files under `backend/environment/` appear to say.

By hand: `uv run <skill>/scripts/soliplex_config.py show` (or `get <path>`,
`rooms`, `room <room_id>`) from the stack directory. See
[Backend configuration](../users/architecture/configuration.md).

## Check the Ollama models

*"Are all the models this stack uses available?"*

The agent runs the backend's own audit, which checks every model the
resolved configuration names against the Ollama server it resolves to, and has
each one answer a request. Use it after changing `OLLAMA_BASE_URL` in `.env`,
or when requests start failing because the server lost a model. The agent
offers to pull any that are missing.

By hand: `soliplex-cli audit ollama --check-responsive` and `soliplex-cli
ollama pull` in a backend container; see
[Local Ollama](../users/operations/ollama.md) for reaching a server on the
Docker host.

## Build a separate RAG database

*"Build a RAG database called handbook from rag/docs/handbook/."*
*"Add this URL to the handbook database."*

The stack's ingester maintains one database continuously. For a separate,
mostly static corpus, the agent builds a second database under
`rag/db/<name>.lancedb` with the stack's own `haiku-rag` image and settings,
so it never collides with the ingester. Sources can be a path under
`rag/docs/`, a URL or S3 URI, or any local path. It can also add documents to,
re-index, or remove documents from a database built this way.

A new database is not used by any room until you wire it into one; the agent
offers to do that next.

By hand: `<skill>/scripts/rag_db.py create` / `update`; see
[RAG pipeline](../users/operations/rag.md).

## Point rooms at a RAG database

*"Point the chat room at the handbook database."*

The agent lists the rooms the stack loads, with their names and descriptions,
and lets you pick one or more. It then edits each room's `room_config.yaml`
in place (comments kept) to read the database. A room that has a `skills:`
block without the RAG skill in it is reported rather than edited, for you to
wire by hand.

By hand: `<skill>/scripts/rag_db.py add-rag-to-room`; see
[Separate RAG database](tutorials/separate-rag-database.md).

## Add a room

*"Add a search room for the handbook."*

The agent offers the skill's room templates (`chat`, `search`, `minimal`, and
`sandbox`) and asks for the room id, name, description, agent template, system
prompt, and (for templates that search) the RAG database to read. It shows
you a dry run before writing anything, then tells the running backend to
reload its rooms and confirms the new room loads. The `sandbox` template is
offered only for a stack generated with the sandbox enabled.

The new `room_config.yaml` includes commented-out examples of custom tools
and skills, for you to uncomment later.

By hand: `<skill>/scripts/add_room.py list` / `add`; see
[Backend configuration](../users/architecture/configuration.md).

## Provision a private room for a user

*"Provision a room called Q3 Plans for alice."*

The agent creates a private RAG room on that user's behalf: its own database
fed from the room's uploads, access for that user alone, and its config
committed to the stack's `src/rooms-provisioned` repository. It asks for the
user's username (plus email and display name if known) and the room's name;
the room id becomes `<username>:<slug>`.

The provisioner needs an `admin_users` entry, which the stack's `stack-init`
adds. If the entry is missing, the agent says so and offers to run
`stack-init`.

It leaves one thing to you: **leftovers from a room removed by hand.** If an
earlier room with the same id left its database, uploads, or authorization
policy behind, provisioning refuses and the agent lists what is left. It
does not delete them to get past the error.

By hand: `<skill>/scripts/provision_room.py`; see
[Room provisioning](../users/operations/room-provisioning.md).

## Work on another project alongside the stack

*"Clone the soliplex repo into this stack."*

The agent clones the project into `src/`, puts its package on the backend's
`PYTHONPATH`, and has the backend restart when its files change. It then
checks the project's dependencies against what the backend image has
installed and reports:

- **Missing packages** — add them to `backend/requirements.txt` and rebuild
  the backend; restarting alone will not fix the import error.
- **Conflicting versions** — the project wants versions the image does not
  have. The agent offers the fixes (adapt the project, pick a different
  soliplex image, or customize `backend/Dockerfile`) and lets you choose.
- **Changed dev dependencies** — the project's test dependencies are added to
  the `soliplex-dev` image; rebuild it and commit
  `soliplex-dev/requirements-dev.txt`.

If your backend runs without `--reload`, the agent leaves it that way.

By hand: `<skill>/scripts/src_projects.py clone` / `add` / `remove` / `list`;
see [Custom Python package](../users/custom-package.md).

## Migrate databases after a version bump

*"I bumped soliplex; do the databases need migrating?"*
*"I bumped haiku.rag; do the RAG stores need migrating?"*

Most bumps need no migration: the backend migrates its own databases on
startup, and most `haiku.rag` releases leave the store format alone. The
exceptions are a stack whose databases were created by soliplex 0.81 or
earlier, and a `haiku.rag` bump that crosses a store upgrade. The agent
asks which case yours is rather than guessing, then runs a read-only check.

To apply a migration, the backend (for the soliplex databases) or the ingester
(for the RAG stores) has to be stopped; the scripts refuse otherwise. The
agent suggests a backup first (`pg_dump`, or a copy of `rag/db/`). When you
change the `haiku.rag` version, it also checks that the new version is one
the stack's soliplex release accepts.

By hand: the stack's own `scripts/migrate_soliplex_dbs.py` and
`scripts/migrate_rag_dbs.py`, each with `--check`; see
[Database migrations](../users/operations/migrations.md).

## Move an older stack to the `src/` layout

*"Migrate this stack to the src/ layout."*

Stacks generated before v0.16 keep their package directly under `src/`. The
agent shows a dry run of the conversion, then applies it; the stack must be a
clean git checkout, so the result is reviewable as a diff. If a file has
drifted from what the script expects, nothing is changed and the error points
at the manual steps for that file.

By hand: `<skill>/scripts/migrate_layout.py`; see
[Move to the `src/` project layout](../upgrades/move-to-src-layout.md).

## Manage the skill's version

*"Is there a newer soliplex-template skill?"*

The skill can list, diff, and upgrade itself against its published builds:

```bash
uv run <skill>/scripts/skill_versions.py list          # published versions, newest first
uv run <skill>/scripts/skill_versions.py diff [TAG]    # installed copy vs a published build
uv run <skill>/scripts/skill_versions.py upgrade [TAG] # install a published build in place
```

Release builds (`v…`) are attached to tagged releases; rolling builds
(`template-skill-YYYY.MM.DD-<sha>`) track the main branch, and `latest` (the
default `TAG`) means the newest rolling build. Pass a `v…` tag to stay on
releases. Setting `GITHUB_TOKEN` raises the GitHub API rate limit.

## Where to look

The agent's own instructions are in
[`SKILL.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/SKILL.md).
They are written for the agent rather than for you, but they are the exact
text the agent follows.
