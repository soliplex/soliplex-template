---
icon: lucide/refresh-cw
---

# Hack on the backend

Run your stack against a **checkout** of Soliplex itself instead of the pinned
release, so an edit to the backend source restarts the server the way an edit
under `backend/environment/` already does. This builds on the core walkthroughs
— [First steps](01-first-steps.md) through
[Add a custom tool](03-add-a-custom-tool.md) — so have a stack you have brought
up at least once.

## Prerequisites

- A generated stack, up and working (the three core tutorials).
- A `soliplex-template` skill build of **v0.16 or later** — that release
  introduced both the `src/` project layout and `src_projects.py`. Ask your
  agent to upgrade it, or:

    ```bash
    uv run <skill>/scripts/skill_versions.py list
    uv run <skill>/scripts/skill_versions.py upgrade
    ```

- A stack **in the v0.16 `src/` project layout**. If you generated yours by
  following an earlier version of these tutorials, convert it first — see
  [Migrating to the `src/` project layout](../migrating-layout.md). This is
  not optional, and nothing will stop you: an older stack has
  `PYTHONPATH: /app/src`, which `src_projects.py` happily appends to, leaving
  the mount point itself an import root. `src/` is then a `sys.path` entry, so
  the checkout's **repo root** joins `soliplex.__path__` ahead of the real
  package — `soliplex.alembic` and `soliplex.tests` become importable, and the
  package you meant to edit is the second portion, not the first.

    `list` is the cheap pre-flight check; run it before step 1:

    ```bash
    uv run <skill>/scripts/src_projects.py list
    ```

    A converted stack names each project and shows your own marked `*` and on
    the path. An unconverted one gives itself away with a
    `(no such directory)  /app/src  yes` row.

- A stack whose own project is **not** named `soliplex`. Its project directory
  would be `src/soliplex/` — exactly where the checkout goes. The generator
  defaults to `soliplex-dojo` for this reason; if you named yours `soliplex`,
  generate a fresh one for this walkthrough.

## 1. Clone Soliplex into `src/`

Every entry under a stack's `src/` is a project directory, and the checkout
becomes a sibling of your own project. Ask your agent to *"clone the soliplex
backend into this stack's `src/` and turn on Python reload"*. The skill runs:

```bash
uv run <skill>/scripts/src_projects.py clone \
    --reload-python https://github.com/soliplex/soliplex
```

That does three things: clones the repo to `src/soliplex/`, appends
`/app/src/soliplex/src` to the backend's `PYTHONPATH` in `docker-compose.yml`,
and switches the serve command from `--reload=config` to `--reload=both`. It
reports what it did:

```text
cloned https://github.com/soliplex/soliplex into src/soliplex
added: /app/src/soliplex/src
  dependencies: every runtime dependency src/soliplex declares is already
                in the backend image.
  reload: backend serve flag '--reload=config' -> '--reload=both'; edits under
          the checkout now restart the server.
```

`--reload=python` watches `soliplex.__path__`, which is why the flag only earns
its keep once a checkout is on the path. Without `--reload-python` the script
notices the checkout provides `soliplex` and offers the switch rather than
making it for you.

Nothing is needed for git: `.gitignore` already ignores everything under `src/`
except the stack's own project, so the checkout stays part of its own repo.

## 2. Restart the backend

Editing `docker-compose.yml` is a compose change, which `--reload=config` does
not pick up:

```bash
docker compose up -d backend
```

## 3. Confirm the checkout is what's running

`PYTHONPATH` is searched before site-packages, so the checkout's modules win
over the installed release. Check it from inside the container with the
`soliplex-dev` service, which mirrors the backend's `PYTHONPATH` exactly:

```bash
docker compose run --rm soliplex-dev \
    /app/.venv/bin/python -c "import soliplex; print(soliplex.__path__)"
```

```text
['/app/src/soliplex/src/soliplex', '/app/.venv/lib/.../site-packages/soliplex']
```

`soliplex` is a namespace package, so those two directories **merge** rather
than one shadowing the other: every module the checkout has wins, and the
installed release fills the gaps. Two consequences worth knowing —

- a module the checkout *deletes* or renames stays importable from the
  installed copy, so a clean-looking run may still be executing old code;
- nothing was installed, so the distribution metadata still reports the pinned
  version. Anything that reads it — `pip list`, the admin
  `/v1/installation/versions` view — shows the release, not the checkout.

## 4. Watch an edit take effect

Follow the backend's log in one terminal:

```bash
docker compose logs -f backend
```

Then edit any module under `src/soliplex/src/soliplex/` on the host. The
reloader notices the change and restarts the server, and the next request runs
your edited code — no rebuild, no `docker compose up`.

The mount is read-only for the backend and the container runs as the
`PUID:PGID` from `.env`, so edit on the host; the backend only reads.

## 5. Run backend tasks against the stack

`soliplex-dev` is a one-shot task runner (held out of `docker compose up` by
`profiles: ["devmode"]`) built from the backend image, with the stack's config,
secrets and databases — and `src/` mounted **read-write**. Validate the
installation the backend actually serves:

```bash
docker compose run --rm soliplex-dev \
    /app/.venv/bin/soliplex-cli audit /environment
```

A checkout of `main` is usually ahead of the pinned release, so its database
schema may be too. Apply its migrations, and generate new ones, from the
checkout's own root (`alembic` writes into `versions/` and prepends `.` to
`sys.path`, so the `-w` matters):

```bash
docker compose run --rm -w /app/src/soliplex soliplex-dev \
    /app/.venv/bin/alembic -x soliplex.installation_path=/environment upgrade head
```

## 6. When you need a rebuild

`PYTHONPATH` makes source importable; it does not install anything. If the
checkout imports a distribution the backend image lacks, the import fails at
runtime and `docker compose up -d backend` will not fix it. `clone`/`add`
check for this and say which of three cases you are in — all satisfied, some
**MISSING** (it names them), or not checkable (no Docker, or the image is not
built).

The check is by distribution *name*, so a checkout needing a **newer version**
of something already in the image passes it and still fails at runtime. Either
way the fix is the same: add or bump the pin in `backend/constraints.txt` and
the `uv add` line in `backend/Dockerfile`, then

```bash
docker compose build backend
docker compose up -d backend
```

See [Backend image & dependencies](../../users/architecture/backend.md).

## 7. Going back to the release

Take the checkout off the import path — it leaves the clone itself untouched:

```bash
uv run <skill>/scripts/src_projects.py remove soliplex
```

Then set the serve command in `docker-compose.yml` back to `--reload=config`
(nothing does that for you) and `docker compose up -d backend`. At any point,
`src_projects.py list` shows which projects are under `src/` and which are on
the path.

## Where next

The same mechanism works for any repo, not just Soliplex — a library you depend
on, a tool you are patching — minus the reload flag, which is specific to the
`soliplex` package. See
[Custom Python package](../../users/custom-package.md) for that, and
[Next steps](04-next-steps.md) for the other walkthroughs.
