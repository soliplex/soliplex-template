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

## By hand

You can also run the generator directly:

```bash
# Show every parameter and its default:
uv run skills/soliplex-template/scripts/generate_soliplex_project.py --print-defaults

# Generate into a new directory from a params file:
uv run skills/soliplex-template/scripts/generate_soliplex_project.py \
    --out ../my-stack --params params.json
```

`ollama_base_url` is the only value with no usable default; everything else
falls back to a sensible default. Useful flags:

- `--interactive` — prompt for each parameter on stdin.
- `--no-generate-secrets` — skip generating the stack's Docker secrets
  (generation runs `generate_secrets.py` by default).
- `--no-git` — skip the initial git commit.
- `--force` — allow writing into a non-empty `--out`.

## What the generated project adds

Beyond the stack itself, a generated project is an **installable Python
library**: it ships a `src/<package_name>/` package (a demo `tools.greeting`
tool and a `views.router` FastAPI router) plus a `tests/unit/` tree, and its
`pyproject.toml` declares a build backend. The backend reads the package over a
read-only `./src` bind mount on `PYTHONPATH`, and the Soliplex config references
it by dotted name — so you have a ready place to add custom tools, routers, and
configuration.

## Where to look

- **Parameters, defaults, and validation:**
  [`references/PARAMETERS.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/references/PARAMETERS.md)
- **Skill instructions:**
  [`SKILL.md`](https://github.com/soliplex/soliplex-template/blob/main/skills/soliplex-template/SKILL.md)

Once generated, follow [Installation](installation.md) from the secrets step
onward to bring your new stack up.
