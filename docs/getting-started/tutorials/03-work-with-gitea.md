---
icon: lucide/git-pull-request
---

# Work with Gitea

Add a tool to the stack's own Python package and ship it through a pull request
— hosted by the Gitea that runs inside the stack itself. This continues from
[First steps](01-first-steps.md).

## Prerequisites

- The `soliplex-template` skill installed in your agent (see
  [First steps](01-first-steps.md)).
- `git` and an SSH key available (in your ssh-agent, or `~/.ssh/*.pub`) —
  `--push-to-gitea` registers it and pushes over SSH.

## 1. Generate a stack with Gitea

Ask your agent to generate a stack **with the Gitea service included**
(`include_gitea`). Then bring it up from the project directory:

```bash
docker compose up
```

## 2. Provision Gitea and back the repo

With the stack up, provision Gitea and point the project's git at it:

```bash
uv run scripts/init_gitea.py --admin-user yourname --push-to-gitea
```

This creates a web-UI admin user `yourname` (prompting for its password),
registers your SSH key, creates a repo, sets the project's git `origin` to it,
and pushes the initial commit. nginx serves the Gitea web UI at `/gitea/` on the
HTTPS port.

## 3. Start a branch and add a tool

Your project is an installable Python package; tools live in
`src/<package>/tools.py` (the `greeting` tool from
[First steps](01-first-steps.md) is there). On a new branch, add a sibling:

```bash
git checkout -b add-farewell
```

```python
# src/<package>/tools.py
def farewell(name: str) -> str:
    """Return a friendly farewell for ``name``."""
    return f"Goodbye, {name}! Come back soon."
```

The docstring matters: the LLM uses it as the tool's description.

## 4. Wire it into the `custom` room

Add the new tool to `backend/environment/rooms/custom/room_config.yaml`:

```yaml
tools:
  - tool_name: "<package>.tools.greeting"
  - tool_name: "<package>.tools.farewell"
```

The backend runs with `--reload=config`, so saving this config change restarts
it — which also re-imports your edited `tools.py`, picking up `farewell`. (If it
doesn't appear, `docker compose restart backend`.)

## 5. Test it

Open the **Custom Tool Demo** room and ask:

> Please say farewell to Ada.

The agent calls your new tool and replies:

> Goodbye, Ada! Come back soon.

## 6. Add a unit test

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

## 7. Commit and push the branch

```bash
git add src/<package>/tools.py tests/unit/test_tools.py \
    backend/environment/rooms/custom/room_config.yaml
git commit -m "Add a farewell tool (with a test) to the custom room"
git push -u origin add-farewell
```

The push goes to the in-stack Gitea over SSH (the remote `init_gitea.py` set).

## 8. Open a pull request

Sign in to the Gitea web UI (the `/gitea/` path on the HTTPS port) as the admin
user from step 2. Gitea shows the freshly pushed `add-farewell` branch and
offers to open a pull request against `main`; create it.

## 9. Merge it

Review the diff in Gitea and merge the pull request from the web UI. Back in the
project, `git checkout main && git pull` brings the merged change down — your
stack now runs code you shipped through its own review workflow.

## Where next

The next tutorial, *concierge room*, lets users request new rooms that land as
Gitea issues for an administrator to fulfil.
