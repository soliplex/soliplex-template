---
icon: lucide/settings
---

# Backend configuration

The backend's behavior is driven by the files under `backend/environment/`,
bind-mounted into the container at `/environment`. Because the backend runs with
`--reload=config`, edits here take effect without a rebuild.

## Layout

| Path | What it configures |
|------|--------------------|
| `installation.yaml` | The top-level Soliplex install config: agents, secrets, environment vars, room list, skills, DB URIs, upload/sandbox paths. **Start here.** |
| `rooms/<name>/room_config.yaml` | Per-room agent prompts, tools, and skills. |
| `skills/<name>/` | Filesystem skills discovered via `filesystem_skills_paths`. |
| `completions/`, `quizzes/`, `oidc/` | Feature-specific configs referenced from `installation.yaml`. |
| `logging.yaml`, `haiku.rag.yaml` | Logging and RAG configuration. |

`installation.yaml` is heavily commented with pointers to the
[Soliplex config docs](https://soliplex.github.io/soliplex/config/). Those
comments describe **defaults** — so a section being empty or absent is not the
same as being unconfigured.

!!! note "Two sources of truth to remember"
    - The `room_paths` list in `installation.yaml` determines which rooms load.
      Adding a directory under `rooms/` without listing it there does nothing.
    - A filesystem skill must be both present under a `filesystem_skills_paths`
      directory **and** declared in `skill_configs` to be enabled.

## Sandbox (code execution for agents)

- `backend/sandbox/environments/<name>/pyproject.toml` — each subdirectory is a
  `uv` project. The backend Dockerfile runs `uv sync --frozen` on each at build
  time, so adding or changing a sandbox environment requires
  `docker compose build backend`.
- `backend/sandbox/workdirs/` — per-run working directories created by agents at
  runtime (gitignored).

Dependencies needed by agent-executed sandbox code belong in the relevant
sandbox environment, **not** in the backend image's top-level dependency list
(see [Backend image & dependencies](backend.md)).
