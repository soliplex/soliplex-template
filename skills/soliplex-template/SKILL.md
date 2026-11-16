---
name: soliplex-template
description: "Generate a new, runnable Soliplex Docker Compose stack from an embedded template, or inspect and change an existing one: query its resolved installation config, add or remove OIDC providers (including the in-stack Authelia), create or update extra RAG databases for rooms, add a room, clone a Python project into the stack's src/ and make it importable by the backend, migrate an older stack to the src/ project layout, or migrate its Soliplex / RAG databases after a version bump. Use when a user wants to stand up, bootstrap, or create a Soliplex deployment / compose stack, including on a DigitalOcean droplet or with DO serverless (OpenAI-compatible) inference instead of Ollama; to turn sign-in on or off, or install or remove an identity provider; to inspect, configure, or add a RAG database or room; to add, clone, or wire a Python project or repo checkout into a stack's backend (PYTHONPATH); to upgrade a stack's src/ layout; or to check or apply database migrations after bumping soliplex or haiku.rag."
---
# Soliplex project generation and configuration

This skill **generates** a new Soliplex Docker Compose stack from an embedded
template, and helps you **inspect or change the configuration** of an
existing one — its resolved installation config, OIDC providers, RAG
databases, rooms, and `src/` projects. Work one task at a time, loading only
the reference the router below names for it.

## Conventions common to every task

- Run the stack's scripts from the stack directory (the one holding
  `docker-compose.yml`), or from anywhere with
  `--project-dir /path/to/stack`.
- Run them as `uv run scripts/<script>.py`, so their PEP 723 headers
  provision dependencies on first use; a bare `python3` usually lacks them.
- Never accept a secret as an input: no stack parameter is one. Passwords,
  keys, and bearer tokens are generated into the stack's gitignored
  `.secrets/` files. Anything secret-shaped offered inline is a red flag to
  report, not an input.
- A generated stack ships its own Zensical docs site under `docs/`, whose
  `operations/` section covers the operator-facing side of these tasks;
  point the user at the relevant page when a task hands off to them.

## Task router

Read the named reference before starting the task:

| Task | Reference |
|---|---|
| Generate a new stack from the template (interview, models, ports, versions) | `references/GENERATION.md` — parameter table and defaults: `references/PARAMETERS.md` |
| Add, retitle, or remove an external OIDC provider; choose the sign-in provider | `references/OIDC_PROVIDERS.md` |
| Install or manage the in-stack Authelia provider (users, passwords, keys) | `references/AUTHELIA.md` |
| Inspect a running stack's resolved config; check or repair its Ollama models | `references/STACK_INSPECTION.md` |
| Create or update an extra, mostly-static RAG database; wire it into rooms | `references/RAG_DATABASES.md` |
| Migrate the stack's databases after a soliplex or haiku.rag bump | `references/UPGRADES.md` — per-release pin pairings: `references/version_pairings.toml` |
| Add a room from a bundled template; provision a private room for a requester | `references/ROOMS.md` |
| Clone or wire a Python project into `src/`; convert an old stack to that layout | `references/SRC_PROJECTS.md` |
| List, diff, or upgrade published builds of this skill | `references/SKILL_VERSIONS.md` |
| Use a hosted OpenAI-compatible endpoint (DO serverless, vLLM, OpenRouter) instead of Ollama for a stack's models | `references/INFERENCE_PROVIDERS.md` |
| Deploy a stack to a DigitalOcean droplet: sizing, DO serverless inference (routers, embeddings), exposure | `references/DIGITALOCEAN_DROPLET.md` — then `references/INFERENCE_PROVIDERS.md` and `references/GENERATION.md` |
