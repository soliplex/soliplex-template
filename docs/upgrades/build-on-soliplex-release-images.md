# Build on the soliplex release images

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.20-a2c13ae` skill release** — its generator produces the rung's end state (the 0.85 *release-image* shape).  The stack being upgraded names the release that generated it in its own `[tool.soliplex-template]` manifest — use that
  one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.20-a2c13ae
```

From here the backend no longer resolves soliplex from PyPI: it builds on the
published `ghcr.io/soliplex/soliplex-postgres` image, which already holds
soliplex and its PostgreSQL driver. See
[Backend image & dependencies](../users/architecture/backend.md) for how the
result works.

Stop the stack first (`docker compose down`). The backend reloads
`backend/environment/` as soon as a file there changes, so a running stack
would pick up step 5's edit on the old image.

1. **Note your extras.** The old `backend/Dockerfile` installs packages with
   `uv add`. Anything on that line other than `soliplex`, `psycopg[binary]`,
   `asyncpg`, `textual`, `textual-fspicker` and `textual-serve` is yours,
   and so is any pin in `backend/constraints.txt` other than `soliplex`'s.
   Your own edits to the two files show in their history from generation
   to the `pre-upgrade` tag, which leaves out section 1's changes:

    ```bash
    git diff $(git rev-list --max-parents=0 HEAD) pre-upgrade \
        -- backend/Dockerfile backend/constraints.txt
    ```

    That takes the stack's first commit as "as generated", which holds when
    the generator created the repository (its default). For a stack
    generated with `--no-git`, or whose history was rewritten, compare
    against a reference stack instead (see the tip at the top).

    The `textual` packages go: the backend image no longer carries the
    TUI's dependencies, so a TUI run inside the backend container needs the
    TUI service instead (`include_tui`).

2. **Create `backend/requirements.txt`** with your extras, one requirement
   per line: an `httpx` on the `uv add` line with an `httpx >= 0.28` pin in
   `constraints.txt` becomes the single line `httpx >= 0.28`. Drop pins on
   packages the soliplex release already holds: the build freezes the
   image's own versions as constraints, so a pin that disagrees fails the
   build rather than moving them. With no extras, the file holds only its
   header:

    ```text
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/backend/requirements.txt"
    ```

3. **Replace `backend/Dockerfile`** with the release-image one below. Carry
   over any local edits from step 1 (e.g. a `COPY` of `vendor/`).

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/backend/Dockerfile"
    ```

4. **Delete `backend/constraints.txt`**.

5. **Switch the async driver** in `backend/environment/installation.yaml`:
   both `async_dburi` values change `postgresql+asyncpg://` to
   `postgresql+psycopg://`. The image ships psycopg, not asyncpg. Leave
   `haiku.rag/haiku.rag.yaml` alone: the haiku.rag image has its own driver,
   as the comment above its `dburi` says.

6. **Replace the backend's healthcheck test** in `docker-compose.yml`. The
   image has no `curl`, so the `test:` entry under the backend's
   `healthcheck:` uses the venv's Python instead; keep its `interval`,
   `timeout` and `retries`:

    ```yaml
        healthcheck:
          # The soliplex image has no curl; use the venv's stdlib instead.
          test:
            - "CMD"
            - "python"
            - "-c"
            - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/ok', timeout=5).status == 200 else 1)"
          interval: 30s
          timeout: 10s
          retries: 4
    ```

    It takes effect when step 13 recreates the container.

7. **The TUI**, if `tui/Dockerfile` exists: replace it with the one below,
   built `FROM ghcr.io/soliplex/soliplex-tui` at the same tag as the
   backend, and delete `tui/constraints.txt`:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/tui/Dockerfile"
    ```

8. **Update the stack's host-side dependencies** in the root
   `pyproject.toml`. The `soliplex`, `psycopg[binary]` and `asyncpg` entries
   become one:

    ```toml
    dependencies = [
        "soliplex[postgres-binary] >= 0.85, < 0.86",
    ]
    ```

    then refresh the lock and the environment (`uv lock && uv sync`).
    Section 1 already moved the lock to 0.85, so the diff is small, mostly
    `asyncpg` dropping out. `src/<package>/pyproject.toml` already admits
    0.85 (section 1, step 1).

9. **Update the manifest's parameters** in the
   `[tool.soliplex-template.params]` table. The version parameters are
   renamed, `soliplex_backend_constraint` and `soliplex_tui_constraint`
   giving way to:

    ```toml
    soliplex_image_tag = "0.85"
    soliplex_dev_constraint = ">= 0.85, < 0.86"
    ```

    and, if section 1 did not already set them, `haiku_rag_version =
    "0.96.1"` and `enable_sandbox` (section 1, steps 3 and 7). Finally, so
    the manifest names the template the stack now matches, set its
    `skill_source_commit` and `skill_generated` to the upgraded skill's own
    `source_commit` and `generated`, from the `metadata:` at the top of its
    `SKILL.md`. Only a built release carries that block: the skill as it
    sits in the template repository has none. A skill built from a checkout
    with `scripts/build_skill.py` gets one, stamped with the checkout's
    `HEAD`, which does not cover changes not yet committed there.

10. **Watch your own package.** A stack generated before the backend
    watched its package carries no `--reload-dirs`, so an edit under
    `src/<package>/` needs a restart. Have it watched, as a newly generated
    stack's is:

    ```bash
    uv run <skill>/scripts/src_projects.py add <package>
    ```

    Its `PYTHONPATH` entry is already there (`unchanged`); `add` adds the
    matching `--reload-dirs=/app/src/<package>/src` to the serve command.
    Ignore its advice to bring the backend up: step 13 does that.

11. **Give `soliplex-dev` a build of its own** (stacks whose
    `docker-compose.yml` has a `soliplex-dev` service). It now builds on the
    backend's image plus every `src/` project's `dev` dependency group, so
    a project's tests run there against the backend's versions, without the
    backend image carrying them. Make a `soliplex-dev/` directory, save the
    template's Dockerfile into it as `soliplex-dev/Dockerfile`:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/soliplex-dev/Dockerfile"
    ```

    then generate its requirements file from the projects' dev groups:

    ```bash
    uv run <skill>/scripts/src_projects.py dev-requirements
    ```

    and point the service's `build:` at it, in place of its
    `context: backend`, `dockerfile: Dockerfile` and `args:`:

    ```yaml
        build:
          context: soliplex-dev
          additional_contexts:
            backend: "service:backend"
    ```

    Commit both files. Step 13 builds it.

12. **Build nginx on the published web client.** Instead of building the
    Flutter client from a release tarball, `nginx/Dockerfile` now copies it
    from the `ghcr.io/soliplex/frontend-web` image, already cache-busted. Replace the
    Dockerfile with the template's, keeping your own `-subj "…"` certificate
    subject on its `openssl req` line:

    ```dockerfile
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/nginx/Dockerfile"
    ```

    delete `nginx/post-build-cache-bust.sh`, which it no longer runs, and in
    `nginx/nginx.conf` replace the hashed-asset locations — in **both**
    `server` blocks, the HTTP one and the HTTPS one — with the one below. The
    image names its asset directory for the release tag, build metadata
    included (`v0.110.0+96/`), which the old pattern does not match, so
    without this change the assets lose their long-lived caching:

    ```nginx
    --8<-- "docs/getting-started/includes/template-skill-2026.10.20-a2c13ae/nginx/nginx.conf:hashed-assets"
    ```

    Then, in the manifest, set `frontend_version` to the image tag (`0.110`
    follows the 0.110 series) and drop `frontend_release_path`, which nothing
    reads any more.

13. **Tag the current backend image, then rebuild with `--pull`**, so Docker
    fetches the release image, and bring the stack up:

    ```bash
    docker tag <project>-backend:latest <project>-backend:pre-release-image
    docker compose build --pull backend nginx
    docker compose up -d
    ```

    Tag and rebuild `tui` the same way if the stack has it, and add
    `soliplex-dev` to the build if the stack has that (it builds on the
    backend's image). Then [**Check it**](index.md#check-it), and commit.
