# Generator parameters

All parameters are supplied via a JSON file (`--params`) and/or `--interactive`.
Anything omitted falls back to the default below. `ollama_base_url` is the only
value with no usable default — it must be provided.

Run `uv run scripts/generate_soliplex_project.py --print-defaults` to dump the current defaults
as JSON.

## CLI arguments (not template parameters)

| Argument | Meaning |
|----------|---------|
| `--out DIR` | Target directory for the new project (required to generate). |
| `--params FILE` | JSON object of parameter overrides. |
| `--interactive` | Prompt for each parameter on stdin (blank keeps the default). |
| `--force` | Allow writing into a non-empty `--out`. |
| `--generate-secrets` / `--no-generate-secrets` | Run `scripts/generate-secrets.sh` in the new project after scaffolding. **Default: enabled**; pass `--no-generate-secrets` to skip. |
| `--no-git` | Skip `git init` / initial commit. |
| `--disable-gpg-sign` | Pass `commit.gpgsign=false` for the initial commit. Default: respect the host git config. |
| `--print-defaults` | Print default parameters as JSON and exit. |

## Parameters

| Parameter | Default | Notes / validation | Where it lands |
|-----------|---------|--------------------|----------------|
| `project_name` | `soliplex` | derived `package_name` must be a valid Python identifier | compose `name:`, `pyproject.toml` `[project] name`, `README.md` |
| `setup_id` | `<project_name>-conf` | derived if unset | `installation.yaml` `id:` |
| `nginx_http` | `9000` | int 1–65535, unique among host ports | compose host port, `README.md` |
| `nginx_https` | `9443` | int, unique | compose host port, TUI public-url, `README.md` |
| `ingester_port` | `8765` | int, unique | compose host port |
| `docling_port` | `5001` | int, unique | compose host port |
| `postgres_port` | `5432` | int, unique | compose host port |
| `server_name` | `localhost` | — | `nginx.conf` `server_name`, TUI public-url, derived `tls_subject` |
| `tls_subject` | `/C=US/ST=State/L=City/O=Soliplex/CN=<server_name>` | derived if unset | `nginx/Dockerfile` self-signed cert subject |
| `ollama_base_url` | *(required)* | non-empty | `.env` |
| `chat_model` | `gpt-oss:latest` | — | `installation.yaml` `default_chat` |
| `chat_model_alt` | `gpt-oss:20b` | — | `installation.yaml` `alternate_chat` |
| `title_model` | `gpt-oss:latest` | — | `installation.yaml` `title` |
| `rag_embed_model` | `qwen3-embedding:4b` | — | `backend/environment/haiku.rag.yaml` |
| `rag_embed_dim` | `2560` | int; must match the embedding model | `backend/environment/haiku.rag.yaml` |
| `rag_qa_model` | `gpt-oss:latest` | — | `backend/environment/haiku.rag.yaml` |
| `rag_research_model` | `gpt-oss:latest` | — | `backend/environment/haiku.rag.yaml` |
| `chunk_size` | `256` | int | both `haiku.rag.yaml` files |
| `agui_db` | `soliplex_agui` | SQL identifier; ≠ `authz_db` | `installation.yaml` DB URIs, `postgres/config/init.sh` (role **and** database) |
| `authz_db` | `soliplex_authz` | SQL identifier; ≠ `agui_db` | `installation.yaml` DB URIs, `postgres/config/init.sh` |
| `soliplex_backend_constraint` | `>= 0.68, < 0.69` | non-empty | `backend/constraints.txt`, `pyproject.toml` |
| `soliplex_tui_constraint` | `>= 0.60.6, < 0.61` | non-empty | `tui/constraints.txt` |
| `frontend_version` | `latest` | `latest` or a release tag (letters, digits, `.`, `_`, `-`) | `nginx/Dockerfile` frontend release fetched at image build |
| `auth_mode` | `no-auth` | `no-auth` or `auth` | backend `command` (`--no-auth-mode` present/absent) |
| `docs_dir` | `rag/docs` | relative path inside the project | compose ingester bind mount; created at generation time |
| `ingester_token` | `secret` | weak default — override for real deployments | `.env` `INGESTER_TOKEN` |

## Derived values

- `setup_id` ← `<project_name>-conf` when not supplied.
- `tls_subject` ← `/C=US/ST=State/L=City/O=Soliplex/CN=<server_name>` when not supplied.
- `backend_auth_flag` ← `--no-auth-mode` plus a trailing space when `auth_mode == "no-auth"`, else empty
  (consumed by `docker-compose.yml.mako`).
- `frontend_release_path` ← `latest` when `frontend_version == "latest"`, else `tags/<frontend_version>`.
  Selects the GitHub releases API endpoint in `nginx/Dockerfile`
  (`/releases/latest` vs `/releases/tags/<tag>`). Default `latest` keeps the
  historical "newest release" behavior; pin a tag for reproducible builds.
- `package_name` ← `project_name` lower-cased with hyphens turned into underscores.
  This is the **import name** of the generated `src/<package_name>/` package, so it
  must be a valid Python identifier (and not a keyword); generation fails otherwise.
  It lands in: the package path `src/<package_name>/`, `pyproject.toml`
  (`[tool.hatch.build.targets.wheel]`), the backend `PYTHONPATH` bind mount in
  `docker-compose.yml`, and the dotted names referenced from `installation.yaml`
  (`app_router_operations`, commented `meta:` examples) and
  `rooms/custom/room_config.yaml` (`tool_name`).

## The generated project as an installable library

The scaffolded project ships a `src/<package_name>/` package (`tools.py`,
`views.py`) and a `tests/unit/` tree, and its `pyproject.toml` declares a
build backend (`hatchling`) plus a `dev` dependency group, so it is
`uv sync` / `uv pip install -e .`-able. The backend reads the package over a
read-only `./src` bind mount on `PYTHONPATH` — no image rebuild needed to edit
the custom code. The bundled `tools.greeting` tool and `views.router` FastAPI
router are referenced by dotted name from the Soliplex config (which is why
those config files are Mako templates).

## The generated project's documentation site

The scaffolded project also ships its own [Zensical](https://zensical.org) docs
site under `docs/` (with a `zensical.toml`), paralleling this template repo's
own site but with the project's parameters substituted in (name, ports,
`package_name`, …). `zensical` is added to the project's `dev` dependency group,
so the owner can build or preview it locally:

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/ (gitignored)
```

No publishing workflow is generated (the owner's eventual repository URL is
unknown at generation time).

## Notes

- Container-internal ports (backend `8000`, TUI `8002`, and the internal nginx
  `9000`/`9443`) are fixed; the port parameters set only the **host-published**
  side of each mapping.
- `agui_db` / `authz_db` are used as both the Postgres role name and the
  database name (the template uses identical names for each).
- The backend `soliplex` version pin (`soliplex_backend_constraint`) lands in
  `backend/constraints.txt`. Choose it from the published releases on PyPI
  (`https://pypi.org/pypi/soliplex/json`) rather than guessing — pin one
  release (e.g. `== 0.68.3`) or keep a range (e.g. `>= 0.68, < 0.69`); the
  skill's interview step (see `SKILL.md`) lists them for you.
