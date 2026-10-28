# Move to soliplex 0.87

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.26-b57f7ba` skill release** —
its generator produces the rung's end state (the current template shape).  The
stack being upgraded names the release that generated it in its own
`[tool.soliplex-template]` manifest — use that one when climbing *from* an
older shape.

```bash
uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.26-b57f7ba
```

1. Stop the stack with `docker compose down`, and back up the Postgres
   databases and `rag/db/` before upgrading.

2. This rung is a pure release bump: the upgrade changes no template files
   beyond version spellings, sets up no database migrations (the last
   alembic migrations shipped with soliplex v0.86), and leaves the
   haiku.rag pair and docling-serve untouched.  Stacks that changed the
   generated files by hand should compare the rung's cut with the one
   their manifest names to confirm nothing else moved.

3. Bump the baked soliplex image tags from `0.86.1` to `0.87.0`: the
   `FROM` lines in `backend/Dockerfile`
   (`ghcr.io/soliplex/soliplex-postgres`) and, if the stack ships the
   TUI, `tui/Dockerfile` (`ghcr.io/soliplex/soliplex-tui`).  If
   `AGENTS.md` or `docs/architecture/backend.md` name the release
   version, update those spellings to match.

4. Update `soliplex_image_tag` and `soliplex_dev_constraint` in the
   project's manifest.  Use `>= 0.87.0, < 0.88` for the Soliplex
   requirement in the root and package `pyproject.toml` files, then run
   `uv lock` in each project.

5. Rebuild `backend`, `soliplex-dev`, and the optional `tui` with
   `docker compose build --pull`.

6. The default generated stack sets no migration policy, so the backend
   migrates its Alembic databases itself on startup.  Stacks which
   configured a migration policy are responsible for driving their own
   migrations; [the migrations
   reference](../users/operations/migrations.md) describes running them
   with the CLI.

7. Start the stack with `docker compose up -d` and inspect the backend
   logs.  Then [**Check it**](index.md#check-it), and commit.
