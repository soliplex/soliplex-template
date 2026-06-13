---
icon: lucide/database
---

# Separate RAG database

Build a second, independent knowledge base — a one-off database you create and
point a room at — alongside the ingester's continuously-updated one. This builds
on [Populate RAG](02-populate-rag.md), so do that first (it covers ingesting a
corpus and querying it from a room).

## 1. Build a separate, static database

The `haiku-ingester` service maintains one continuously-updated database
(`rag/db/haiku.rag.lancedb`). For a separate corpus you build once, ask the
agent to create a standalone database — say a `handbook` from another directory
of Markdown. The skill builds `rag/db/handbook.lancedb` as a one-off,
independent of the ingester's database (and it refuses to overwrite the
ingester's own database while that service is running).

## 2. Point a cloned room at it

Ask the agent to clone the `custom` room under a new id — say `handbook` — and
wire that copy to the new database (`rag_lancedb_stem: "handbook"`) instead of
the ingester's. Open the new room and ask a question answerable from the
`handbook` corpus: it answers from that database, while the `custom` room still
uses the ingester's.

## Where next

Back to [Additional topics](04-additional-topics.md) for the other optional
walkthroughs.
