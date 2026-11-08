# Extra RAG databases, and wiring them into rooms

Loads when: Load this reference to create a separate, mostly-static RAG database, update or prune one, or point rooms at a database created earlier.

## Creating and updating extra RAG databases

The stack's `haiku-ingester` *continuously* maintains a single LanceDB. When a
user wants a *separate*, mostly-static RAG database — e.g. one room per corpus —
run `scripts/rag_db.py` from inside the generated stack directory. It drives the
stack's `haiku-rag` service (the one-shot CLI runner sharing the ingester's
image, the `rag/db`/`rag/docs` mounts, the `OLLAMA_BASE_URL` env, and the same
`haiku.rag.yaml`) via `docker compose run`, writing to a *different*
`rag/db/<name>.lancedb`, so it never collides with the running ingester's
single-writer database.

```bash
# create a new database and populate it (db must not exist yet)
python3 scripts/rag_db.py create --db-name handbook --source rag/docs/handbook/

# add more documents / re-index / remove a document (db must already exist)
python3 scripts/rag_db.py update --db-name handbook --source https://example.com/a.html
python3 scripts/rag_db.py update --db-name handbook --rebuild --rechunk
python3 scripts/rag_db.py update --db-name handbook --delete <document_id>
```

`--source` accepts a path under `rag/docs/`, a remote URL/S3 URI, or any other
local path (auto-bind-mounted read-only).

Building the database does not make any room use it. **After `create`, offer to
wire the new database into one or more rooms.** If the user agrees, follow the
steps in *Wiring the database into rooms* below.

## Wiring the database into rooms

Wire a database into rooms when the user asks directly (including pointing a
room at a database created earlier), or when they accept the offer you made
after `create`. Either way:

1. **List the candidate rooms** with the `soliplex_config` helper:

   ```bash
   uv run scripts/soliplex_config.py rooms
   ```

   It emits a YAML list with one `{room_id, name, description}` mapping per
   room the installation actually loads (driven by the resolved `room_paths`,
   so it honors installations that limit their rooms or point at shared
   directories — a `rooms/*` glob would get that wrong). Present the `name`
   and `description` alongside each `room_id` so the user picks meaningfully,
   not from bare ids. (Requires the stack's images to be built; pass
   `--project-dir` if you are not in the stack directory.)

2. **Let the user pick one or more** of those rooms.

3. **Wire each chosen room** with the `add-rag-to-room` subcommand, passing
   `--room` once per selected `room_id`:

   ```bash
   python3 scripts/rag_db.py add-rag-to-room --db-name handbook \
       --room <room_id> [--room <room_id> ...]
   ```

   It resolves those ids to their `room_config.yaml` the same way (via the
   installation's `room_paths`), then sets `rag_lancedb_stem: "<name>"` on each
   room's `haiku.rag.skills.rag` skill config, editing the file in place
   (comments preserved). A room with no `skills:` block gets one appended; a
   room that already has a `skills:` block but no `haiku.rag.skills.rag` entry
   is reported so you can wire it by hand. See `docs/operations/rag.md`.
