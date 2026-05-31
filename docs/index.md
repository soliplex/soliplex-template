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

## Get running

The fastest path is the four-step quickstart in the repository
[`README.md`](https://github.com/soliplex/soliplex-template#quickstart). For the
full walkthrough — prerequisites, exposed ports, and how to verify the stack —
see **[Installation](getting-started/installation.md)**.

Want a customized project instead of a verbatim clone? The repository ships a
`soliplex-template` Agent Skill that scaffolds a tailored stack; see
**[Generate a custom project](getting-started/generator.md)**.

## Documentation map

| Section | What's there |
|---------|--------------|
| [Installation](getting-started/installation.md) | Clone, generate secrets, set `OLLAMA_BASE_URL`, bring the stack up. |
| [Generate a custom project](getting-started/generator.md) | Scaffold a tailored stack with the bundled skill. |
| [Service graph](architecture/services.md) | What each container does and how they connect. |
| [Backend configuration](architecture/configuration.md) | The `backend/environment/` layout and the sandbox. |
| [Backend image & dependencies](architecture/backend.md) | How the backend image is built and how to add dependencies. |
| [Secrets](operations/secrets.md) | File-based vs env-var secret modes. |
| [RAG pipeline](operations/rag.md) | The vector store, the ingester, and adding documents. |
| [Ingester control plane](operations/ingester.md) | The control-plane API and its auth token. |
