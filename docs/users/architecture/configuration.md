---
icon: lucide/settings
---

# Backend configuration

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The backend's behavior is driven by the files under `backend/environment/`,
bind-mounted into the container at `/environment`. Because the backend runs
with `--reload=config`, edits here take effect without a rebuild.

## Layout

- `installation.yaml` — the top-level install config: agents, secrets, env
  vars, room list, skills, DB URIs, paths. **Start here.**
- `rooms/<name>/room_config.yaml` — per-room agent prompts, tools, and skills.
- `skills/<name>/` — filesystem skills discovered via
  `filesystem_skills_paths`.
- `logging.yaml`, `haiku.rag.yaml` — logging and RAG configuration.

`installation.yaml` is heavily commented with pointers to the
[Soliplex config docs](https://soliplex.github.io/soliplex/config/). Those
comments describe **defaults** — so a section being empty or absent is not the
same as being unconfigured.

!!! note "Two sources of truth to remember"
    - `room_paths` in `installation.yaml` lists the directories rooms load
      from. It points at `./rooms`, so every `rooms/<name>/room_config.yaml`
      is loaded; to leave a room out, remove its directory or replace `./rooms`
      with an explicit list.
    - A filesystem skill must be both present under a `filesystem_skills_paths`
      directory **and** declared in `skill_configs` to be enabled.

## Sandbox (code execution for agents)

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a
  `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build
  time, so adding or changing one requires `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents
  at runtime (gitignored).

Dependencies needed by agent-executed sandbox code belong in the relevant
sandbox environment, **not** in the backend image's top-level dependency list
(see [Backend image & dependencies](backend.md)).

Running sandbox code is **opt-in**, chosen when the stack is generated
(`enable_sandbox`). The backend image includes `bwrap` (bubblewrap), but
inside a container `bwrap` needs user namespaces and a fresh `/proc`.
Docker's default seccomp profile, AppArmor profile and `/proc` masking each
block one of those, so all three have to be lifted. A stack generated with
`enable_sandbox: true` carries this `security_opt` block on the `backend`
service in `docker-compose.yml` (and the matching line on `soliplex-dev`);
one generated without it carries the same lines commented out. To opt in
later, uncomment both and recreate the containers:

```yaml
    security_opt: &sandbox_security_opt
      - seccomp=unconfined
      - apparmor=unconfined
      - systempaths=unconfined
```

That relaxes the backend container's own hardening, in exchange for `bwrap`
isolating each execution inside it. Without it, a room that configures the
`bubble-sandbox` skill fails when used, with "Sandbox unavailable": soliplex
checks that `bwrap` can run before it hands the room the skill. The skill's
`add_room.py` checks the same `security_opt` block, and refuses to add such
a room (the `sandbox` template) to a stack without it.
