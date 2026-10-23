---
icon: lucide/box
---

# Backend image & dependencies

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The `backend` service image is built from `backend/Dockerfile`, on top of the
published soliplex release image:

```dockerfile
FROM ghcr.io/soliplex/soliplex-postgres:0.86.1
```

That image already holds soliplex and its PostgreSQL driver (`psycopg`, over
the system `libpq5`) in `/app/.venv`. The Dockerfile adds only what is
particular to this stack:

- `bwrap` (`bubblewrap`), for the `bubble-sandbox` skill;
- the extra PyPI packages listed in `backend/requirements.txt`;
- the sandbox environments under `backend/sandbox/environments/`;
- the service user, from `PUID` / `PGID` in `.env`.

The image carries no `uv`, `curl`, `git`, or compiler. `uv` is mounted only
into the build steps that need it, and never lands in the image.

Because everything is baked in at build time, changing any of it requires a
rebuild:

```bash
docker compose build backend
docker compose up -d backend
```

The dev-mode `soliplex-dev` service builds on this image, adding the `src/`
projects' dev dependencies, so rebuild it too if you use it
(`docker compose build backend soliplex-dev`).

## Choosing the soliplex release

The tag on the `FROM` line picks the release:

| Tag | Meaning |
|---|---|
| `0.85` | moving: the newest `0.85.x` patch release |
| `0.85.3` | exact: that one release |
| `latest` | moving: the newest release of any series |

Docker keeps using its local copy of a moving tag until it is told to look
again, so pass `--pull` to pick up a new patch release:

```bash
docker compose build --pull backend
docker compose up -d backend
```

To see which tags are published:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:soliplex/soliplex-postgres:pull" | jq -r .token)
TAGS=https://ghcr.io/v2/soliplex/soliplex-postgres/tags/list
curl -s -H "Authorization: Bearer $TOKEN" "$TAGS" | jq -r '.tags[]' | sort -V
```

Moving to a new series (say `0.85` to `0.86`) is an upgrade, not a rebuild:

- check the release notes, and [Database migrations](../operations/migrations.md)
  for the databases and RAG stores it may need to migrate;
- raise the `soliplex` requirement in `pyproject.toml` and
  `src/<package>/pyproject.toml` to match, so host-side work runs the same
  release the backend does;
- check the `haiku.rag-slim` tag in `haiku.rag/Dockerfile` and
  `docker-compose.yml` against the `haiku.rag-slim` range the new release
  requires: the ingester writes the LanceDB the backend reads.

<!-- if:tui -->
The TUI's `tui/Dockerfile` builds on `ghcr.io/soliplex/soliplex-tui` at the
same tag. Change the two together, and rebuild `tui` as well.
<!-- endif -->

## Adding a third-party dependency (from PyPI)

1. Add one requirement per line to `backend/requirements.txt`:

    ```text
    httpx-retries >= 0.4
    ```

2. Rebuild and restart the service (see above).

The build freezes every package the soliplex image already holds as a
constraint, and resolves the new requirements around them. An extra can add
packages, but one that needs a *different* version of a package the soliplex
release pinned fails the build with a resolver error rather than quietly
moving it. Pick a compatible version of the extra, or a soliplex release whose
pins suit it.

## Adding a local Python dependency

"Local" means a package whose source lives on your machine. The build context
of the `backend` image is the `backend/` directory, so anything the Dockerfile
`COPY`s must live **inside** `backend/`. Place the source under
`backend/vendor/<pkgname>/`, copy it in just before the `requirements.txt`
install step in `backend/Dockerfile`:

```dockerfile
COPY --link vendor /vendor
```

and list it in `backend/requirements.txt` by path (`/vendor/<pkgname>`). Its
own dependencies are resolved against the same frozen constraints as any
other extra.

!!! tip "Your own code does not need this"
    For code that belongs to *this* project, use its own project directory
    under `src/` instead — it is bind-mounted onto the backend's `PYTHONPATH`
    with no rebuild. See [Custom Python package](../custom-package.md).

## Gotchas

- While `backend/requirements.txt` holds only comments and blank lines, the
  install step is skipped entirely.
- The install layer is cached on `requirements.txt` and the soliplex image it
  builds on. A `--pull` that fetches a newer image re-runs it, against that
  release's versions, so an extra that fit the old release can fail on the
  new one.
- Sandbox environments under `backend/sandbox/environments/<name>/` are
  **separate** `uv` projects, with their own locked dependencies; a package
  the sandboxed code needs belongs there, not in `requirements.txt`.
