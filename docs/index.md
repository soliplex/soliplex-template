---
icon: lucide/rocket
---

# Soliplex Docker Compose Template

A Docker Compose template that assembles a running **Soliplex** stack:

- **nginx** — serves the Flutter web frontend and reverse-proxies the API.
- **backend** — the Soliplex server (`soliplex-cli serve`).
- **haiku-ingester** — the document pipeline writer for the RAG vector store.
- **docling-serve** — stateless document converter.
- **postgres** — thread persistence and authorization policy.

This repository is **configuration + Dockerfiles**, not application source: the
Soliplex backend is installed from the published `soliplex` Python package, the
frontend is fetched from a `soliplex/frontend` GitHub release at image-build
time, and `haiku-rag` / `docling-serve` use upstream images.

You don't run this repository directly. You generate your **own** stack from it
with the bundled `soliplex-template` Agent Skill (or the generator script), then
run and operate that generated project.

## Getting started

| Page | What's there |
|------|--------------|
| [Generate a project](getting-started/generator.md) | Scaffold a tailored stack with the skill, or by running the generator directly. |

## Tutorials

Hands-on walkthroughs that take a freshly generated stack from first launch
through populating RAG and extending it:

| Page | What's there |
|------|--------------|
| [First steps](getting-started/tutorials/01-first-steps.md) | Install the skill, generate a stack, bring it up, and try a demo room. |
| [Populate RAG](getting-started/tutorials/02-populate-rag.md) | Drop documents into the ingester and query them from a room. |
| [Add a custom tool](getting-started/tutorials/03-add-a-custom-tool.md) | Wire a Python tool into the backend and call it from an agent. |
| [Next steps](getting-started/tutorials/04-next-steps.md) | Where to go after the core walkthroughs. |
| [Separate RAG database](getting-started/tutorials/separate-rag-database.md) | Create and wire in an additional RAG database. |
| [Work with Gitea](getting-started/tutorials/work-with-gitea.md) | Back the stack with a Gitea repo and push the initial commit. |
| [Concierge room](getting-started/tutorials/concierge-room.md) | Add and use the concierge room. |
| [Resolve room requests](getting-started/tutorials/concierge-admin.md) | Administer concierge room requests. |

## Using a generated stack

Documentation that ships *inside* a generated project — how to bring the stack
up and operate it day to day:

| Page | What's there |
|------|--------------|
| [Installation](users/installation.md) | Generate secrets, set `OLLAMA_BASE_URL`, bring the stack up. |
| [Service graph](users/architecture/services.md) | What each container does and how they connect. |
| [Backend configuration](users/architecture/configuration.md) | The `backend/environment/` layout and the sandbox. |
| [Backend image & dependencies](users/architecture/backend.md) | How the backend image is built and how to add dependencies. |
| [Secrets](users/operations/secrets.md) | File-based vs env-var secret modes. |
| [RAG pipeline](users/operations/rag.md) | The vector store, the ingester, and adding documents. |
| [Ingester control plane](users/operations/ingester.md) | The control-plane API and its auth token. |
| [Custom Python package](users/custom-package.md) | The installable `src/` library wired into the backend. |

## Contributing

Working on this repository rather than running a stack:

| Page | What's there |
|------|--------------|
| [Developing this template](contributing/index.md) | The repo → template derivation, the refresh workflow, and the documentation conventions. |
