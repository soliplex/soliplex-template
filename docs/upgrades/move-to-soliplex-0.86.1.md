# Move to soliplex 0.86.1 and haiku.rag 0.101.0

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.24-932e134` skill release** — its generator produces the rung's end state (the current template shape).  The stack being upgraded names the release that generated it in its own `[tool.soliplex-template]` manifest — use that one when climbing *from* an older shape.

    ```bash
    uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.24-932e134
    ```

1. Stop `backend` and `haiku-ingester`, and back up the Postgres databases
   and `rag/db/` before upgrading.

2. Adopt the image-shape changes, which came to the template ahead of
   this step: the image shim Dockerfiles (`haiku.rag/Dockerfile`,
   `docling-serve/Dockerfile`) are gone; their build recipes moved to
   `README.md` files beside their build contexts, and the template's
   `build:` stanzas for those services ship commented. Delete your
   copies (or rename them to the `README.md`s, which carry the
   recipes), and comment the stanzas the same way.

3. Set the backend and optional TUI image tags to `0.86.1`; set the
   haiku.rag image to `0.101.0` in the `haiku_rag_image` anchor in
   `docker-compose.yml`; set the `docling-serve` image to
   `quay.io/docling-project/docling-serve-cpu:v1.38.0`.

4. Update `soliplex_image_tag`, `haiku_rag_version`, and
   `soliplex_dev_constraint` in the project's manifest. Use
   `>= 0.86.1, < 0.87` for the Soliplex requirement in the root and package
   `pyproject.toml` files, then run `uv lock` in each project.

5. In `haiku.rag/haiku.rag.yaml`, move the source list from
   `ingester.sources` to the `sources` list of the `ingester.databases`
   entry keyed `haiku.rag` (the key is the database's own name, dot
   included). Keep its
   source ids and paths. Add `namespace: soliplex` under `ingester.queue`.
   This still writes `/data/haiku.rag.lancedb`. Additional databases are
   described in
   [RAG pipeline](../users/operations/rag.md#ingesting-multiple-databases).

6. In the `haiku-ingester` Compose command, change `sh -c` to `sh -ec` and
   insert this command after exporting `INGESTER_DB_PASSWORD` and before
   `exec haiku-ingester --config /app/haiku.rag.yaml serve`:

    ```bash
    haiku-ingester --config /app/haiku.rag.yaml queue migrate
    ```

7. Rebuild `backend`, `soliplex-dev`, `haiku-ingester`, `docling-serve`, and
   the optional `tui` with `docker compose build --pull`.

8. Before restarting the ingester, follow
   [the queue migration](../users/operations/migrations.md#the-ingesters-job-queue)
   to migrate the schema and assign existing rows to `soliplex`. Otherwise
   the ingester will not see its previous jobs and sync state.  The template
   prepares the queue schema before every ingester start. Manual namespace
   assignment is needed only when upgrading rows from a queue without
   namespaces.

9. The ingester's new database API is `GET /databases/{name}`; update any
   callers of `GET /database` to follow suit.

10. Run `uv run scripts/migrate_soliplex_dbs.py --check` and
   `uv run scripts/migrate_rag_dbs.py --check`; apply any reported migrations
   with the relevant service stopped. Start the stack with
   `docker compose up -d` and inspect the backend and ingester logs.
   Then [**Check it**](index.md#check-it), and commit.
