---
icon: lucide/package
---

# Custom Python package

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

Every entry under `src/` is a self-contained project directory: a
`pyproject.toml`, an `src/` package inside it, and its own tests. This
project's own is `src/myproject/`:

```text
src/
  myproject/
    pyproject.toml
    src/myproject/
    tests/unit/
  <other>/
```

`src/myproject/` is the project directory; `src/myproject/src/myproject/` is
the importable package inside it. `<other>/` is whatever else you clone —
same shape, no special handling.

Work in it the way you would work in any checkout:

```bash
cd src/myproject
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
uv pip install -e .     # or a plain editable install into another environment
```

The stack root has its own `pyproject.toml`, but it is a tooling environment
(`soliplex-cli`, the docs site) rather than a library — the importable code all
lives under `src/`.

## How it reaches the backend

The Soliplex backend bind-mounts `./src` (read-only) into its container as
`/app/src` and puts **this project's package directory** on `PYTHONPATH` as
`/app/src/myproject/src` (see `docker-compose.yml`), so anything you define
here is importable by **dotted name** from the Soliplex config under
`backend/environment/` — no image rebuild needed to edit your code.

Because only the package directories named in `PYTHONPATH` are import roots,
`/app/src` itself is not one: a repo you clone next to this project is
visible to the container but contributes nothing to the import namespace
until you add its own `src/` to `PYTHONPATH`.

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

Add modules under `src/myproject/src/myproject/`, reference them by dotted name
from the config, and add tests under `src/myproject/tests/unit/`. Delete the
demonstration `custom` room once you have your own.

## Adding a second project

`src/` is where you clone any other repo you want to work on alongside this
one — the `soliplex` backend itself, a library you depend on, a tool you are
patching.

### 1. Clone it

The `soliplex-template` skill does the clone and the wiring below in one step,
which is the recommended route:

```bash
uv run <skill>/scripts/src_projects.py clone <url>
uv run <skill>/scripts/src_projects.py list     # what is there, what is on the path
```

By hand it is just:

```bash
git clone <url> src/<other>
```

Nothing to do for git either way: `.gitignore` ignores everything under `src/`
except this project's own directory, so the clone stays part of its own repo
and never shows up in this stack's `git status`. That is a rule, not a list — a
third and fourth clone need no further edits.

### 2. Work on it

Each project directory carries its own environment, so treat it as the
standalone checkout it is:

```bash
cd src/<other>
uv sync
uv run pytest
```

Its `.venv` is its own; nothing is shared with this project's, or with the
stack root's.

### 3. Make it importable by the backend (optional)

Only needed if the backend should run *that* checkout's code. The `./src`
bind mount already carries it into the container at `/app/src/<other>`; what
it lacks is a place on the import path.

`src_projects.py clone` does this automatically, and `src_projects.py add
<other>` does it for a checkout you cloned by hand. Either way it also checks
the checkout's declared dependencies against the backend image and tells you
whether a rebuild is needed — see *What `PYTHONPATH` does and does not do*
below for why that matters.

By hand, append its package directory to the backend's `PYTHONPATH` in
`docker-compose.yml`:

```yaml
      PYTHONPATH: /app/src/myproject/src:/app/src/<other>/src
```

The trailing `/src` is right for a checkout that uses a `src/` layout (most
modern projects, including this one). For a flat-layout repo — packages
sitting at the repo root — use `/app/src/<other>` instead. Point at the
directory that *contains* the importable package, never at the package
itself, and never at `/app/src`: making the mount point an import root is
what this layout exists to avoid.

Then restart the backend. This is a compose change, so the `--reload=config`
flag does not pick it up:

```bash
docker compose up -d backend
```

### What `PYTHONPATH` does and does not do

- **It shadows installed distributions.** `PYTHONPATH` is searched before
  site-packages, so a `soliplex` checkout listed there wins over the
  `soliplex` installed in the image. That is what makes a backend dev-mode
  checkout work — and what makes an accidental name collision confusing.
  For that case, `src_projects.py --reload-python` also switches the backend's
  serve command to `--reload=both`, so editing the checkout restarts the
  server; it offers this as a hint when it notices a checkout providing the
  `soliplex` package, and only acts when you ask.
- **It does not install anything.** The checkout's own dependencies are *not*
  resolved: only its source becomes importable. If it imports a third-party
  package the backend image does not already have, the import fails at
  runtime — and `docker compose up -d backend` will not fix it, because the
  dependency has to be baked into the image. Add it to
  `backend/constraints.txt` and the `uv add` line in `backend/Dockerfile`,
  then `docker compose build backend`; see [Backend image &
  dependencies](architecture/backend.md). `src_projects.py` checks this for
  you and says which of the three cases you are in.
- **The mount is read-only**, and the container runs as the `PUID:PGID` from
  `.env`. Edit the checkout on the host; the backend only reads it.
