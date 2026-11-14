# Generating a new Soliplex stack

Loads when: Load this reference before generating a new Soliplex Docker Compose stack from the embedded template, and whenever generation inputs are revisited (models, versions, ports, providers).

## Soliplex project generator

Generate a fresh Soliplex Docker Compose stack — nginx, the Soliplex backend,
the Flutter web frontend, haiku-ingester, Postgres, docling-serve, and a TUI —
from the embedded template under `assets/template/`. The heavy lifting is done
by `scripts/generate_soliplex_project.py`; your job is to collect parameters
from the user and invoke it.

### Steps

1. **Gather parameters.** Defaults exist for everything except
   `ollama_base_url` (always required); the full list, defaults, and validation
   rules are in [PARAMETERS.md](PARAMETERS.md). Collect
   the values in two passes — resolve anything the user supplied inline first,
   then prompt only for what's left.

   **(a) Resolve inline shortcuts.** If the skill was invoked with arguments
   (e.g. `/soliplex-template where=./test project=acme
   ollama=http://host:11434 ports=default`), parse them *before* prompting.
   Read [`assets/aliases.json`](../assets/aliases.json) — the single source of truth
   for the shortcut vocabulary — and apply:
   - `key=value` where `key` is a raw parameter name **or** an entry in
     `aliases` → set that parameter. Two aliases are **not** parameters but
     route to CLI arguments, and name the target two different ways:
     `where` / `output_dir` becomes `--out` (that directory *is* the project),
     while `in` / `parent_dir` becomes `--parent` (the project is created
     *inside* it, in a directory named for `project_name`). Prefer `in=` when
     the user points at a directory that holds other stacks. They are mutually
     exclusive.
   - `<group>=default` for a key in `groups` (e.g. `ports=default`,
     `models=default`) → accept the defaults for every member of that group and
     **skip its prompt**.
   - Flags: `force` (→ `--force`), `git=no` (→ `--no-git`), `secrets=no`
     (→ `--no-generate-secrets`).
   - A token whose key matches none of the above → don't guess; show it back to
     the user and ask what they meant.
   - **Never accept a secret inline.** No parameter is a secret (the ingester
     control plane's bearer is generated into `.secrets/ingester_bearer.gen`);
     anything secret-shaped offered inline is a red flag, not an input.

   **(b) Prompt for whatever is still unanswered** — and *only* that, so a
   fully-specified command runs with no questions while a bare
   `/soliplex-template` walks every group. Use `AskUserQuestion`, grouping
   related options into one question each:
   - **location** — `output_dir` (where to create the project; free text,
     required to actually write) and whether to `--force` into a non-empty dir.
   - **identity** — `project_name` (drives `server_name`, `setup_id`, and the
     derived `package_name`). **`soliplex` is reserved**: the generator
     rejects it with `ReservedPackageName` and writes nothing, because the
     stack's own package would shadow the installed distribution's
     `soliplex.tools` / `soliplex.views` subpackages, and `src/soliplex/` is
     where a dev-mode checkout is cloned. The check is on the derived
     `package_name`, so `Soliplex` and `SOLIPLEX` fail too. Catch this when
     the name arrives — inline (`project=soliplex`) or in the answer — and
     offer the default `soliplex-dojo` instead of letting generation fail.
   - **ollama** — `ollama_base_url` (**required**, e.g. `http://host:11434`;
     free text — always obtain it, inline or by asking). If the answer names
     `localhost` / `127.0.0.1`, see *A local Ollama on the Docker host* below
     before generating.
   - **ports** — offer "use defaults" vs "customize"; `ports=default`
     pre-answers this. nginx's host ports (`nginx_http`, `nginx_https`)
     are always published; **every other service's external port is
     opt-in** (unset = the service binds only the compose network). Customizing
     collects the nginx ports plus whichever opt-ins the user wants:
     `ingester_port`, `docling_port`, `postgres_port`, `backend_port`,
     and, when `include_gitea`, `gitea_http_port`/`gitea_ssh_port`.
     Unset = unpublished.
   - **sign-in** — a generated stack always starts with no OIDC provider,
     and so without authentication. Ask whether the user wants sign-in, and
     if so which provision they choose from: the in-stack Authelia provider
     (install it after generating with `scripts/install_authelia.py`;
     load `references/AUTHELIA.md` for that), or external providers
     (collect each provider's details and add them after generating,
     **before** `stack-init` and the first `up` — load
     `references/OIDC_PROVIDERS.md` for that). Do not pass `auth_mode`: it is
     deprecated, and `auth` is refused.
   - **models** — offer "use defaults" vs "customize"; `models=default`
     pre-answers this. Whichever way the models are chosen, **probe them
     against `ollama_base_url` before generating** — see *Choosing and checking
     models* below.
   - **versions** — `frontend_version` and `soliplex_image_tag` (use the
     version-listing guidance below; they are independent choices).
   - **gitea** — `include_gitea`: include the opt-in local Gitea service or not.
   - **sandbox** — `enable_sandbox`: let rooms run agent code in the bubblewrap
     sandbox. It lifts the backend container's seccomp, AppArmor and `/proc`
     masking, so say so when asking; default no. Without it the `sandbox` room
     template is unavailable.
   - **secrets handling** — no secret is prompted for: the ingester control
     plane's bearer is generated into `.secrets/ingester_bearer.gen` (see
     "Ingester control plane auth" in the generated AGENTS.md). Confirm the
     secrets/git flags: generating them here (default) or skipping
     (`secrets=no`), initial git commit (`git=no` skips).

   You can show all defaults with:

   ```bash
   uv run scripts/generate_soliplex_project.py --print-defaults
   ```

   **A local Ollama on the Docker host needs extra wiring.** If
   `ollama_base_url` names `localhost`, `127.0.0.1`, or `::1`, the model probes
   below will pass — you run them on the host — but the generated stack will
   still fail: every container has its own network namespace, so the host's
   loopback is unreachable from `backend` and `haiku-ingester`. Catch this
   during the interview, not after the stack is up. Confirm what the server is
   bound to:

   ```bash
   ss -ltn | grep 11434   # '127.0.0.1:11434' = loopback only, unreachable
   ```

   If it is loopback-bound, **present the options rather than silently
   rewriting the URL**. Binding Ollama to `0.0.0.0` is not among them: that
   publishes an unauthenticated model server to every network the host is on.

   - **Relay sidecar (recommended)** — leave Ollama on loopback and add a
     `socat` service that republishes it on the docker bridge gateway. Nothing
     about the host's Ollama install changes, so it suits a systemd-managed
     one.
   - **Rebind Ollama** — `OLLAMA_HOST=<gateway>:11434` in its systemd unit. The
     host's `ollama` CLI then needs the same `OLLAMA_HOST` exported to find it.
   - **Use a different server** — a remote Ollama that is already reachable.

   For either of the first two, set `ollama_base_url` to
   `http://<gateway>:11434`, finding the gateway with `ip -4 addr show docker0`
   (conventionally `172.17.0.1`). **Keep probing the models over the loopback
   URL** — that is what works from the host; only the value written into
   `params.json` is the gateway address.

   `ollama_base_url` must carry a **bare IP, never a hostname**. Ollama's
   DNS-rebinding guard answers `403` with an empty body to any `Host` header
   that is not `localhost` or an IP literal, and a TCP relay forwards bytes
   without rewriting headers — so adding an `extra_hosts` alias to let services
   say `http://ollama:11434` breaks the stack rather than tidying it.

   The relay itself is a post-generation edit. Once the project is written, add
   to its `docker-compose.yml`:

   ```yaml
     ollama-relay:
       image: alpine/socat:latest
       network_mode: host      # required to reach the host's 127.0.0.1
       command:
         - "TCP-LISTEN:11434,bind=${OLLAMA_RELAY_BIND:-172.17.0.1},fork,reuseaddr"
         - "TCP:127.0.0.1:11434"
       restart: unless-stopped
   ```

   and set `OLLAMA_RELAY_BIND` to the same address in `.env`. The generated
   stack ships `docs/operations/ollama.md`, which covers all three options
   (including running Ollama inside the stack) — point the user at it.

   **Choosing and checking models.** Every model parameter resolves through the
   default Ollama provider at `ollama_base_url` — the template pins no other
   provider — so each chosen model must exist *and load* on that server. The
   chat-style model parameters are `chat_model`, `chat_model_alt`,
   `title_model`, and `rag_qa_model`; the embedding model
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
   # each chat-style model (chat_model, chat_model_alt, title_model, rag_qa_model)
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
   (see *Report the result* below). After generation, `soliplex-cli audit
   ollama --check-responsive` repeats the probe from inside the stack, against
   the resolved installation; the probe here catches a bad model before
   anything is written.

   The frontend version (`frontend_version`) and the backend `soliplex`
   release (`soliplex_image_tag`) are **independent choices**: the
   `soliplex/frontend` and `soliplex` repos release on their own schedules,
   so any frontend version can pair with any backend version.
   **Ask for them as two separate selections**, each with its own list of real
   versions (below). Never present paired/coupled front-end+back-end
   combinations; the user picks one value on each axis.

   For `frontend_version` (the tag of `ghcr.io/soliplex/frontend-web`, the published
   web client `nginx/Dockerfile` copies from), **offer the user real
   choices** instead of asking them to recall a tag: list the published tags
   (oldest first) and present the most recent:

   ```bash
   TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:soliplex/frontend-web:pull" | jq -r .token)
   TAGS=https://ghcr.io/v2/soliplex/frontend-web/tags/list
   curl -s -H "Authorization: Bearer $TOKEN" "$TAGS" | jq -r '.tags[]' | sort -V
   ```

   Explain the kinds: a `<major>.<minor>` tag (e.g. `0.117`, the default)
   moves, picking up patch releases on `docker compose build --pull nginx`;
   an exact `<major>.<minor>.<patch>` or `<major>.<minor>.<patch>-<build>`
   pins one; `latest` is also accepted. A `soliplex/frontend` *release* tag
   (`v0.117.0+106`) is not an image tag, and the generator refuses it, naming
   the image tag to use (`0.117.0-106`). Images exist only from 0.110 on.
   Fall back to the default if GHCR is unreachable.

   For the backend `soliplex` release (`soliplex_image_tag`: the tag of
   `ghcr.io/soliplex/soliplex-postgres`, which `backend/Dockerfile` builds
   `FROM`, and of `ghcr.io/soliplex/soliplex-tui` for the TUI), **offer the
   user real choices** instead of asking them to recall a tag: list the
   published tags (oldest first) and present the most recent:

   ```bash
   TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:soliplex/soliplex-postgres:pull" | jq -r .token)
   TAGS=https://ghcr.io/v2/soliplex/soliplex-postgres/tags/list
   curl -s -H "Authorization: Bearer $TOKEN" "$TAGS" | jq -r '.tags[]' | sort -V
   ```

   Explain the two kinds: a `<major>.<minor>` tag (e.g. `0.86`)
   moves, picking up patch releases on `docker compose build --pull`; an
   exact `<major>.<minor>.<patch>` pins one release. `latest` is also
   accepted. Fall back to the default if GHCR is unreachable.

   Then set `soliplex_dev_constraint` (the `soliplex` requirement in the
   stack's host-side `pyproject.toml` files) to a range that admits the
   release, e.g. `>= 0.88, < 0.89` for `0.88` or `0.88.1`; the default
   `>= 0.93, < 0.94` admits the default `0.93` tag. The generator refuses a
   tag outside the constraint.

2. **Write the answers to a JSON file** (omit keys to accept defaults), e.g.
   `params.json`:

   ```json
   { "project_name": "acme", "ollama_base_url": "http://ollama:11434",
     "nginx_http": 9100 }
   ```

3. **Run the generator:**

   ```bash
   uv run scripts/generate_soliplex_project.py --out <output_dir> --params params.json
   ```

   - Without `uv`: `pip install mako && python3 scripts/generate_soliplex_project.py --out <dir> --params params.json`
   - Add `--interactive` to be prompted on stdin instead of (or in addition to)
     a params file.
   - `generate_secrets.py` is run in the new project **by default**; add
     `--no-generate-secrets` to skip it (e.g. to generate secrets yourself
     later).
   - Add `--no-git` to skip the initial git commit, or `--disable-gpg-sign` if
     the host's commit-signing config would block a non-interactive commit.

4. **Report the result.** The script prints the chosen ports/models and the next
   steps. Relay those. Secrets are generated by default; if you passed
   `--no-generate-secrets`, the user must run
   `uv run scripts/generate_secrets.py` first.

   If you go on to bring the stack up yourself, **build the images explicitly
   first** rather than letting `up` build them implicitly: run
   `docker compose build`; add any OIDC providers the user asked for; run
   `docker compose run --rm --quiet-build stack-init`, which prepares the
   databases and runs the `stack-init/init.d/` setup steps; then
   `docker compose up -d`, then poll `docker compose ps` (or
   `docker compose logs -f`) until services report healthy. The nginx image copies the prebuilt, cache-busted web client
   from the published `ghcr.io/soliplex/frontend-web` image, so there is
   no Flutter build in the stack; a separate `build` step still keeps its
   output legible instead of hiding it inside `up`.

   Once the backend reports healthy, **verify the Ollama models the install
   actually references** with the backend's own audit, and pull any that are
   missing. This is the authoritative, config-driven check — it runs against the
   *resolved* installation, so it covers the models named in every referenced
   YAML file (and only those that resolve via Ollama), not just the ones you
   set in the interview:

   ```bash
   # per configured Ollama URL: ERROR (unreachable), MISSING, UNRESPONSIVE, or OK;
   # --check-responsive has each model answer on its role's endpoint
   # (chat on /v1/chat/completions, embeddings on /v1/embeddings)
   docker compose run --rm backend /app/.venv/bin/soliplex-cli audit ollama \
       --check-responsive /environment

   # if any URL reports MISSING, pull them (run with -n first to preview)
   docker compose run --rm backend /app/.venv/bin/soliplex-cli ollama pull /environment
   ```

   On success the audit prints only `OK` per URL, not the models it checked.
   To show the user which models (and roles) the install resolved, ask the
   backend — an admin-only endpoint, open to anyone while the stack has no
   OIDC provider (`stack-init` makes the no-auth user an admin):

   ```bash
   curl -s http://localhost:<nginx_http>/api/v1/installation/providers | jq
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
