---
icon: lucide/package
---

# Custom Python package

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

This project is an installable Python library: your own code lives under
`src/myproject/` and its tests under `tests/unit/`.

```bash
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
uv pip install -e .     # or a plain editable install into another environment
```

## How it reaches the backend

The Soliplex backend bind-mounts `./src` (read-only) into its container and
puts it on `PYTHONPATH` (see `docker-compose.yml`), so anything you define
here is importable by **dotted name** from the Soliplex config under
`backend/environment/` — no image rebuild needed to edit your code.

## What ships wired up

Two examples are referenced from the config so you can see the pattern:

- a tool, `myproject.tools.greeting`, referenced from
  `backend/environment/rooms/custom/room_config.yaml`;
- a FastAPI router, `myproject.views.router`, registered via
  `app_router_operations` in `backend/environment/installation.yaml`.

Dotted names into this package can equally be used in the `installation.yaml`
`meta:` section (tool/agent/skill config classes, MCP wrappers, secret sources)
— see the commented `myproject.*` examples there.

## Making it your own

Add modules under `src/myproject/`, reference them by dotted name from
the config, and add tests under `tests/unit/`. Delete the demonstration
`custom` room once you have your own.
