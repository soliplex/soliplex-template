# ${project_name}

A Soliplex Docker Compose stack (nginx + Soliplex backend + Flutter frontend +
haiku-ingester + Postgres, plus docling-serve and a TUI), scaffolded from the
`soliplex-template` project generator.

<%text>## First-time setup</%text>

```bash
./scripts/generate-secrets.sh   # populates .secrets/*.gen (gitignored)
```

Set `OLLAMA_BASE_URL` in `.env` if it is not already correct.

<%text>## Run the stack</%text>

```bash
docker compose up            # foreground
docker compose up -d         # detached
docker compose logs -f backend
docker compose down          # stop (keeps the postgres_data volume)
docker compose down -v       # stop AND wipe the postgres volume
```

<%text>## Ports</%text>

| Service          | Host port |
|------------------|-----------|
| nginx (HTTP)     | ${nginx_http} |
| nginx (HTTPS)    | ${nginx_https} |
| haiku-ingester   | ${ingester_port} |
| docling-serve    | ${docling_port} |
| postgres         | ${postgres_port} |

Open the app at <http://localhost:${nginx_http}/> (or
<https://${server_name}:${nginx_https}/> for TLS).

<%text>## Custom Python package</%text>

This project is also an installable Python library: your own code lives under
`src/${package_name}/` (a demo tool and FastAPI router ship wired up) and its
tests under `tests/unit/`.

```bash
uv sync                 # create/refresh the dev environment (installs pytest)
uv run pytest           # run the project's tests
```

See [Custom Python package](docs/custom-package.md) for how the package is put
on the backend's import path and referenced by dotted name from the Soliplex
config.

<%text>## Documentation</%text>

Full documentation for this project lives under `docs/`, built with
[Zensical](https://zensical.org):

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/
```
