---
icon: lucide/hammer
---

# Add a custom tool

Extend a running stack with your own tool: add it to the project's Python
package, wire it into a room, exercise it, and cover it with a test. This
continues from [First steps](01-first-steps.md).

## Prerequisites

- A generated stack, brought up (see [First steps](01-first-steps.md)).
- `git` and [`uv`](https://docs.astral.sh/uv/).

## 1. Add a tool

Your project is an installable Python package; tools live in
`src/<package>/tools.py` (the `greeting` tool from
[First steps](01-first-steps.md) is there). Add a sibling:

```python
# src/<package>/tools.py
def farewell(name: str) -> str:
    """Return a friendly farewell for ``name``."""
    return f"Goodbye, {name}! Come back soon."
```

The docstring matters: the LLM uses it as the tool's description.

## 2. Wire it into the `custom` room

Add the new tool to `backend/environment/rooms/custom/room_config.yaml`:

```yaml
tools:
  - tool_name: "<package>.tools.greeting"
  - tool_name: "<package>.tools.farewell"
```

The backend runs with `--reload=config`, so saving this config change restarts
it — which also re-imports your edited `tools.py`, picking up `farewell`. (If it
doesn't appear, `docker compose restart backend`.)

## 3. Test it in the room

Open the **Custom Tool Demo** room and ask:

> Please say farewell to Ada.

The agent calls your new tool and replies:

> Goodbye, Ada! Come back soon.

## 4. Add a unit test

The generated project is also a normal Python package with a test suite already
wired up: `tests/unit/test_tools.py` covers `greeting`. Add a case for
`farewell` beside it (`tools` is already imported at the top of that file):

```python
# tests/unit/test_tools.py
def test_farewell_includes_name():
    result = tools.farewell("Ada")

    assert "Ada" in result
```

Create the dev environment once, then run the suite:

```bash
uv sync          # installs the dev dependencies, incl. pytest
uv run pytest
```

No setup is needed: `pyproject.toml` already declares `pytest`, puts `src/` on
the path, and points `testpaths` at `tests/unit/`, so your new test is collected
and passes.

## 5. Commit it

```bash
git add src/<package>/tools.py tests/unit/test_tools.py \
    backend/environment/rooms/custom/room_config.yaml
git commit -m "Add a farewell tool (with a test) to the custom room"
```

## Where next

The next tutorial, [Work with Gitea](04-work-with-gitea.md), ships a change like
this one through a pull request hosted by the stack's own Gitea.
