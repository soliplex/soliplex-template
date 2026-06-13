# Soliplex Docker Compose Template

A Docker Compose template that assembles a full Soliplex stack — a self-hosted
RAG / AI system — from a backend, a Flutter web frontend, a document-ingest
pipeline, and Postgres. This repository is configuration and Dockerfiles, not
application source: the backend is installed from the published `soliplex`
package and the frontend from a release build.

There are two ways to use it: run this repository directly as a reference stack
to try Soliplex now, or generate your own tailored stack from it (for a real
deployment). Both are below.

## Try it

You need Docker (with the Compose plugin), [`uv`](https://docs.astral.sh/uv/),
and an Ollama server the containers can reach.

```bash
git clone https://github.com/soliplex/soliplex-template.git
cd soliplex-template
uv run scripts/generate_secrets.py    # writes .secrets/*.gen (gitignored)
echo 'OLLAMA_BASE_URL=http://your-ollama-host:11434' > .env
docker compose up                     # first run builds images; takes a few minutes
```

`OLLAMA_BASE_URL` must point at an Ollama server that serves the models named in
`backend/environment/installation.yaml`.

Once it is up, open the web frontend at <http://localhost:9000>. The terminal
client (TUI) is bundled in the backend image, so you can run it against the
stack without installing anything:

```bash
docker compose exec backend soliplex-tui --url http://localhost:8000
```

## Generate your own stack

For a real deployment, scaffold a tailored project — your own project name, host
ports, model choices, auth mode, and an installable Python package wired into
the backend — instead of running this repository as-is. Use the bundled
`soliplex-template` Agent Skill (ask an agent to generate a stack), or run the
generator script directly:

```bash
uv run skills/soliplex-template/scripts/generate_soliplex_project.py \
    --out ../my-stack --interactive
```

See the "Generate a project" guide in the documentation for the full parameter
list and the skill workflow.

## What's in the stack

- nginx — serves the web frontend and reverse-proxies the API.
- backend — the Soliplex server (`soliplex-cli serve`).
- haiku-ingester — the document pipeline that feeds the RAG vector store.
- docling-serve — converts uploaded documents for ingestion.
- postgres — thread persistence, authorization, and the ingester job queue.

Two more services are optional when you generate a project (and both run in this
reference stack): a web TUI (the terminal client served as a web app, proxied at
`/tui/`) and a local Gitea (proxied at `/gitea/`).

## Documentation

Full documentation — prerequisites, exposed ports, architecture, secrets, the
RAG pipeline, configuration, and generating a customized project — lives at
<https://soliplex.github.io/soliplex-template/> (sources under `docs/`, built
with [Zensical](https://zensical.org)).

Preview or build it locally:

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/
```
