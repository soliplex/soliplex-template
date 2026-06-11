---
icon: lucide/settings
---

# Backend configuration

The backend's behavior is driven by the files under `backend/environment/`,
bind-mounted into the container at `/environment`. Because the backend runs
with `--reload=config`, edits here take effect without a rebuild.

<%text>## Layout</%text>

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

<%text>## Sandbox (code execution for agents)</%text>

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a
  `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build
  time, so adding or changing one requires `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents
  at runtime (gitignored).

Dependencies needed by agent-executed sandbox code belong in the relevant
sandbox environment, **not** in the backend image's top-level dependency list
(see [Backend image & dependencies](backend.md)).
