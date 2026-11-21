# RAG Artifacts

- `db/` holds the LanceDB databases, one `<stem>.lancedb` directory each.
  `db/haiku.rag.lancedb` is the one the `haiku-ingester` service keeps up to
  date; any other is a separate, one-off database (for example one built by
  the `soliplex-template` skill's `rag_db.py`).
- `docs/` holds the documents the `haiku-ingester` service ingests into
  `db/haiku.rag.lancedb` (the default `docs_dir`; a stack generated with a
  different `docs_dir` mounts that directory instead).
