# ${project_name}

A Soliplex Docker Compose stack (nginx + Soliplex backend + Flutter frontend +
haiku-ingester + Postgres, plus docling-serve and a TUI), scaffolded from the
`soliplex-template` project generator.

## First-time setup

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
```

Set `OLLAMA_BASE_URL` in `.env` if it is not already correct.

## Run the stack

```bash
docker compose up            # foreground
docker compose up -d         # detached
docker compose logs -f backend
docker compose down          # stop (keeps the postgres_data volume)
docker compose down -v       # stop AND wipe the postgres volume
```

## Ports

| Service          | Host port |
|------------------|-----------|
| nginx (HTTP)     | ${nginx_http} |
| nginx (HTTPS)    | ${nginx_https} |
| haiku-ingester   | ${ingester_port} |
| docling-serve    | ${docling_port} |
| postgres         | ${postgres_port} |

Open the app at <http://localhost:${nginx_http}/> (or
<https://${server_name}:${nginx_https}/> for TLS).

## Custom Python package

This project is also an installable Python library: your own code lives under
`src/${package_name}/` and its tests under `tests/unit/`.

```bash
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
uv pip install -e .     # or a plain editable install into another environment
```

The Soliplex backend bind-mounts `./src` into its container and puts it on
`PYTHONPATH` (see `docker-compose.yml`), so anything you define here is
importable by **dotted name** from the Soliplex config under
`backend/environment/`. Two examples ship wired up:

- a tool, `${package_name}.tools.greeting`, referenced from
  `backend/environment/rooms/custom/room_config.yaml`;
- a FastAPI router, `${package_name}.views.router`, registered via
  `app_router_operations` in `backend/environment/installation.yaml`.

Dotted names into this package can equally be used in the `installation.yaml`
`meta:` section (tool/agent/skill config classes, MCP wrappers, secret
sources) — see the commented `${package_name}.*` examples there.
