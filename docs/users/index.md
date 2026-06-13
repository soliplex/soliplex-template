---
icon: lucide/rocket
---

# myproject

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

A Soliplex Docker Compose stack — nginx + Soliplex backend + Flutter frontend +
haiku-ingester + Postgres, plus docling-serve and a TUI — scaffolded from the
`soliplex-template` generator.

This site documents *this* project. The quickest path to a running stack is the
`README.md` at the project root; for the full walkthrough see
[Installation](getting-started/installation.md).

## Services

- **nginx** — serves the Flutter web frontend and reverse-proxies the API.
- **backend** — the Soliplex server (`soliplex-cli serve`).
- **haiku-ingester** — the document-pipeline writer for the RAG vector store.
- **docling-serve** — stateless document converter.
- **postgres** — thread persistence and authorization policy.

## Exposed ports

| Service | Host port |
|---------|-----------|
| nginx (HTTP) | 9000 |
| nginx (HTTPS) | 9443 |
| haiku-ingester | 8765 |
| docling-serve | 5001 |
| postgres | 5432 |

## Documentation map

- **[Installation](getting-started/installation.md)** — generate secrets,
  confirm `OLLAMA_BASE_URL`, bring the stack up.
- **[Service graph](architecture/services.md)** — what each container does and
  how they connect.
- **[Backend configuration](architecture/configuration.md)** — the
  `backend/environment/` layout and the sandbox.
- **[Backend image & dependencies](architecture/backend.md)** — building the
  backend image and adding dependencies.
- **[Secrets](operations/secrets.md)** — file-based vs env-var secret modes.
- **[RAG pipeline](operations/rag.md)** — the vector store, the ingester, and
  adding documents.
- **[Ingester control plane](operations/ingester.md)** — the control-plane API
  and its auth token.
- **[Custom Python package](custom-package.md)** — the installable
  `src/myproject` library.
