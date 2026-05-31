---
icon: lucide/wand-sparkles
---

# Generate a custom project

Cloning the template gives you the stack with its default project name, ports,
models, and version pins. If you instead want a **tailored** project — your own
name, host ports, model choices, auth mode, and an installable Python package
wired into the backend — use the bundled `soliplex-template` **Agent Skill**.

The skill drives `skill/scripts/generate_soliplex_project.py`, which renders the
embedded template (a Mako tree under `skill/assets/template/`) into a fresh,
runnable project directory.

## At a glance

```bash
# Show every parameter and its default:
uv run skill/scripts/generate_soliplex_project.py --print-defaults

# Generate into a new directory from a params file:
uv run skill/scripts/generate_soliplex_project.py --out ../my-stack --params params.json
```

`ollama_base_url` is the only value with no usable default; everything else
falls back to a sensible default. Useful flags:

- `--interactive` — prompt for each parameter on stdin.
- `--run-secrets` — also run `generate-secrets.sh` in the new project.
- `--no-git` — skip the initial git commit.

## What the generated project adds

Beyond the verbatim stack, a generated project is an **installable Python
library**: it ships a `src/<package_name>/` package (a demo `tools.greeting`
tool and a `views.router` FastAPI router) plus a `tests/unit/` tree, and its
`pyproject.toml` declares a build backend. The backend reads the package over a
read-only `./src` bind mount on `PYTHONPATH`, and the Soliplex config references
it by dotted name — so you have a ready place to add custom tools, routers, and
configuration.

## Where to look

- **Parameters, defaults, and validation:**
  [`skill/references/PARAMETERS.md`](https://github.com/soliplex/soliplex-template/blob/main/skill/references/PARAMETERS.md)
- **Skill instructions:**
  [`skill/SKILL.md`](https://github.com/soliplex/soliplex-template/blob/main/skill/SKILL.md)

Once generated, the new project runs exactly like a clone — follow
[Installation](installation.md) from the secrets step onward.
