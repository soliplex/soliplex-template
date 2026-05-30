---
name: soliplex-project-generator
description: Scaffold a new, runnable Soliplex Docker Compose project (nginx + Soliplex backend + Flutter frontend + haiku-ingester + Postgres, plus docling-serve and a TUI). Prompts for parameters (project name, host ports, OLLAMA_BASE_URL, models, Postgres DB names, version pins, auth mode, docs dir, ingester token) and generates the full stack from an embedded template. Use when a user wants to stand up, bootstrap, or create a new Soliplex deployment / compose stack.
---

# Soliplex project generator

Generate a fresh Soliplex Docker Compose stack from the embedded template under
`assets/template/`. The heavy lifting is done by `scripts/generate.py`; your job
is to collect parameters from the user and invoke it.

## Steps

1. **Gather parameters.** Ask the user for the values they care about. Sensible
   defaults exist for everything except `ollama_base_url` (always required). The
   full list, defaults, and validation rules are in
   [references/PARAMETERS.md](references/PARAMETERS.md). The most commonly set:
   - `output_dir` (where to create the project — this is the `--out` argument)
   - `project_name`
   - `ollama_base_url` (**required**, e.g. `http://host:11434`)
   - host ports: `nginx_http`, `nginx_https`, `ingester_port`, `docling_port`,
     `postgres_port`
   - `auth_mode` (`no-auth` or `auth`)
   - models (`chat_model`, `title_model`, `rag_embed_model`, …)

   You can show the defaults with:
   ```bash
   uv run scripts/generate.py --print-defaults
   ```

2. **Write the answers to a JSON file** (omit keys to accept defaults), e.g.
   `params.json`:
   ```json
   { "project_name": "acme", "ollama_base_url": "http://ollama:11434",
     "nginx_http": 9100, "auth_mode": "auth" }
   ```

3. **Run the generator:**
   ```bash
   uv run scripts/generate.py --out <output_dir> --params params.json
   ```
   - Without `uv`: `pip install mako && python3 scripts/generate.py --out <dir> --params params.json`
   - Add `--interactive` to be prompted on stdin instead of (or in addition to)
     a params file.
   - Add `--run-secrets` to also run `generate-secrets.sh` in the new project.
   - Add `--no-git` to skip the initial git commit, or `--disable-gpg-sign` if
     the host's commit-signing config would block a non-interactive commit.

4. **Report the result.** The script prints the chosen ports/models and the next
   steps. Relay those. If `--run-secrets` was not used, the user must run
   `./scripts/generate-secrets.sh` before `docker compose up`.

## Notes

- `.mako` files in the template are rendered with the parameters; all other
  files are copied verbatim. Literal `${...}` (docker-compose / shell
  interpolation) is preserved.
- The generator validates ports (range + uniqueness), requires
  `ollama_base_url`, and checks DB identifiers before writing anything.
- `.env` and `.secrets/` in the generated project are gitignored, so the initial
  commit never captures secrets.
