---
icon: lucide/footprints
---

# First steps

A guided walkthrough for someone new to Soliplex: install the skills, generate a
stack, bring it up, and exercise the demo tool in the `custom` room.

## Prerequisites

- A coding agent that supports filesystem Agent Skills (e.g. Claude Code).
- On the machine the agent drives: Docker (with the Compose plugin),
  [`uv`](https://docs.astral.sh/uv/), and an Ollama server reachable from the
  containers that serves the models the stack will reference.

## 1. Install the skills

Install two skills into your agent. Both are listed at
<https://agentskills.io/>; each is also published as a rolling `latest` release
you can install from directly:

- [`soliplex-template`](https://github.com/soliplex/soliplex-template/releases/tag/template-skill-latest)
  — generates and configures a stack (this template).
- [`soliplex-docs`](https://github.com/soliplex/soliplex/releases/tag/docs-latest)
  — Soliplex's documentation, so the agent can answer setup and usage questions
  as you go.

## 2. Generate a stack

Ask the agent to generate one — e.g. *"generate a new Soliplex stack"*. The
`soliplex-template` skill prompts for what it needs (at minimum your Ollama URL),
scaffolds a fresh project directory, and generates its secrets.

(To drive the generator by hand instead, see
[Generate a project](../generator.md).)

## 3. Bring it up

From the generated project directory:

```bash
docker compose up
```

The first run builds the `nginx` and `backend` images and initializes Postgres,
so it takes a few minutes. Once it settles, the web frontend is at
<http://localhost:9000>. The stack runs with auth off by default, so there is no
login step.

## 4. Visit the `custom` room

Open <http://localhost:9000> and select the **Custom Tool Demo** room. Every
generated stack ships it to demonstrate a tool provided by the project's *own*
Python package (`src/<package>/tools.py`).

## 5. Exercise the greeting tool

Ask the room to greet someone:

> Please greet Ada.

The room's agent calls the package's `greeting` tool and replies with something
like:

> Hello, Ada! This greeting came from your own package's tool.

That round-trip — your prompt, the agent calling a tool defined in *your*
package, and the result coming back — shows the stack already running your own
code, not just stock Soliplex.

## Where next

The next tutorial, [Populate RAG](02-populate-rag.md), feeds a document set into
the stack and asks questions grounded in it.
