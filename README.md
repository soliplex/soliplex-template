# Soliplex Docker Compose Template

A Docker Compose template that assembles a full Soliplex stack — a self-hosted
RAG / AI system — from a backend, a Flutter web frontend, a document-ingest
pipeline, and Postgres. This repository is configuration and Dockerfiles, not
application source: the backend and the frontend are both built on published
release images.

You don't run this repository directly. You generate your **own** stack from it
with the bundled `soliplex-template` Agent Skill (or the generator script the
skill drives), then run and operate that generated project.

📖 **Full documentation:** <https://soliplex.github.io/soliplex-template/>

## Prerequisites

- A coding agent that supports filesystem [Agent Skills](https://agentskills.io/).
- On the machine the agent drives: Docker (with the Compose plugin),
  [`uv`](https://docs.astral.sh/uv/), and an Ollama server reachable from the
  containers that serves the models the stack will reference.

An Ollama running on the same machine as Docker isn't reachable at
`localhost`: inside a container, `localhost` is the container itself. Use
`http://host.docker.internal:11434` instead, which Docker Desktop provides. On
Linux, see [Local Ollama](https://soliplex.github.io/soliplex-template/users/operations/ollama/)
for the options.

## Install the skills

Install two skills into your agent. Both are listed at
<https://agentskills.io/>; each is also published as a rolling `latest` release
you can install from directly:

- [`soliplex-template`](https://github.com/soliplex/soliplex-template/releases/tag/template-skill-latest)
  — generates and configures a stack (this template).
- [`soliplex-docs`](https://github.com/soliplex/soliplex/releases/tag/docs-latest)
  — Soliplex's documentation, so the agent can answer setup and usage questions
  as you go.

Each release carries the skill as a `.tar.gz` and a `.zip`; unpack it into your
agent's skills directory (see your agent's documentation for where that is).

## Generate a stack

Ask the agent to generate one — e.g. *"generate a new Soliplex stack"*. The
`soliplex-template` skill asks for what it needs (at minimum your Ollama URL),
checks that your models load, scaffolds a fresh project directory, and
generates its secrets. To skip its questions, put the answers on the command:

```text
/soliplex-template in=~/stacks project=acme ollama=http://192.168.1.20:11434 ports=default models=default
```

To drive the generator by hand instead, run it from the installed skill, with
`<skills>` standing for your agent's skills directory:

```bash
uv run <skills>/soliplex-template/scripts/generate_soliplex_project.py \
    --out ../my-stack --interactive
```

See [Generate a project](https://soliplex.github.io/soliplex-template/getting-started/generator/)
for every parameter and what the skill checks before writing anything.

## Bring it up

From the generated project directory, prepare the databases, then start the
stack:

```bash
docker compose run --rm --quiet-build stack-init
docker compose up
```

The first run builds the images and initializes Postgres, so it takes a few
minutes. Once it settles, the web frontend is at <http://localhost:9000> (the
default port). The [First steps](https://soliplex.github.io/soliplex-template/getting-started/tutorials/01-first-steps/)
tutorial continues from here.

## What's in a generated stack

- nginx — serves the web frontend and reverse-proxies the API.
- backend — the Soliplex server (`soliplex-cli serve`).
- soliplex-provisioner — creates rooms on a requester's behalf.
- haiku-ingester — the document pipeline that feeds the RAG vector store.
- docling-serve — converts uploaded documents for ingestion.
- postgres — thread persistence, authorization, and the ingester job queue.

Two more services are optional, off by default and chosen at generation time:
a web TUI (the terminal client served as a web app, proxied at `/tui/`) and a
local Gitea (proxied at `/gitea/`). So is a code-execution sandbox for the
backend's agents.

## Documentation

Full documentation — tutorials, exposed ports, architecture, secrets, the RAG
pipeline, configuration, and upgrading an existing stack — lives at
<https://soliplex.github.io/soliplex-template/> (sources under `docs/`, built
with [Zensical](https://zensical.org)). Working on this repository itself? See
[Developing this template](https://soliplex.github.io/soliplex-template/contributing/).

Preview or build the docs locally:

```bash
uv run zensical serve     # preview at http://localhost:8000
uv run zensical build     # static site under site/
```
