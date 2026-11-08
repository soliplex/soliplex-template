# Projects in src/: cloning, wiring, and the old-layout migration

Loads when: Load this reference to clone or wire a Python project into the stack's src/ import path, or to convert a pre-#176 stack to the src/ project layout.

## Cloning another project into a stack's src/

Every entry under a stack's `src/` is a project directory, so a repo the owner
wants to hack on alongside their own — the `soliplex` backend itself, a library
they depend on — is cloned there as a sibling. `scripts/src_projects.py` does
the clone *and* the wiring:

```bash
uv run scripts/src_projects.py list                  # what is there, what is on the path
uv run scripts/src_projects.py clone <url>           # clone into src/ and wire it up
uv run scripts/src_projects.py add <name>            # a checkout cloned by hand
uv run scripts/src_projects.py remove <name>         # off the path; checkout untouched
uv run scripts/src_projects.py dev-requirements      # regenerate soliplex-dev's dev deps
```

`clone` picks the directory name from the URL (`--name` overrides), then
appends the checkout's package directory to the backend's `PYTHONPATH` in
`docker-compose.yml` — with the trailing `/src` only when the checkout uses a
`src/` layout, which it detects — and a matching `--reload-dirs` to the
backend's serve command. Nothing is needed for git: `.gitignore` already
ignores everything under `src/` except the stack's own project.

**It then checks the checkout's dependencies.** `PYTHONPATH` makes source
importable; it does not install anything. So `add`/`clone` read the checkout's
`[project].dependencies` and check each requirement inside the backend image
(a one-off `docker compose run --rm --no-deps backend`, using the image's own
`packaging`): its environment marker against the image's Python, its version
specifier against what the image has installed. The outcomes:

- all satisfied → the next step is just `docker compose up -d backend`;
- some **MISSING** → it names them and leads with `docker compose build
  backend`, because `up` alone will not fix an `ImportError`. Add them to
  `backend/requirements.txt` first;
- some **CONFLICT** — installed, at a version the checkout rejects → it names
  the requirement and the image's version. `requirements.txt` cannot fix
  this (the image's own versions are frozen as constraints), so offer, in
  this order: adapt the checkout to the image's versions (relax its
  requirement, port its code — the user cloned it to work on it); pick a
  soliplex image tag whose release has versions it accepts; customize
  `backend/Dockerfile`;
- not checkable (no docker, or the image is not built) → it says so and lists
  what the checkout declares, rather than guessing.

Only the checkout's *direct* requirements are checked. To catch a problem
deeper in its tree, import it in the backend's environment:
`docker compose run --rm --no-deps soliplex-dev /app/.venv/bin/python -c
"import <package>"`. An `ImportError` names what the image lacks.

**Hot reload (#209).** The backend's serve command carries one `--reload-dirs`
per `PYTHONPATH` entry — the stack's own package from generation — and
`add`/`clone`/`remove` keep the two in step, so editing any project on the path
restarts the server; `list` shows a `watched` column. A `soliplex` checkout
(dev mode) is no special case. Re-running `add` on an entry already on the path
adds a missing `--reload-dirs`: that is how a stack generated before this gets
its own package watched (`add <package>`). They also settle an older reload
mode: `--reload=both` becomes `--reload=config`, with a warning;
`--reload=python` is kept, being what turns reload on without watching the
config, with a note that `--reload=config` works too. A backend serving with no
`--reload` at all watches nothing: they edit only `PYTHONPATH`, and say so —
never turn reload on themselves.

**Dev dependencies for tests (#213).** The `soliplex-dev` service is built on
the backend's image (`soliplex-dev/Dockerfile`) plus every `src/` project's
`dev` dependency group, so a project's tests run there against the backend's
own versions: `docker compose run --rm --no-deps -w /app/src/<project>
soliplex-dev /app/.venv/bin/python -m pytest`. The groups are listed in the
committed `soliplex-dev/requirements-dev.txt`, which `add`/`clone` regenerate
as they go and `dev-requirements` regenerates on demand: after a project's
dev group changes, or after a project's directory is deleted from `src/`.
Every directory under `src/` contributes, on `PYTHONPATH` or not, so `remove`
(which only takes a project off the path and the watch list, leaving the
checkout) does not change the file. `dev-requirements --check` reports
whether it is up to date. When it changes, tell the user to rebuild (`docker compose build
soliplex-dev`) and to commit the file. A dev requirement that conflicts with
the image's versions fails that build; the fix is in the project's dev group.
A stack without a `soliplex-dev/` directory predates this, and the command
says so.

Pass `--no-dep-check` to skip that container round-trip, `--dry-run` to report
without writing, and `--no-path` to `clone` when the user wants the checkout
but not the import-path change.

Relay the reported outcome — especially a rebuild — rather than just saying the
clone succeeded.

## Migrating an existing stack to the src/ project layout

Stacks scaffolded before v0.16 put the package directly under `src/`, with its
tests at the stack root. Since #176 every entry under `src/` is a *project
directory* — `src/<pkg>/{pyproject.toml,src/<pkg>/,tests/}` — so a checkout
cloned alongside the project needs no special casing. Convert an old stack with
`scripts/migrate_layout.py`; do not do it by hand (the `pyproject.toml` split
is easy to get wrong, and dropping `[project]` leaves the stack root with no
tooling environment).

This is rung 0 of the stack upgrade ladder (the "Upgrading an existing
stack" docs page): every later rung assumes the new layout, so an old stack
converts before it climbs any further.

It reads `[tool.soliplex-template.params]` for the package name, moves the
tree with `git mv` (so history follows as renames), splits `pyproject.toml`,
repoints the backend's `PYTHONPATH`, and adds the `src/` ignore rule. Every
edit asserts on the text it expects, and nothing is written until all checks
pass — a drifted stack aborts intact rather than half-converted.

1. **Dry-run** to show the plan (run from the stack dir, or pass
   `--project-dir`):

   ```bash
   uv run scripts/migrate_layout.py --project-dir /path/to/stack --dry-run
   ```

2. **Apply** it. The working tree must be a clean git checkout so the result is
   reviewable as a diff; `--force` waives that (and moves files without
   `git mv`). Pass `--package-name` only if the stack has no manifest and the
   inference is ambiguous.

   ```bash
   uv run scripts/migrate_layout.py --project-dir /path/to/stack
   ```

3. **Verify** both halves — the second command is the one a hand-migration
   usually breaks:

   ```bash
   cd /path/to/stack/src/<pkg> && uv sync && uv run pytest
   cd /path/to/stack && uv sync && uv run zensical build
   ```

   Then `docker compose up -d backend` to pick up the new `PYTHONPATH`, and
   review the diff before committing. Comments in `installation.yaml` and
   `rooms/custom/room_config.yaml` still name the old paths; they are cosmetic.

If the script reports that a file has drifted from the exemplar, reconcile that
one file by hand and re-run — the page linked from the error has the manual
steps.
