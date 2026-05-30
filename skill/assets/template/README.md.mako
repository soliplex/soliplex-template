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
