#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["mako"]
# ///
"""Add a new room to an existing Soliplex stack from a bundled template.

A generated stack ships a fixed set of rooms; its ``room_paths`` list in
``backend/environment/installation.yaml`` is baked in at generation time. This
script grows that stack *after the fact*: it renders one of the skill's bundled
room templates (under ``assets/rooms/<name>/``) into
``backend/environment/rooms/<room_id>/room_config.yaml`` and splices
``./rooms/<room_id>`` into the installation's ``room_paths`` list.

It is pure filesystem work -- no Docker, no running backend. The backend serves
with ``--reload=config``, so a room added under ``backend/environment/`` is
picked up without an image rebuild or restart.

Run it from the skill directory (``uv run`` provisions Mako from the PEP 723
header above), pointing ``--project-dir`` at the stack::

    # list the bundled room templates
    python3 add_room.py list

    # add a conversational-RAG room called 'handbook'
    python3 add_room.py add --project-dir /path/to/stack \\
        --template chat --room-id handbook \\
        --name "Handbook" --description "Q&A over the staff handbook"

    # preview without writing
    python3 add_room.py add --template chat --room-id handbook --dry-run

The room template is rendered with Mako (mirroring the project generator), so
its comments -- including the commented-out examples for custom tools and for
filesystem / entrypoint skills -- carry through to the generated file for the
operator to uncomment and edit. The ``room_paths`` splice is line-based (not a
YAML round-trip), so comments and unrelated layout in ``installation.yaml`` are
preserved (mirrors ``rag_db.py``'s ``wire_room_stem``).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

from mako.template import Template

# Bundled room templates live beside this script under '<skill>/assets/rooms/'
# (this file is '<skill>/scripts/add_room.py'); resolve them relative to
# __file__ so the script works from an unpacked release bundle (no checkout).
TEMPLATES_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "assets" / "rooms"
)
TEMPLATE_FILE = "room_config.yaml.mako"
# The line a template carries to describe itself for 'list' (a Mako '##' line
# comment, so it never renders into the room_config.yaml).
_SUMMARY_PREFIX = "## summary:"

# A room id usable as a path segment and a YAML id: no '/', no '..', no
# leading dot (mirrors rag_db.py's DB_NAME_RE).
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

DEFAULT_AGENT_TEMPLATE = "default_chat"
DEFAULT_RAG_STEM = "haiku.rag"
# Placeholder when the stack's own package can't be inferred (no 'src/<pkg>/');
# the '<pkg>.tools.greeting' demo tool in the templates references it.
DEFAULT_PACKAGE_NAME = "your_package"
# Written into the room dir when --prompt-file is given; the config then points
# its system_prompt at this file (the 'search' demo room uses the same form).
PROMPT_FILE_NAME = "prompt.txt"

# Stack markers: the files that mark --project-dir as a generated stack.
COMPOSE_FILE = "docker-compose.yml"
ENVIRONMENT_DIR = pathlib.PurePosixPath("backend", "environment")
INSTALLATION_FILE = ENVIRONMENT_DIR / "installation.yaml"
ROOMS_DIR = ENVIRONMENT_DIR / "rooms"

# room_paths splice: the anchor line and the entry-already-present probe.
_ROOM_PATHS_RE = re.compile(r"^room_paths:\s*$")

ADDED = "added"
UNCHANGED = "unchanged"

# A starting system prompt per template, used when neither --system-prompt nor
# --prompt-file is given. Templates without an entry fall back to _GENERIC.
_GENERIC_PROMPT = "You are a helpful assistant."
_DEFAULT_PROMPTS = {
    "chat": (
        "You are a helpful agent.\n"
        "\n"
        "Use the room's tools and skills to respond to the user's "
        "requests.\n"
        "\n"
        "Never hallucinate -- use the 'rag' skill to ground your answers."
    ),
    "search": (
        "You are a knowledgeable assistant. For every question, FIRST call "
        "the 'search_documents' tool, then answer only from the results and "
        "cite the source documents. If the knowledge base does not contain "
        "the answer, say so plainly."
    ),
    "sandbox": (
        "You are a data-analysis assistant. Use the bubblewrap sandbox to "
        "read uploaded files and run Python, and the 'rag' skill for "
        "additional context. Delegate file reading and processing to the "
        "sandbox rather than pulling file contents into the conversation."
    ),
}


class AddRoomError(Exception):
    """A user-facing error (printed without a traceback).

    Message construction lives in these classmethod factories so call sites
    read ``raise AddRoomError.<reason>(...)`` with no inline message string.
    """

    @classmethod
    def compose_not_found(cls, path):
        return cls(
            f"no {COMPOSE_FILE} at {path} "
            "(run with --project-dir pointing at the stack directory)"
        )

    @classmethod
    def not_a_stack(cls, path):
        return cls(
            f"{path} is not a generated Soliplex stack: missing "
            f"'{INSTALLATION_FILE}'"
        )

    @classmethod
    def bad_room_id(cls, room_id):
        return cls(
            f"--room-id {room_id!r} must match {ROOM_ID_RE.pattern} "
            "(letters, digits, '.', '_', '-'; no leading dot)"
        )

    @classmethod
    def unknown_template(cls, name, available):
        avail = ", ".join(sorted(available)) or "(none found)"
        return cls(f"unknown --template {name!r}; available: {avail}")

    @classmethod
    def prompt_file_missing(cls, path):
        return cls(f"--prompt-file {path} does not exist")

    @classmethod
    def room_exists(cls, path):
        return cls(f"{path} already exists (use --force to overwrite it)")

    @classmethod
    def no_room_paths(cls, path):
        return cls(
            f"no 'room_paths:' block in {path} to extend "
            "(unexpected installation.yaml shape)"
        )


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
def _template_summary(text: str) -> str:
    """The template's ``## summary:`` line, or '' when it has none."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_SUMMARY_PREFIX):
            return stripped[len(_SUMMARY_PREFIX) :].strip()
    return ""


def available_templates() -> dict[str, str]:
    """Map each bundled template name to its one-line summary, sorted."""
    found = {}
    for mako in sorted(TEMPLATES_DIR.glob(f"*/{TEMPLATE_FILE}")):
        found[mako.parent.name] = _template_summary(mako.read_text())
    return found


def resolve_template(name: str) -> pathlib.Path:
    """Return the path to template ``name``'s mako file, or raise."""
    path = TEMPLATES_DIR / name / TEMPLATE_FILE
    if not path.is_file():
        raise AddRoomError.unknown_template(name, available_templates())
    return path


# --------------------------------------------------------------------------
# Rendering helpers (pure)
# --------------------------------------------------------------------------
def validate_room_id(room_id: str) -> None:
    if not ROOM_ID_RE.match(room_id):
        raise AddRoomError.bad_room_id(room_id)


def _dq(value: str) -> str:
    """Escape a string for a YAML double-quoted scalar."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_system_prompt(
    inline: str | None, prompt_file_name: str | None
) -> str:
    """Render the ``system_prompt:`` value for the room config.

    With ``prompt_file_name``, point at that sibling file (``"./prompt.txt"``).
    Otherwise emit ``inline`` as a YAML block scalar (``|``) indented to sit
    under the ``system_prompt:`` key; blank lines stay blank (no trailing
    whitespace).
    """
    if prompt_file_name is not None:
        return f'"./{prompt_file_name}"'
    body = "\n".join(
        f"    {line}" if line.strip() else "" for line in inline.splitlines()
    )
    return f"|\n{body}"


def render_room_config(template_path: pathlib.Path, ctx: dict) -> str:
    """Render a bundled room template with ``ctx`` (strict undefined)."""
    template = Template(filename=str(template_path), strict_undefined=True)
    return template.render(**ctx)


def build_context(
    args: argparse.Namespace,
    template: str,
    package_name: str = DEFAULT_PACKAGE_NAME,
) -> dict:
    """Assemble the Mako context for a room from the parsed args."""
    if args.prompt_file is not None:
        system_prompt = format_system_prompt(None, PROMPT_FILE_NAME)
    else:
        inline = args.system_prompt
        if inline is None:
            inline = _DEFAULT_PROMPTS.get(template, _GENERIC_PROMPT)
        system_prompt = format_system_prompt(inline, None)

    name = args.name if args.name is not None else args.room_id
    description = args.description if args.description is not None else name
    return {
        "room_id": args.room_id,
        "name": _dq(name),
        "description": _dq(description),
        "agent_template": _dq(args.agent_template),
        "rag_stem": _dq(args.rag_stem),
        "package_name": package_name,
        "system_prompt_yaml": system_prompt,
    }


def add_room_path(text: str, room_id: str) -> tuple[str, str]:
    """Splice ``- "./rooms/<room_id>"`` into the ``room_paths:`` list.

    Returns ``(new_text, action)`` where action is ``"unchanged"`` (the entry
    is already present) or ``"added"``. The edit is line-based, so comments and
    unrelated layout are preserved. Raises ``AddRoomError`` when the file has
    no top-level ``room_paths:`` block.
    """
    entry = f"./rooms/{room_id}"
    probe = re.compile(r'-\s*["\']?' + re.escape(entry) + r'["\']?\s*$')
    lines = text.splitlines(keepends=True)
    if any(probe.search(line) for line in lines):
        return text, UNCHANGED
    idx = next(
        (i for i, line in enumerate(lines) if _ROOM_PATHS_RE.match(line)),
        None,
    )
    if idx is None:
        raise AddRoomError.no_room_paths(INSTALLATION_FILE)
    lines.insert(idx + 1, f'  - "{entry}"\n')
    return "".join(lines), ADDED


# --------------------------------------------------------------------------
# Stack discovery
# --------------------------------------------------------------------------
def resolve_project(project_dir: str) -> pathlib.Path:
    project = pathlib.Path(project_dir).resolve()
    if not (project / COMPOSE_FILE).is_file():
        raise AddRoomError.compose_not_found(project / COMPOSE_FILE)
    if not (project / INSTALLATION_FILE).is_file():
        raise AddRoomError.not_a_stack(project)
    return project


def resolve_package_name(project: pathlib.Path, override: str | None) -> str:
    """The stack's own package (for ``<pkg>.tools.greeting``), or placeholder.

    Prefer an explicit ``override``; otherwise infer the single package under
    ``src/`` (the generator scaffolds ``src/<package_name>/tools.py``); failing
    that, return ``DEFAULT_PACKAGE_NAME`` for the operator to edit.
    """
    if override is not None:
        return override
    src = project / "src"
    if src.is_dir():
        packages = [
            child.name
            for child in sorted(src.iterdir())
            if child.is_dir() and (child / "tools.py").is_file()
        ]
        if len(packages) == 1:
            return packages[0]
    return DEFAULT_PACKAGE_NAME


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------
def do_list(args: argparse.Namespace) -> int:
    templates = available_templates()
    width = max((len(name) for name in templates), default=0)
    print("Bundled room templates:")
    for name, summary in templates.items():
        print(f"  {name.ljust(width)}  {summary}")
    return 0


def do_add(args: argparse.Namespace) -> int:
    project = resolve_project(args.project_dir)
    validate_room_id(args.room_id)
    template_path = resolve_template(args.template)

    prompt_text = None
    if args.prompt_file is not None:
        prompt_src = pathlib.Path(args.prompt_file)
        if not prompt_src.is_file():
            raise AddRoomError.prompt_file_missing(prompt_src)
        prompt_text = prompt_src.read_text()

    room_dir = project / ROOMS_DIR / args.room_id
    if room_dir.exists() and not args.force:
        raise AddRoomError.room_exists(room_dir)

    package_name = resolve_package_name(project, args.package_name)
    ctx = build_context(args, args.template, package_name)
    config_text = render_room_config(template_path, ctx)

    installation = project / INSTALLATION_FILE
    new_installation, path_action = add_room_path(
        installation.read_text(), args.room_id
    )

    config_path = room_dir / "room_config.yaml"
    if args.dry_run:
        _print_dry_run(config_path, config_text, path_action, args.room_id)
        return 0

    room_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text)
    if prompt_text is not None:
        (room_dir / PROMPT_FILE_NAME).write_text(prompt_text)
    if path_action != UNCHANGED:
        installation.write_text(new_installation)

    _print_summary(config_path, path_action, args.room_id, project)
    return 0


def _print_dry_run(
    config_path: pathlib.Path,
    config_text: str,
    path_action: str,
    room_id: str,
) -> None:
    print(f"# would write {config_path}:")
    print(config_text)
    print(f"# would add room_paths entry './rooms/{room_id}': {path_action}")


def _print_summary(
    config_path: pathlib.Path,
    path_action: str,
    room_id: str,
    project: pathlib.Path,
) -> None:
    print(f"✓ Added room {room_id!r}:")
    print(f"  - {config_path}: added")
    print(f"  - {INSTALLATION_FILE} room_paths: {path_action}")
    print()
    print("Next steps:")
    print(f"  - review/edit {config_path}")
    print(
        "  - the backend reloads config automatically (--reload=config); "
        "verify with:"
    )
    print(
        "      python3 scripts/soliplex_config.py rooms "
        f"--project-dir {project}"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add a room to an existing Soliplex stack."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser(
        "list", help="list the bundled room templates and their summaries"
    )
    listing.set_defaults(func=do_list)

    add = sub.add_parser(
        "add", help="render a room template into the stack and wire it up"
    )
    add.add_argument(
        "--project-dir",
        default=".",
        help="stack directory (default: current directory)",
    )
    add.add_argument(
        "--template",
        required=True,
        help="bundled template to render (see 'list')",
    )
    add.add_argument(
        "--room-id",
        required=True,
        help="id for the new room (also its directory and room_paths entry)",
    )
    add.add_argument("--name", help="room display name (default: --room-id)")
    add.add_argument(
        "--description", help="room description (default: the name)"
    )
    add.add_argument(
        "--agent-template",
        default=DEFAULT_AGENT_TEMPLATE,
        help=(
            "agent template_id from installation.yaml "
            f"(default: {DEFAULT_AGENT_TEMPLATE})"
        ),
    )
    add.add_argument(
        "--rag-stem",
        default=DEFAULT_RAG_STEM,
        help=(
            "LanceDB stem for the rag/search templates "
            f"(default: {DEFAULT_RAG_STEM})"
        ),
    )
    add.add_argument(
        "--package-name",
        default=None,
        help=(
            "the stack's own package, for '<pkg>.tools.greeting' (default: "
            "inferred from src/<pkg>/, else a placeholder)"
        ),
    )
    prompt = add.add_mutually_exclusive_group()
    prompt.add_argument(
        "--system-prompt", help="inline system prompt (overrides the default)"
    )
    prompt.add_argument(
        "--prompt-file",
        help="copy this file into the room as prompt.txt and reference it",
    )
    add.add_argument(
        "--force",
        action="store_true",
        help="overwrite the room directory if it already exists",
    )
    add.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rendered room and room_paths change; write nothing",
    )
    add.set_defaults(func=do_add)

    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except AddRoomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
