---
icon: lucide/wand-sparkles
---

# Generate a project

You don't run this template repository directly — you generate your **own**
Soliplex stack from it, then run and operate that generated project. A generated
project gets its own project name, host ports, model choices, auth mode, and an
installable Python package wired into the backend.

There are two ways to generate, both driven by the same generator
(`skills/soliplex-template/scripts/generate_soliplex_project.py`, which renders
the embedded Mako template under `skills/soliplex-template/assets/template/`
into a fresh, runnable directory):

## With the Agent Skill (usual path)

In an agent that has the `soliplex-template` skill available, ask it to create a
stack — e.g. *"generate a new Soliplex stack"*. The skill prompts for the
parameters it needs (at minimum your Ollama URL) and runs the generator for you.
See [Working with the skill](#working-with-the-skill) below for answering its
questions up front, what it checks, and what it asks you to decide; once the
stack exists, [Using the skill](using-the-skill.md) covers what else it can do.

## By hand

You can also run the generator directly:

```bash
# Show every parameter and its default:
uv run skills/soliplex-template/scripts/generate_soliplex_project.py --print-defaults

# Generate into a new directory from a params file:
uv run skills/soliplex-template/scripts/generate_soliplex_project.py \
    --out ../my-stack --params params.json

# Or create it inside a directory that collects your stacks; the project
# directory is named for project_name:
uv run skills/soliplex-template/scripts/generate_soliplex_project.py \
    --parent ~/stacks --params params.json
```

`ollama_base_url` is the only value with no usable default; everything else
falls back to a sensible default. Useful flags:

- `--interactive` — prompt for each parameter on stdin.
- `--no-generate-secrets` — skip generating the stack's Docker secrets
  (generation runs `generate_secrets.py` by default).
- `--no-git` — skip the initial git commit.
- `--parent DIR` — create the project *inside* `DIR` rather than treating the
  target as the project directory itself. Mutually exclusive with `--out`.
- `--force` — allow writing into a non-empty target directory (with
  `--parent`, that means the project directory, not the parent).

## What the generated project adds

Beyond the stack itself, a generated project ships an **installable Python
library** of its own, as a project directory under `src/`:

```text
src/
  <package_name>/         # this project
    pyproject.toml
    src/<package_name>/   # a demo tools.greeting and views.router
    tests/unit/
```

Every entry under `src/` has that shape, so `cd src/<X> && uv sync && uv run
pytest` works the same for this project and for any repo the stack owner later
clones alongside it (the `soliplex` checkout, a third-party dependency). The
backend mounts the whole `src/` tree read-only and puts *this* project's
package directory on `PYTHONPATH`, and the Soliplex config references it by
dotted name — so you have a ready place to add custom tools, routers, and
configuration.

An existing stack generated before this layout is converted by the skill's
`scripts/migrate_layout.py`: see [Move to the `src/` project
layout](../upgrades/move-to-src-layout.md), rung 0 of
[Upgrading an existing stack](../upgrades/index.md).

## Working with the skill

Asked for a new stack, the agent interviews you, grouping related settings
into one question each: location, project name, Ollama URL, host ports, auth
mode, models, frontend and backend versions, the optional Gitea service, the
optional code-execution sandbox, and whether to generate secrets and make an
initial git commit.

### Answering up front

To skip questions, put the answers on the command that invokes the skill:

```text
/soliplex-template in=~/stacks project=acme ollama=http://192.168.1.20:11434 ports=default models=default
```

- `key=value` sets one setting. `key` is a generator parameter name (see
  [`references/PARAMETERS.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/references/PARAMETERS.md))
  or one of the shorter aliases listed below.
- `where=DIR` makes `DIR` the project directory, like `--out`; `in=DIR`
  creates the project *inside* `DIR`, like `--parent`. Use one or the other.
- `ports=default` or `models=default` accepts the defaults for that whole group
  and skips its question.
- `force`, `git=no`, and `secrets=no` are the `--force`, `--no-git`, and
  `--no-generate-secrets` flags.

A fully specified command runs without questions; a bare `/soliplex-template`
asks them all. The agent asks about any token it does not recognize rather than
guessing.

??? note "The full alias and group vocabulary"

    ```json
    --8<-- "skills/soliplex-template/assets/aliases.json"
    ```

### What it checks before writing anything

- **Models.** The agent lists the models your Ollama server actually has and
  offers those, then sends each chosen model a one-token request, even with
  `models=default`. A model that is missing or won't load is caught here, and
  the agent offers to switch to another or pull it.
- **A loopback-only Ollama.** If your Ollama URL names `localhost` or
  `127.0.0.1`, the containers will not be able to reach it, even though the
  model checks pass from the host. The agent explains your options (a relay
  container, rebinding Ollama to the Docker bridge, or another server); see
  [Local Ollama](../users/operations/ollama.md).
- **Versions.** It lists the published frontend and backend image tags and asks
  for each separately; any frontend version works with any backend version.
- **The project name.** `soliplex` is reserved (in any capitalization); the
  agent suggests the default `soliplex-dojo` instead.

If you then ask it to bring the stack up, it builds the images, starts the
services, waits for them to report healthy, and re-checks the models from
inside the stack against the full resolved configuration.

### Decisions it leaves to you

- **Secrets.** No setting is a secret; the stack's secrets are generated into
  `.secrets/`. If you type something secret-looking on the command line, the
  agent treats it as a mistake, not as input.
- **Exposing Ollama.** It will not suggest binding Ollama to `0.0.0.0`, which
  would publish an unauthenticated model server to every network the host is
  on, and it will not rewrite your Ollama URL without asking.

## Where to look

- **Parameters, defaults, and validation:**
  [`references/PARAMETERS.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/references/PARAMETERS.md)
- **Skill instructions:**
  [`SKILL.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/SKILL.md)

Once generated, follow [Installation](../users/installation.md) from the secrets step
onward to bring your new stack up.
