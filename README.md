# Soliplex Docker Compose Template

A starting point for running Soliplex and related services under
Docker Compose.

## Quickstart

```bash
git clone https://github.com/soliplex/soliplex-template.git
cd soliplex-template
uv run scripts/generate_secrets.py   # populates .secrets/*.gen (gitignored)
echo 'OLLAMA_BASE_URL=http://your-ollama-host:11434' > .env
docker compose up
```

Then open <http://localhost:9000>. The terminal client (TUI) is bundled in the
backend image — run it against the stack with
`docker compose exec backend soliplex-tui --url http://localhost:8000`. This
template also serves the TUI as a web app (the optional `tui` service) at
<https://localhost:9443/tui/>.

`OLLAMA_BASE_URL` must point at an Ollama server that serves the models
referenced in `backend/environment/installation.yaml`. The first `up` builds
images and initializes Postgres, so it takes a few minutes.

## Documentation

Full documentation — prerequisites, exposed ports, architecture, secrets, the
RAG pipeline, configuration, and generating a customized project — lives at
<https://soliplex.github.io/soliplex-template/> (sources under `docs/`, built
with [Zensical](https://zensical.org)).

Build the docs locally with:

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/
```
