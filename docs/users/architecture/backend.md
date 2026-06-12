---
icon: lucide/box
---

# Backend image & dependencies

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The `backend` service image is built from `backend/Dockerfile`. It uses
[`uv`](https://docs.astral.sh/uv/) to install the pinned `soliplex` release and
a handful of runtime dependencies into `/app/.venv`, subject to the pins in
`backend/constraints.txt`. Because dependencies are baked in at build time,
adding or changing one requires a rebuild:

```bash
docker compose build backend
docker compose up -d backend
```

## Adding a third-party dependency (from PyPI)

1. Append the distribution name(s) to the `uv add` invocation in
   `backend/Dockerfile`.
2. (Recommended) pin the version in `backend/constraints.txt` so builds are
   reproducible:

    ```text
    soliplex >= 0.68, < 0.69
    httpx >= 0.28, < 0.29
    ```

3. Rebuild and restart the service (see above).

## Adding a local Python dependency

"Local" means a package whose source lives on your machine. The build context
of the `backend` image is the `backend/` directory, so anything the Dockerfile
`COPY`s must live **inside** `backend/`. Place the source under
`backend/vendor/<pkgname>/` and add it as a path dependency in the `uv add`
step (`uv add /vendor/<pkgname>`, or `--editable` for editable installs).

!!! tip "Your own code does not need this"
    For code that belongs to *this* project, use the bundled `src/` package
    instead — it is bind-mounted onto the backend's `PYTHONPATH` with no
    rebuild. See [Custom Python package](../custom-package.md).

## Gotchas

- The build cache is keyed on `constraints.txt`, `Dockerfile`, and
  `sandbox/environments/*/pyproject.toml`. Editing `constraints.txt` or the
  `uv add` line forces a dependency re-resolve.
- Sandbox environments under `backend/sandbox/environments/<name>/` are
  **separate** `uv` projects; their dependencies belong there, not in the
  top-level `uv add` list.
- `constraints.txt` only affects PyPI resolution; local path dependencies are
  not version-constrained by it.
