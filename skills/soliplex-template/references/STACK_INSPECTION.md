# Inspecting a running stack

Loads when: Load this reference to reason about what a running stack is actually configured to do, or to check/repair the Ollama models its install references.

## Inspecting a running stack's config

When you need to reason about what a *running* stack is actually configured to
do — rather than re-reading the `backend/environment/` source — query its
**resolved** installation config with `scripts/soliplex_config.py`, run from the
stack directory. It runs `soliplex-cli config` inside a one-off `backend`
container (`docker compose run --rm`) and parses the YAML it emits, so it
reflects defaults, secret/env substitution, and `room_paths` resolution exactly
as the backend sees them.

```bash
uv run scripts/soliplex_config.py show                 # whole resolved config
uv run scripts/soliplex_config.py get room_paths        # one value (dotted path)
uv run scripts/soliplex_config.py get room_paths.0      # list index
uv run scripts/soliplex_config.py rooms                 # {room_id, name, description} per loaded room
uv run scripts/soliplex_config.py room chat             # full room_config.yaml of one room by id
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

The implementation lives in the `soliplex-plumber` package (the script is a thin
wrapper); `uv run scripts/soliplex_config.py …` installs it from the script's
inline metadata, so run it that way (a bare `python3` needs `soliplex-plumber`
already installed).

### Checking and repairing Ollama models

The model names a running stack uses all resolve through the Ollama server at
`OLLAMA_BASE_URL`; if that URL changes (edited in `.env`) or the server loses
models (a flush, a cache eviction), requests will start failing. Check the
install's referenced models against each configured server — and pull back any
that went missing — with the backend's own audit, run from the stack directory
in a one-off `backend` container the same way as `soliplex_config.py`:

```bash
# per configured Ollama URL: ERROR, MISSING, UNRESPONSIVE, or OK
docker compose run --rm backend /app/.venv/bin/soliplex-cli audit ollama \
    --check-responsive /environment

# pull whatever the install references (-n previews; -u URL scopes to one server)
docker compose run --rm backend /app/.venv/bin/soliplex-cli ollama pull /environment
```

`audit ollama` exits non-zero when a server is unreachable or missing models
(or, with `--check-responsive`, has a model that fails to answer),
and with `-q` (placed before the `ollama` subcommand) prints a JSON error
report, so it is scriptable. Both commands need the stack's images built and
should be run from the directory holding `docker-compose.yml`.
