---
icon: lucide/folder-tree
---

# Migrating to the `src/` project layout

Stacks generated before soliplex-template v0.16 put the project's package
*directly* under `src/`, with its tests at the stack root:

```text
<stack>/
  pyproject.toml
  src/<package>/            # tools.py, views.py
  tests/unit/
```

`src/` was therefore one specific package rather than "the source trees",
which left nowhere to put a second one. Newly generated stacks instead make
every entry under `src/` a project directory of the same shape:

```text
<stack>/
  pyproject.toml            # stack tooling only
  src/
    <package>/              # this project
      pyproject.toml
      src/<package>/        # tools.py, views.py
      tests/unit/
    <other>/                # anything you clone alongside it
```

Nothing forces an existing stack to convert — the old layout keeps working.
Convert when you want to clone a second repo (a `soliplex` checkout, a
third-party dependency) into the stack without ignore rules per clone.

## Use the script

Do not do this by hand. The skill ships `scripts/migrate_layout.py`, which
performs every step below and asserts on the text it expects to find at each
one:

```bash
cd <stack>
uv run <skill>/scripts/migrate_layout.py --dry-run   # show the plan
uv run <skill>/scripts/migrate_layout.py             # apply it
```

It reads the package name from this stack's own
`[tool.soliplex-template.params]`, moves the tree with `git mv` so history
follows as renames, splits `pyproject.toml`, repoints the backend's
`PYTHONPATH`, and appends the `src/` ignore rule. Nothing is written until
every check has passed, so a stack that has drifted from the exemplar aborts
**intact** — it names the file and the anchor it could not find, rather than
leaving you half-converted.

It requires a clean git checkout, so the result is reviewable as a diff
(`--force` waives that). Verify with:

```bash
cd src/<package> && uv sync && uv run pytest
cd <stack> && uv sync && uv run zensical build
docker compose up -d backend
```

The rest of this page documents what the script does, step by step. Read it if
the script reported a drifted file and you need to reconcile that step by
hand — or if you would simply rather see the work.

## Doing it by hand

!!! tip "Changed your mind? Ordinary `git` cleanup hands it back to the script"
    The script converts a stack in one shot and refuses one that is already
    part-way there — a hand migration abandoned after step 1 reports `has no
    src/<package>/tools.py`, because the move it looks for has already
    happened. You do not have to finish by hand: every step below edits only
    tracked files, so the usual cleanup returns the stack to a state the
    script can take from the top.

    ```bash
    cd <stack>
    git reset --hard     # undo the moves and the edited files
    git clean -fd        # drop files the migration newly created
    ```

    **Leave `-x` / `-X` off.** Both would also delete *ignored* files, and in
    a generated stack that means `.env`, the `.secrets/*.gen` files (which
    Postgres has already been initialised against), the LanceDB under
    `rag/db/`, and every `.venv`.

    The flip side is that ignored files do not travel back: build artifacts
    such as `__pycache__/` or `.pytest_cache/` stay wherever the half-finished
    move left them, and the migration will carry them along into the new tree
    — harmless, but it leaves a stray `tests/` inside your package. Delete
    them before re-running:

    ```bash
    find src -name __pycache__ -type d -prune -exec rm -rf {} +
    git status --short   # must be empty before you re-run the script
    ```

    This assumes the hand migration is uncommitted. If you committed it,
    `git revert` or reset to the commit before it, then clean as above.

### 1. Move the files

Four `git mv`s, which git records as pure renames — history follows:

```bash
mkdir -p .mig/<package>
git mv src .mig/<package>
git mv tests .mig/<package>
git mv .mig src
```

Check it before going further:

```bash
git status --short     # every line should start with R
```

!!! warning "The intermediate needs the package-name level"
    `mkdir .mig && git mv .mig src` (without `/<package>`) lands the files at
    `src/src/<package>/` and `src/tests/`, which is not the target layout.
    This is the first of the two steps that are easy to get wrong by hand;
    the `pyproject.toml` split below is the other.

### 2. Split `pyproject.toml`

There are now **two** `pyproject.toml` files, and the split is not a pure move:
only the build backend and the package/test settings migrate. The stack root
keeps its `[project]` table, its dependency groups, and the
`[tool.soliplex-template]` manifest — it stops being a *distribution*, not a
project.

The stack root becomes exactly this (the `[tool.soliplex-template]` block
already there stays put, unchanged, at the end):

```toml
[project]
name = "<project_name>"
version = "0.1.0"
requires-python = ">=3.13"
# Host-side dependencies for driving *the stack*.
dependencies = [
    "soliplex >= 0.79, < 0.80",
    "psycopg[binary]",
    "asyncpg",
]

# Nothing at the stack root is importable, so there is nothing to build here.
[tool.uv]
package = false

[dependency-groups]
dev = [
    # Builds the documentation site under docs/ (`uv run zensical build`).
    "zensical",
]

[tool.soliplex-template]
# ... the generation manifest, left exactly as it was ...
```

!!! warning "Keep `[project]` and `[dependency-groups]`"
    Deleting them along with `[build-system]` leaves the root with nothing but
    `[tool.uv]` and the manifest. `uv sync` then warns `No requires-python
    value found in the workspace` and `uv run zensical build` fails with
    `Failed to spawn: zensical` — the docs site has no tooling environment to
    run in. Step 5 below catches this.

Then create `src/<package>/pyproject.toml` with the parts you removed:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "<project_name>"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["soliplex >= 0.79, < 0.80"]

[dependency-groups]
dev = ["pytest"]

[tool.hatch.build.targets.wheel]
packages = ["src/<package>"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]
```

Leave the `[tool.soliplex-template]` manifest at the stack root: it records how
the *stack* was generated, not how the library is built.

### 3. Repoint the backend's `PYTHONPATH`

The `./src` bind mount in `docker-compose.yml` stays as it is — it now carries
every project directory. Only the import root changes, from the mount point
itself to this project's package directory:

```yaml
      PYTHONPATH: /app/src/<package>/src
```

That is the point of the move: `/app/src` stops being a `sys.path` entry, so a
checkout sitting next to your project can no longer contribute stray portions
to a namespace package that shares its name.

Restart the backend to pick it up (the `--reload=config` flag covers config
edits, not the compose file):

```bash
docker compose up -d backend
```

### 4. Add the `src/` ignore rule

Replace any per-checkout ignore lines with the rule they were standing in for,
in the stack's `.gitignore`:

```gitignore
# Every entry under src/ is a project directory. This project's own
# package is tracked; anything else cloned there belongs to its own repo.
/src/*
!/src/<package>/
```

Add `.venv/` too if it is not already there — each project directory gets its
own.

### 5. Verify

The library, from its own project directory:

```bash
cd src/<package>
uv sync
uv run pytest
```

The stack root, which is the half the migration is easiest to get wrong —
both commands fail loudly if `[project]` or `[dependency-groups]` went
missing in step 2:

```bash
cd <stack>
uv sync                 # must not warn "No requires-python value found"
uv run zensical build   # must not fail with "Failed to spawn: zensical"
```

Then open the `custom` room and ask it to greet someone: that exercises the
dotted `<package>.tools.greeting` name through the new `PYTHONPATH`.

## Checking the whole stack against a reference

For more than a smoke test, generate a throwaway stack from this stack's own
generation manifest and diff the two. The manifest under
`[tool.soliplex-template.params]` records every parameter the stack was built
with, so the reference is the stack as the current generator would scaffold
it:

```bash
cd <stack>

# Build a params file from the manifest. The generator recomputes derived
# values, so they are not accepted as input -- drop them. (If it rejects
# others, its error message names them; add those to the list.)
python3 - <<'EOF' > /tmp/ref-params.json
import json, pathlib, tomllib

manifest = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
params = manifest["tool"]["soliplex-template"]["params"]
for derived in ("package_name", "frontend_release_path", "backend_auth_flag"):
    params.pop(derived, None)
print(json.dumps(params, indent=2))
EOF

# <skill> is wherever the soliplex-template skill is installed, e.g.
# ~/.claude/skills/soliplex-template
uv run <skill>/scripts/generate_soliplex_project.py \
    --out /tmp/ref-stack --params /tmp/ref-params.json \
    --no-git --no-generate-secrets

diff -u /tmp/ref-stack/pyproject.toml pyproject.toml
diff -ru /tmp/ref-stack/src src
```

Two differences are expected and harmless: `skill_source_commit` and
`skill_generated` in the manifest record *your* stack's generation, and the
reference's are blank or current. Anything else is a real divergence.

## Loose ends

The dotted names in `backend/environment/` do not change — only where the
package directory sits on disk does. Comments in `installation.yaml` and
`backend/environment/rooms/custom/room_config.yaml` still point at the old
paths; update them at your leisure.

Once converted, see [Custom Python
package](../users/custom-package.md#adding-a-second-project) for what to do
when you clone another repo into `src/`.
