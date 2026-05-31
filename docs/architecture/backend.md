---
icon: lucide/box
---

# Backend image & dependencies

The `backend` service image is built from `backend/Dockerfile`. It uses
[`uv`](https://docs.astral.sh/uv/) to create a project at `/app` and install the
pinned `soliplex` release plus a handful of third-party runtime dependencies
(`psycopg[binary]`, `asyncpg`, `textual`, …) into `/app/.venv`, subject to the
pins in `backend/constraints.txt`. The `soliplex-cli serve` entrypoint runs out
of that venv.

Because dependencies are baked in at image-build time, adding or changing a
dependency requires a rebuild:

```bash
docker compose build backend
docker compose up -d backend
```

## Adding a third-party dependency (from PyPI)

1. Edit the `uv add` invocation in `backend/Dockerfile` and append the new
   distribution name(s). For example, to add `httpx` and `pydantic-settings`:

    ```dockerfile
    RUN \
        uv init --bare --name "soliplex-template" --no-description && \
        uv add \
          --constraints constraints.txt \
          soliplex \
          psycopg[binary] \
          asyncpg \
          textual \
          textual-fspicker \
          textual-serve \
          httpx \
          pydantic-settings && \
        uv sync
    ```

2. (Optional but recommended) pin the version in `backend/constraints.txt` so
   builds are reproducible:

    ```text
    soliplex >= 0.68, < 0.69
    httpx >= 0.28, < 0.29
    pydantic-settings >= 2.7, < 3
    ```

    Any constraint listed here is honored by the
    `uv add --constraints constraints.txt` step.

3. Rebuild and restart the service (see above).

## Adding a local Python dependency

"Local" means a package whose source lives on your machine rather than on PyPI.
The build context of the `backend` image is the `backend/` directory, so
anything the Dockerfile `COPY`s from the host must live **inside** `backend/`.
The simplest pattern is to place the source under `backend/vendor/<pkgname>/`
and install it as a path dependency:

```text
backend/
  Dockerfile
  constraints.txt
  pyproject.toml
  vendor/
    my_local_pkg/
      pyproject.toml
      src/my_local_pkg/__init__.py
```

In `backend/Dockerfile`, `COPY` the package into the image **before** the
`uv add` step and add it as a path dependency:

```dockerfile
COPY --link constraints.txt constraints.txt
COPY --link vendor /vendor

RUN \
    uv init --bare --name "soliplex-template" --no-description && \
    uv add \
      --constraints constraints.txt \
      soliplex \
      psycopg[binary] \
      asyncpg \
      textual \
      textual-fspicker \
      textual-serve \
      /vendor/my_local_pkg && \
    uv sync
```

Use `uv add --editable /vendor/my_local_pkg` if you want editable installs —
though edits under `backend/vendor/` still require a rebuild, since `vendor/` is
not bind-mounted into the running container.

!!! tip "Iterating on a local package without a full rebuild"
    Bind-mount the package's source into the container at the path where `uv`
    installed it, and install it editable at build time. Add a `volumes` entry
    under the `backend` service in `docker-compose.yml`:

    ```yaml
    volumes:
      # ... existing bind mounts ...
      - type: bind
        source: "backend/vendor/my_local_pkg"
        target: "/vendor/my_local_pkg"
    ```

    With `uv add --editable /vendor/my_local_pkg` in the Dockerfile, the venv's
    `.pth` file points at `/vendor/my_local_pkg/src`, so host-side edits show up
    in the container. Restart the backend (`docker compose restart backend`) to
    pick up edits that require a Python process restart.

## Gotchas

- The build cache is keyed on `constraints.txt`, `Dockerfile`, and
  `sandbox/environments/*/pyproject.toml`. Editing `constraints.txt` or the
  `uv add` line invalidates the dependency-install layer and forces a
  re-resolve — expect the rebuild to take noticeably longer than a no-op build.
- Sandbox environments under `backend/sandbox/environments/<name>/` are
  **separate** `uv` projects with their own `pyproject.toml` and `uv.lock`.
  Dependencies needed by agent-executed sandbox code belong there, not in the
  top-level `uv add` list (see [Backend configuration](configuration.md)).
- `constraints.txt` only affects PyPI resolution. Local path dependencies are
  not version-constrained by it.
