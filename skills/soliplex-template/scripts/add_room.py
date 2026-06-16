#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-plumber>=0.1", "mako"]
# ///
"""Add a new room to an existing Soliplex stack from a bundled template.

This is the skill's CLI front end. It owns the bundled room templates (under
``assets/rooms/<name>/``) and renders the chosen one with Mako. The
stack-level work -- writing the room and wiring ``room_paths`` -- is left to
``soliplex_plumber.rooms``.

Run it with ``uv run``, which provisions the PEP 723 dependencies above::

    # list the bundled room templates
    uv run add_room.py list

    # add a conversational-RAG room called 'handbook'
    uv run add_room.py add --project-dir /path/to/stack \\
        --template chat --room-id handbook \\
        --name "Handbook" --description "Q&A over the staff handbook"

    # preview without writing
    uv run add_room.py add --template chat --room-id handbook --dry-run

Mako renders the template's comments through to the generated config -- the
commented-out tool and skill examples included -- for the operator to edit.
``room_paths`` is left alone when it already points at ``./rooms`` (which
auto-discovers the new room); installs that enumerate rooms instead get
``- "./rooms/<room-id>"`` spliced in.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from mako import template as mako_template
from soliplex_plumber import rooms

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

DEFAULT_AGENT_TEMPLATE = "default_chat"
DEFAULT_RAG_STEM = "haiku.rag"

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


# --------------------------------------------------------------------------
# Templates (skill-owned)
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


# Two ``AddRoomError`` messages the template-agnostic core deliberately omits:
# templates and prompt files are skill concerns, so the wording lives here.
# They return the core's ``AddRoomError`` so ``main``'s top-level handler (and
# ``install_room``'s own errors) catch them uniformly.
def _unknown_template_error(name: str, available) -> rooms.AddRoomError:
    avail = ", ".join(sorted(available)) or "(none found)"
    return rooms.AddRoomError(f"unknown template {name!r}; available: {avail}")


def _prompt_file_missing_error(path) -> rooms.AddRoomError:
    return rooms.AddRoomError(f"prompt file {path} does not exist")


def resolve_template(name: str) -> pathlib.Path:
    """Return the path to template ``name``'s mako file, or raise."""
    path = TEMPLATES_DIR / name / TEMPLATE_FILE
    if not path.is_file():
        raise _unknown_template_error(name, available_templates())
    return path


# --------------------------------------------------------------------------
# Rendering (skill-owned)
# --------------------------------------------------------------------------
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
    template = mako_template.Template(
        filename=str(template_path),
        strict_undefined=True,
    )
    return template.render(**ctx)


def build_context(
    args: argparse.Namespace,
    template: str,
    package_name: str = rooms.DEFAULT_PACKAGE_NAME,
) -> dict:
    """Assemble the Mako context for a room from the parsed args."""
    if args.prompt_file is not None:
        system_prompt = format_system_prompt(None, rooms.PROMPT_FILE_NAME)
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
    project = rooms.resolve_project(args.project_dir)
    rooms.validate_room_id(args.room_id)
    template_path = resolve_template(args.template)

    prompt_text = None
    if args.prompt_file is not None:
        prompt_src = pathlib.Path(args.prompt_file)
        if not prompt_src.is_file():
            raise _prompt_file_missing_error(prompt_src)
        prompt_text = prompt_src.read_text()

    package_name = rooms.resolve_package_name(project, args.package_name)
    ctx = build_context(args, args.template, package_name)
    config_text = render_room_config(template_path, ctx)

    result = rooms.install_room(
        project,
        args.room_id,
        config_text=config_text,
        prompt_text=prompt_text,
        force=args.force,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        _print_dry_run(
            result.config_path, config_text, result.path_action, args.room_id
        )
    else:
        _print_summary(
            result.config_path, result.path_action, args.room_id, project
        )
    return 0


def _print_dry_run(
    config_path: pathlib.Path,
    config_text: str,
    path_action: str,
    room_id: str,
) -> None:
    print(f"# would write {config_path}:")
    print(config_text)
    print(f"# room_paths './rooms/{room_id}': {path_action}")


def _print_summary(
    config_path: pathlib.Path,
    path_action: str,
    room_id: str,
    project: pathlib.Path,
) -> None:
    print(f"✓ Added room {room_id!r}:")
    print(f"  - {config_path}: added")
    print(f"  - {rooms.INSTALLATION_FILE} room_paths: {path_action}")
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
    except rooms.AddRoomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
