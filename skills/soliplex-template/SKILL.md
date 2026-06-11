---
name: soliplex-template
description: "Generate a new, runnable Soliplex Docker Compose stack from an embedded template, or inspect and change the configuration of an existing one — query its resolved installation config, and create or update extra RAG databases (with guidance for wiring them into rooms). Use when a user wants to stand up, bootstrap, or create a new Soliplex deployment / compose stack, or to inspect, configure, or add a RAG database to an existing one."
---

# Soliplex project generation and configuration

This skill both **generates** a new Soliplex Docker Compose project from an
embedded template and helps you **inspect or change the configuration** of an
existing one — its resolved installation config and its RAG databases.

## Soliplex project generator

Generate a fresh Soliplex Docker Compose stack — nginx, the Soliplex backend,
the Flutter web frontend, haiku-ingester, Postgres, docling-serve, and a TUI —
from the embedded template under `assets/template/`. The heavy lifting is done
by `scripts/generate_soliplex_project.py`; your job is to collect parameters
from the user and invoke it.

### Steps

1. **Gather parameters.** Defaults exist for everything except
   `ollama_base_url` (always required); the full list, defaults, and validation
   rules are in [references/PARAMETERS.md](references/PARAMETERS.md). Collect
   the values in two passes — resolve anything the user supplied inline first,
   then prompt only for what's left.

   **(a) Resolve inline shortcuts.** If the skill was invoked with arguments
   (e.g. `/soliplex-template where=./test project=acme
   ollama=http://host:11434 ports=default`), parse them *before* prompting.
   Read [assets/aliases.json](assets/aliases.json) — the single source of truth
   for the shortcut vocabulary — and apply:
   - `key=value` where `key` is a raw parameter name **or** an entry in
     `aliases` → set that parameter. The one special case is `where` /
     `output_dir`, which is **not** a parameter: it becomes the `--out`
     argument (the target directory).
   - `<group>=default` for a key in `groups` (e.g. `ports=default`,
     `models=default`) → accept the defaults for every member of that group and
     **skip its prompt**.
   - Flags: `force` (→ `--force`), `git=no` (→ `--no-git`), `secrets=no`
     (→ `--no-generate-secrets`).
   - A token whose key matches none of the above → don't guess; show it back to
     the user and ask what they meant.
   - **Never accept a secret inline.** `ingester_token` has no alias and must
     not be read from the command line (it would land in shell history); always
     prompt for it (or accept its warned default) in the secrets step below.

   **(b) Prompt for whatever is still unanswered** — and *only* that, so a
   fully-specified command runs with no questions while a bare
   `/soliplex-template` walks every group. Use `AskUserQuestion`, grouping
   related options into one question each:
   - **location** — `output_dir` (where to create the project; free text,
     required to actually write) and whether to `--force` into a non-empty dir.
   - **identity** — `project_name` (drives `server_name`, `setup_id`, and the
     derived `package_name`).
   - **ollama** — `ollama_base_url` (**required**, e.g. `http://host:11434`;
     free text — always obtain it, inline or by asking).
   - **ports** — offer "use defaults" vs "customize"; `ports=default`
     pre-answers this. Customizing collects `nginx_http`, `nginx_https`,
     `ingester_port`, `docling_port`, `postgres_port`.
   - **auth** — `auth_mode`: `no-auth` (default) or `auth`.
   - **models** — offer "use defaults" vs "customize"; `models=default`
     pre-answers this. Whichever way the models are chosen, **probe them
     against `ollama_base_url` before generating** — see *Choosing and checking
     models* below.
   - **versions** — `frontend_version` and `soliplex_backend_constraint` (use
     the version-listing guidance below; they are independent choices).
   - **gitea** — `include_gitea`: include the opt-in local Gitea service or not.
   - **secrets handling** — prompt for `ingester_token` (or accept the warned
     default) and confirm the secrets/git flags.

   You can show all defaults with:

   ```bash
   uv run scripts/generate_soliplex_project.py --print-defaults
   ```

   **Choosing and checking models.** Every model parameter resolves through the
   default Ollama provider at `ollama_base_url` — the template pins no other
   provider — so each chosen model must exist *and load* on that server. The
   chat-style model parameters are `chat_model`, `chat_model_alt`,
   `title_model`, `rag_qa_model`, and `rag_research_model`; the embedding model
   is `rag_embed_model`. (`rag_embed_dim` and `chunk_size` ride in the same
   `models` shortcut group but are **not** model names — `rag_embed_dim` must
   match the embedding model's vector dimension.)

   **Offer real choices** instead of asking the user to recall a tag: list the
   models the server actually has and present them (defaults pre-selected),
   keeping the chat models and the embedding model in separate questions so an
   embedding model isn't picked for chat:

   ```bash
   curl -s "$OLLAMA_BASE_URL/api/tags" | jq -r '.models[].name'
   ```

   **Probe responsiveness before generating — always, even for
   `models=default`** (the `gpt-oss:*` / `qwen3-embedding:4b` defaults are not
   guaranteed to be present or to load). Don't merely check that a name is in
   the list; actually exercise each selected model, so one that is missing, not
   pulled, or won't load (OOM, bad quantization) is caught before the project is
   written. Probe chat-style models via the OpenAI-compatible
   `/v1/chat/completions` endpoint and the embedding model via `/v1/embeddings`
   (a non-2xx / curl failure means it isn't usable):

   ```bash
   # each chat-style model (chat_model, chat_model_alt, title_model, rag_qa_model, rag_research_model)
   curl -sf "$OLLAMA_BASE_URL/v1/chat/completions" \
     -d '{"model":"<name>","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'

   # the embedding model (rag_embed_model)
   curl -sf "$OLLAMA_BASE_URL/v1/embeddings" -d '{"model":"<name>","input":"ping"}'
   ```

   For any model whose probe fails, offer the user a fix: either **switch to a
   model the server has** (from the `/api/tags` list, then re-probe), or **pull
   the missing model now** and re-probe to confirm it loads:

   ```bash
   curl -s "$OLLAMA_BASE_URL/api/pull" -d '{"model":"<name>","stream":false}'
   ```

   If `ollama_base_url` itself is unreachable at this point (e.g. a remote
   server that isn't up yet), say so, fall back to the defaults or free-text
   entry, and note that an authoritative check will run after the stack is up
   (see *Report the result* below). A post-generation live probe is also coming
   to `soliplex-cli audit ollama` (soliplex#1067); the pre-generation probe here
   is the skill's own, complementary check.

   The frontend version (`frontend_version`) and the backend `soliplex`
   version (`soliplex_backend_constraint`) are **independent choices**: the
   `soliplex/frontend` repo and the `soliplex` PyPI package release on their
   own schedules, so any frontend version can pair with any backend version.
   **Ask for them as two separate selections**, each with its own list of real
   versions (below). Never present paired/coupled front-end+back-end
   combinations; the user picks one value on each axis.

   For `frontend_version`, **offer the user real choices** instead of asking
   them to recall a tag: list recent `soliplex/frontend` releases (newest
   first) and present them alongside `latest` (the default):

   ```bash
   curl -s https://api.github.com/repos/soliplex/frontend/releases \
     | jq -r '.[].tag_name' | head
   ```

   Fall back to `latest` (or free-text tag entry) if the API is unreachable or
   rate-limited; set `GITHUB_TOKEN`/`GH_TOKEN` to raise the rate limit.

   For the backend `soliplex` version (`soliplex_backend_constraint`, which
   lands in `backend/constraints.txt`), **offer the user real choices** instead
   of asking them to hand-write a constraint: list the published `soliplex`
   releases on PyPI (oldest first) and present the most recent:

   ```bash
   curl -s https://pypi.org/pypi/soliplex/json \
     | jq -r '.releases | keys[]' | sort -V | tail
   ```

   From the user's pick, set `soliplex_backend_constraint` — e.g. `== 0.68.3`
   to pin a single release, or a range such as `>= 0.68, < 0.69`. Fall back to
   the default constraint if PyPI is unreachable.

2. **Write the answers to a JSON file** (omit keys to accept defaults), e.g.
   `params.json`:

   ```json
   { "project_name": "acme", "ollama_base_url": "http://ollama:11434",
     "nginx_http": 9100, "auth_mode": "auth" }
   ```

3. **Run the generator:**

   ```bash
   uv run scripts/generate_soliplex_project.py --out <output_dir> --params params.json
   ```

   - Without `uv`: `pip install mako && python3 scripts/generate_soliplex_project.py --out <dir> --params params.json`
   - Add `--interactive` to be prompted on stdin instead of (or in addition to)
     a params file.
   - `generate-secrets.sh` is run in the new project **by default**; add
     `--no-generate-secrets` to skip it (e.g. to generate secrets yourself
     later).
   - Add `--no-git` to skip the initial git commit, or `--disable-gpg-sign` if
     the host's commit-signing config would block a non-interactive commit.

4. **Report the result.** The script prints the chosen ports/models and the next
   steps. Relay those. Secrets are generated by default; if you passed
   `--no-generate-secrets`, the user must run `./scripts/generate-secrets.sh`
   first.

   If you go on to bring the stack up yourself, **build the images explicitly
   first** rather than letting `up` build them implicitly: run
   `docker compose build`, then `docker compose up -d`, then poll
   `docker compose ps` (or `docker compose logs -f`) until services report
   healthy. The nginx image runs a full Flutter web build, so the first build
   can take several minutes — a separate `build` step makes that wait legible
   instead of hiding it inside `up`.

   Once the backend reports healthy, **verify the Ollama models the install
   actually references** with the backend's own audit, and pull any that are
   missing. This is the authoritative, config-driven check — it runs against the
   *resolved* installation, so it covers the models named in every referenced
   YAML file (and only those that resolve via Ollama), not just the ones you
   set in the interview:

   ```bash
   # reachable / MISSING / OK per configured Ollama URL
   docker compose run --rm backend /app/.venv/bin/soliplex-cli audit ollama /environment

   # if any URL reports MISSING, pull them (run with -n first to preview)
   docker compose run --rm backend /app/.venv/bin/soliplex-cli ollama pull /environment
   ```

### Notes

- `.mako` files in the template are rendered with the parameters; all other
  files are copied verbatim. Literal `${...}` (docker-compose / shell
  interpolation) is preserved.
- The generator validates ports (range + uniqueness), requires
  `ollama_base_url`, and checks DB identifiers before writing anything.
- `.env` and `.secrets/` in the generated project are gitignored, so the initial
  commit never captures secrets.
- The generated project includes its own Zensical documentation site under
  `docs/` (parameterized for that project) plus `zensical` in its `dev`
  dependency group; the owner can preview it with `uv run zensical serve`.

## Inspecting a running stack's config

When you need to reason about what a *running* stack is actually configured to
do — rather than re-reading the `backend/environment/` source — query its
**resolved** installation config with `scripts/soliplex_config.py`, run from the
stack directory. It runs `soliplex-cli config` inside a one-off `backend`
container (`docker compose run --rm`) and parses the YAML it emits, so it
reflects defaults, secret/env substitution, and `room_paths` resolution exactly
as the backend sees them.

```bash
python3 scripts/soliplex_config.py show                 # whole resolved config
python3 scripts/soliplex_config.py get room_paths        # one value (dotted path)
python3 scripts/soliplex_config.py get room_paths.0      # list index
python3 scripts/soliplex_config.py rooms                 # {room_id, name, description} per loaded room
python3 scripts/soliplex_config.py room chat             # full room_config.yaml of one room by id
```

`get` prints scalars bare and lists of scalars one per line (shell-friendly);
add `--format yaml` to dump any value — including nested structures — as YAML.
`rooms` emits a YAML list with one `{room_id, name, description}` mapping per
loaded room (`name`/`description` are `null` when a room omits them); use it
when you need a room's id or human-readable name. `room <room_id>` prints that
one room's full `room_config.yaml` verbatim (comments and all), and errors if
no loaded room has that id. Both read the rooms the running stack actually
loads (so the set matches what the backend sees); a room on a shared mount
outside the installation is skipped with a warning on stderr. Pass
`--project-dir` if you are not already in the stack directory;
`--service`/`--cli`/`--installation` override the backend defaults.

The script needs PyYAML; `uv run scripts/soliplex_config.py …` supplies it from
the script's inline metadata, or `pip install pyyaml` first.

### Checking and repairing Ollama models

The model names a running stack uses all resolve through the Ollama server at
`OLLAMA_BASE_URL`; if that URL changes (edited in `.env`) or the server loses
models (a flush, a cache eviction), requests will start failing. Check the
install's referenced models against each configured server — and pull back any
that went missing — with the backend's own audit, run from the stack directory
in a one-off `backend` container the same way as `soliplex_config.py`:

```bash
# reachable / MISSING / OK per configured Ollama URL
docker compose run --rm backend /app/.venv/bin/soliplex-cli audit ollama /environment

# pull whatever the install references (-n previews; -u URL scopes to one server)
docker compose run --rm backend /app/.venv/bin/soliplex-cli ollama pull /environment
```

`audit ollama` exits non-zero when a server is unreachable or missing models,
and with `-q` (placed before the `ollama` subcommand) prints a JSON error
report, so it is scriptable. Both commands need the stack's images built and
should be run from the directory holding `docker-compose.yml`.

## Creating and updating extra RAG databases

The stack's `haiku-ingester` *continuously* maintains a single LanceDB. When a
user wants a *separate*, mostly-static RAG database — e.g. one room per corpus —
run `scripts/rag_db.py` from inside the generated stack directory. It reuses the
`haiku-ingester` service (its image, the `rag/db`/`rag/docs` mounts, the
`OLLAMA_BASE_URL` env, and the same `haiku.rag.yaml`) via `docker compose run`,
writing to a *different* `rag/db/<name>.lancedb`, so it never collides with the
running ingester's single-writer database.

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
   python3 scripts/soliplex_config.py rooms
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

## Adding a room to an existing stack

To add a room to a stack — one generated by this skill, or any hand-built
Soliplex installation laid out the same way — render one of the skill's bundled
room templates with `scripts/add_room.py`. It is pure filesystem work (no Docker,
no running backend): it writes
`backend/environment/rooms/<room-id>/room_config.yaml` and makes sure
`room_paths` loads it. Generated stacks point `room_paths` at `./rooms`, which
auto-discovers every room beneath it, so no `installation.yaml` edit is needed
(reported as `covered`); installations that instead enumerate their rooms get
`- "./rooms/<room-id>"` spliced in (line-based, so comments and layout are
preserved). The backend serves with `--reload=config`, so the new room is picked
up without an image rebuild or restart.

1. **Show the bundled templates** and let the user choose one:

   ```bash
   uv run scripts/add_room.py list
   ```

   The templates are `chat` (conversational RAG), `search` (search-and-answer
   with citations), `minimal` (bare agent + prompt), and `sandbox` (file upload +
   bubblewrap code execution + RAG). Each renders with commented-out examples for
   adding custom tools and skills (both filesystem and entrypoint), for the
   operator to uncomment later.

2. **Gather the parameters** with `AskUserQuestion`: `--room-id` (required;
   letters/digits/`.`/`_`/`-`, no leading dot — also the directory name and the
   `room_paths` entry), `--name`, `--description`, `--agent-template` (an agent
   `template_id` from `installation.yaml`, default `default_chat`; the other
   shipped templates are `alternate_chat` and `title`), a system prompt
   (`--system-prompt "…"` inline, or `--prompt-file PATH` to drop a `prompt.txt`
   in the room and reference it; omit both to take the template's default), and
   `--rag-stem` for the chat/search/sandbox templates (the LanceDB stem the room
   reads, default `haiku.rag` — set it to a database created with `rag_db.py`).

3. **Dry-run first**, then apply (run from the stack dir, or pass
   `--project-dir`):

   ```bash
   uv run scripts/add_room.py add --project-dir /path/to/stack \
       --template chat --room-id handbook \
       --name "Handbook" --description "Q&A over the staff handbook" --dry-run
   ```

   Drop `--dry-run` to write. The room directory must not already exist (pass
   `--force` to overwrite it). The `room_paths` line reports `added`, `covered`
   (a `./rooms` entry already loads it), or `unchanged` (already listed).

4. **Verify** the install now loads the room (the `--reload=config` backend
   needs no rebuild):

   ```bash
   python3 scripts/soliplex_config.py rooms --project-dir /path/to/stack
   ```

   Then point the user at the generated `room_config.yaml` to uncomment the
   tool/skill examples or refine the prompt.

## Managing this skill's version

`scripts/skill_versions.py` lists, diffs, and upgrades published builds of this
skill against its GitHub releases. It is a small
[PEP 723](https://peps.python.org/pep-0723/) helper backed by the shared
[`soliplex-skills`](https://soliplex.github.io/soliplex-skills/) library, so run
it with `uv` (the first run fetches that library):

```bash
uv run scripts/skill_versions.py list              # published versions, newest first
uv run scripts/skill_versions.py diff [TAG]         # installed vs a published build (default: latest)
uv run scripts/skill_versions.py upgrade [TAG]      # install a published build in place (default: latest)
```

Two kinds of versions are published. **Release** builds are snapshots attached
to tagged software releases (`v…`) — stable milestones that only change when a
release is cut. **Rolling** builds (`template-skill-YYYY.MM.DD-<sha>`) are
continuous per-build snapshots, tagged with the build date and short commit
hash; the `template-skill-latest` pointer always tracks the newest one, so the
default `latest` target for `diff`/`upgrade` means "the current tip of the
rolling line." `list` shows both newest first (marking the installed copy and
the `latest` pointer); narrow it with `list --kind release` or
`list --kind rolling`. To stay on stable milestones rather than the rolling
tip, pass an explicit `v…` `TAG` to `diff`/`upgrade`.

Set `GITHUB_TOKEN`/`GH_TOKEN` to raise the GitHub API rate limit. The helper
needs network access to PyPI (to provision `soliplex-skills` on first run) and
to `api.github.com`/`github.com`.
