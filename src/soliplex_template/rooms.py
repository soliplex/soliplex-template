"""Generic, template-agnostic logic for adding a room to a Soliplex stack.

This is the reusable core behind the ``soliplex-template`` skill's bundled
``add_room.py`` -- a PEP 723 shim that owns the ``.mako`` templates and the
CLI and delegates the stack-level work here. It ships in the published
``soliplex-template`` distribution so the skill (and any other consumer) can
``from soliplex_template.rooms import ...``.

It works on a *rendered* room config (text the caller produced however it likes
-- a template, an existing room's config, or built by hand) plus the stack's
``installation.yaml``, which it edits line-based (comment-preserving):

- ``resolve_project`` / ``resolve_package_name`` -- locate + introspect it.
- ``validate_room_id`` -- the room-id / path-segment rule.
- ``add_room_path`` -- ensure ``room_paths`` loads the room (``added`` /
  ``COVERED`` by a ``./rooms`` parent entry / ``unchanged`` when already
  listed), preserving comments and layout.
- ``install_room`` -- write the room dir + config (+ optional prompt file) and
  apply the ``room_paths`` edit; honors dry-run and force.

Pure filesystem work -- no Docker, no running backend, stdlib only.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

# A room id usable as a path segment and a YAML id: no '/', no '..', no
# leading dot (mirrors rag_db.py's DB_NAME_RE).
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Placeholder when the stack's own package can't be inferred (no 'src/<pkg>/');
# the '<pkg>.tools.greeting' demo tool in the skill templates references it.
DEFAULT_PACKAGE_NAME = "your_package"
# Written into the room dir when a prompt file is supplied; the config then
# points its system_prompt at this file (the 'search' demo room uses the form).
PROMPT_FILE_NAME = "prompt.txt"

# Stack markers: the files that mark a directory as a generated stack.
COMPOSE_FILE = "docker-compose.yml"
ENVIRONMENT_DIR = pathlib.PurePosixPath("backend", "environment")
INSTALLATION_FILE = ENVIRONMENT_DIR / "installation.yaml"
ROOMS_DIR = ENVIRONMENT_DIR / "rooms"

# room_paths splice: the anchor line and the entry-already-present probe.
_ROOM_PATHS_RE = re.compile(r"^room_paths:\s*$")

ADDED = "added"
UNCHANGED = "unchanged"
# room_paths may point at the rooms parent directory to auto-discover every
# room beneath it; when it does, a new room needs no room_paths entry.
ROOMS_PARENT_ENTRY = "./rooms"
COVERED = f'covered by "{ROOMS_PARENT_ENTRY}"'


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
            f"room id {room_id!r} must match {ROOM_ID_RE.pattern} "
            "(letters, digits, '.', '_', '-'; no leading dot)"
        )

    @classmethod
    def unknown_template(cls, name, available):
        avail = ", ".join(sorted(available)) or "(none found)"
        return cls(f"unknown template {name!r}; available: {avail}")

    @classmethod
    def prompt_file_missing(cls, path):
        return cls(f"prompt file {path} does not exist")

    @classmethod
    def room_exists(cls, path):
        return cls(f"{path} already exists (use force to overwrite it)")

    @classmethod
    def no_room_paths(cls, path):
        return cls(
            f"no 'room_paths:' block in {path} to extend "
            "(unexpected installation.yaml shape)"
        )


def validate_room_id(room_id: str) -> None:
    if not ROOM_ID_RE.match(room_id):
        raise AddRoomError.bad_room_id(room_id)


def resolve_project(project_dir: str) -> pathlib.Path:
    """Return the resolved stack root, or raise if it is not a stack."""
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


def add_room_path(text: str, room_id: str) -> tuple[str, str]:
    """Ensure ``room_paths`` loads ``rooms/<room_id>``; return (text, action).

    Action is ``"unchanged"`` when the explicit entry is already listed,
    ``COVERED`` when a ``./rooms`` entry already auto-discovers every room
    beneath it (so no entry is needed), or ``"added"`` when the
    ``- "./rooms/<id>"`` entry is spliced in. The edit is line-based, so
    comments and unrelated layout are preserved. Raises ``AddRoomError`` when
    the file has no top-level ``room_paths:`` block.
    """
    entry = f"{ROOMS_PARENT_ENTRY}/{room_id}"
    probe = re.compile(r'-\s*["\']?' + re.escape(entry) + r'["\']?\s*$')
    parent_probe = re.compile(
        r'-\s*["\']?' + re.escape(ROOMS_PARENT_ENTRY) + r'/?["\']?\s*$'
    )
    lines = text.splitlines(keepends=True)
    if any(probe.search(line) for line in lines):
        return text, UNCHANGED
    if any(parent_probe.search(line) for line in lines):
        return text, COVERED
    idx = next(
        (i for i, line in enumerate(lines) if _ROOM_PATHS_RE.match(line)),
        None,
    )
    if idx is None:
        raise AddRoomError.no_room_paths(INSTALLATION_FILE)
    lines.insert(idx + 1, f'  - "{entry}"\n')
    return "".join(lines), ADDED


@dataclasses.dataclass(frozen=True)
class RoomInstall:
    """The outcome of ``install_room``: where the config went + the room_paths
    action (``added`` / ``COVERED`` / ``unchanged``)."""

    config_path: pathlib.Path
    path_action: str


def install_room(
    project: pathlib.Path,
    room_id: str,
    *,
    config_text: str,
    prompt_text: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> RoomInstall:
    """Install a rendered room into ``project``; return a ``RoomInstall``.

    Writes ``rooms/<room_id>/room_config.yaml`` (and ``prompt.txt`` when
    ``prompt_text`` is given), and ensures ``room_paths`` loads it. With
    ``dry_run`` it computes the outcome but writes nothing. Raises
    ``AddRoomError`` when the room directory already exists and ``force`` is
    false. ``config_text`` is template-agnostic -- any caller-produced room
    config.
    """
    room_dir = project / ROOMS_DIR / room_id
    config_path = room_dir / "room_config.yaml"
    if room_dir.exists() and not force:
        raise AddRoomError.room_exists(room_dir)

    installation = project / INSTALLATION_FILE
    new_installation, path_action = add_room_path(
        installation.read_text(), room_id
    )

    if not dry_run:
        room_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_text)
        if prompt_text is not None:
            (room_dir / PROMPT_FILE_NAME).write_text(prompt_text)
        if path_action == ADDED:
            installation.write_text(new_installation)

    return RoomInstall(config_path=config_path, path_action=path_action)
