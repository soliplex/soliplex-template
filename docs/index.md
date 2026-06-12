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
with the bundled `soliplex-template` Agent Skill, then run and operate that
generated project. The documentation is split to match:

## Using a generated stack

Documentation that ships *inside* a generated project — how to bring the stack
up and operate it day to day:

| Page | What's there |
|------|--------------|
| [Installation](users/getting-started/installation.md) | Generate secrets, set `OLLAMA_BASE_URL`, bring the stack up. |
| [Service graph](users/architecture/services.md) | What each container does and how they connect. |
| [Backend configuration](users/architecture/configuration.md) | The `backend/environment/` layout and the sandbox. |
| [Backend image & dependencies](users/architecture/backend.md) | How the backend image is built and how to add dependencies. |
| [Secrets](users/operations/secrets.md) | File-based vs env-var secret modes. |
| [RAG pipeline](users/operations/rag.md) | The vector store, the ingester, and adding documents. |
| [Ingester control plane](users/operations/ingester.md) | The control-plane API and its auth token. |
| [Custom Python package](users/custom-package.md) | The installable `src/` library wired into the backend. |

## Working on the template

Documentation for developing this repository — the generator skill and how the
embedded template is produced:

| Page | What's there |
|------|--------------|
| [Generate a custom project](contributing/generator.md) | Scaffold a tailored stack with the bundled skill. |
