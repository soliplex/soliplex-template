---
icon: lucide/database
---

# Populate RAG

Feed a set of documents into a stack, ask questions grounded in them, then build
a second, independent knowledge base and point a room at it. This continues from
[First steps](01-first-steps.md).

## Prerequisites

- A generated stack, with the `soliplex-template` and `soliplex-docs` skills
  installed in your agent (see [First steps](01-first-steps.md)).
- Your Ollama server must serve the embedding model the stack uses
  (`qwen3-embedding:4b` by default) — ingestion embeds every chunk with it.

## 1. Bring the stack up

From the generated project directory:

```bash
docker compose up
```

## 2. Add a document set

The `haiku-ingester` service watches `rag/docs/` (a filesystem source). Drop
Markdown files there; it hands them to docling-serve for conversion, chunks and
embeds them, and writes the stack's main RAG database,
`rag/db/haiku.rag.lancedb`.

Any Markdown works — the Soliplex documentation is a convenient corpus. If you
have a checkout of it:

```bash
rsync -rv ~/projects/soliplex/docs/ rag/docs/
```

Watch progress on the ingester control plane at <http://localhost:8765/>; the
dashboard prompts for the `INGESTER_TOKEN` (`secret` by default). Once `/stats`
shows the documents processed and the job queue drained, the corpus is
searchable.

## 3. Wire the corpus into the `custom` room

A freshly generated stack's `custom` room has only the greeting tool — no RAG.
Ask your agent to wire the ingested database into it (the `soliplex-template`
skill has a helper for exactly this). It adds a RAG skill to the room's
`backend/environment/rooms/custom/room_config.yaml`:

```yaml
skills:
  skill_configs:
    - kind: "haiku.rag.skill.rag"
      rag_lancedb_stem: "haiku.rag"
```

The stem `haiku.rag` is the ingester's database. The backend runs with
`--reload=config`, so it picks the change up without a restart.

## 4. Ask a grounded question

Open the **Custom Tool Demo** room and ask something the corpus can answer, e.g.:

> What secret sources does Soliplex support?

The room's agent calls its `search_documents` tool, retrieves the matching
chunks, and answers from them — with citations back to the source documents.

## 5. Build a separate, static database

The ingester maintains one continuously-updated database. For a separate corpus
you build once, ask the agent to create a standalone database — say a `handbook`
from another directory of Markdown. The skill builds `rag/db/handbook.lancedb`
as a one-off, independent of the ingester's `haiku.rag.lancedb` (and it refuses
to overwrite the ingester's own database while that service is running).

## 6. Point a cloned room at it

Ask the agent to clone the `custom` room under a new id — say `handbook` — and
wire that copy to the new database (`rag_lancedb_stem: "handbook"`) instead of
the ingester's. Open the new room and ask a question answerable from the
`handbook` corpus: it answers from that database, while the `custom` room still
uses the ingester's.

## Where next

The next tutorial, [Work with Gitea](03-work-with-gitea.md), edits the stack's
own code and ships the change through a pull request.
