#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Query a running Soliplex stack's resolved installation config.

``soliplex-cli config <installation>`` exports the *resolved* installation
config as YAML. ``soliplex-cli`` only exists inside the backend image, so this
helper runs it in a one-off backend container via ``docker compose run --rm``
and parses the YAML output. The subcommands expose that config at three levels
of granularity:

``show``
    Print the whole resolved config (the faithful ``soliplex-cli config``
    output, banner and all).

``get <key>``
    Print a single value addressed by a dotted path into the parsed config,
    e.g. ``room_paths`` (a list), ``room_paths.0`` (list index), or
    ``installation.name`` (nested key). Scalars print bare and list-of-scalars
    print one per line, so the output is shell-friendly; pass ``--format yaml``
    to dump any value (including nested structures) as YAML.

``rooms``
    A convenience over ``get room_paths``: print one
    ``{room_id, name, description}`` mapping (as YAML) for every room the
    installation actually loads. It is driven by the resolved ``room_paths`` --
    so unlike a ``rooms/*`` glob it honors installations that limit their room
    set or point ``room_paths`` at shared directories. ``room_paths`` come back
    as the backend container's absolute paths; each is mapped back to the host
    through the backend's ``<host-environment> -> <installation>`` bind mount,
    and the ``id``, ``name``, and ``description`` of every ``room_config.yaml``
    found beneath it (a directory may hold a single room or a tree of them) are
    read on the host. Rooms whose resolved path lies outside ``--installation``
    (an exotic shared mount) cannot be mapped to a host file; they are reported
    on stderr and skipped rather than silently dropped.

``room <room_id>``
    Print the full ``room_config.yaml`` (verbatim, comments and all) of the
    one loaded room whose ``id`` is ``room_id``, resolved the same way as
    ``rooms``. Errors if no loaded room has that id.

    python3 soliplex_config.py show       --project-dir /path/to/stack
    python3 soliplex_config.py get room_paths --project-dir /path/to/stack
    python3 soliplex_config.py rooms      --project-dir /path/to/stack
    python3 soliplex_config.py room chat  --project-dir /path/to/stack
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

import yaml

# The stack service that ships soliplex-cli, the binary's in-container path
# (the compose command launches it by absolute path -- the venv is not on
# PATH), and the installation path it serves. ``DEFAULT_HOST_ENVIRONMENT`` is
# the host directory bind-mounted onto ``DEFAULT_INSTALLATION`` there.
DEFAULT_SERVICE = "backend"
DEFAULT_CLI = "/app/.venv/bin/soliplex-cli"
DEFAULT_INSTALLATION = "/environment"
DEFAULT_HOST_ENVIRONMENT = "backend/environment"
# A wide terminal so rich (soliplex-cli's console) does not wrap long paths in
# the captured, non-TTY output and corrupt the YAML.
_WIDE_COLUMNS = "10000"
# Fields lifted from each room_config.yaml into a `rooms` mapping, output key
# (left) <- room_config.yaml key (right). ``room_id`` is required; the rest are
# null when absent.
_ROOM_FIELDS = (
    ("room_id", "id"),
    ("name", "name"),
    ("description", "description"),
)


class SoliplexConfigError(Exception):
    """A user-facing error (printed without a traceback)."""


class DockerMissing(SoliplexConfigError):
    def __init__(self):
        super().__init__(
            "docker not found on PATH (the Docker CLI is required)"
        )


class ComposeNotFound(SoliplexConfigError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"no docker-compose.yml at {path} "
            "(run from the stack directory or pass --project-dir)"
        )


class NoRoomPaths(SoliplexConfigError):
    def __init__(self):
        super().__init__(
            "soliplex-cli config output has no 'room_paths' "
            "(unexpected config shape -- is the backend service healthy?)"
        )


class KeyNotFound(SoliplexConfigError):
    def __init__(self, key):
        self.key = key
        super().__init__(
            f"no key {key!r} in the resolved installation config "
            "(use 'show' to inspect the available keys)"
        )


class RoomNotFound(SoliplexConfigError):
    def __init__(self, room_id):
        self.room_id = room_id
        super().__init__(
            f"no room with id {room_id!r} among the loaded rooms "
            "(use 'rooms' to list them)"
        )


def _require_docker() -> None:
    if shutil.which("docker") is None:
        raise DockerMissing()


def resolve_project(project_dir: str) -> pathlib.Path:
    project = pathlib.Path(project_dir).resolve()
    compose = project / "docker-compose.yml"
    if not compose.is_file():
        raise ComposeNotFound(compose)
    return project


def run_config(
    project: pathlib.Path, service: str, cli: str, installation: str
) -> str:
    """Run ``soliplex-cli config`` in a one-off backend container."""
    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-TTY",
        "-e",
        f"COLUMNS={_WIDE_COLUMNS}",
        service,
        cli,
        "config",
        installation,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def parse_config(stdout: str) -> dict:
    """Parse the YAML body of ``soliplex-cli config`` output.

    The export is prefixed with a ``#`` comment banner; YAML treats those as
    comments, so the whole stream loads directly.
    """
    loaded = yaml.safe_load(stdout)
    return loaded if isinstance(loaded, dict) else {}


def navigate(config: dict, key: str):
    """Resolve a dotted ``key`` into ``config``, or raise ``KeyNotFound``.

    Each ``.``-separated segment indexes a mapping by name or a sequence by
    integer position. Descending past a scalar, an unknown mapping key, a
    non-integer sequence index, or an out-of-range index all raise.
    """
    current = config
    for part in key.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyNotFound(key)
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                raise KeyNotFound(key) from None
        else:
            raise KeyNotFound(key)
    return current


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _format_scalar(value) -> str:
    """Render a scalar the YAML way (``null``/``true``/``false``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_value(value, fmt: str) -> str:
    """Render ``navigate``'s result for printing.

    ``yaml`` dumps any value as YAML. ``plain`` (the default) prints a scalar
    bare and a list of scalars one per line -- shell-friendly -- and falls back
    to a YAML dump for anything nested.
    """
    if fmt == "yaml":
        return yaml.safe_dump(
            value, sort_keys=False, allow_unicode=True
        ).rstrip("\n")
    if _is_scalar(value):
        return _format_scalar(value)
    if isinstance(value, list) and all(_is_scalar(item) for item in value):
        return "\n".join(_format_scalar(item) for item in value)
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip(
        "\n"
    )


def map_to_host(
    container_path: str, installation: str, host_environment: pathlib.Path
) -> pathlib.Path | None:
    """Map an in-container room path to its host location, or None.

    Returns None when ``container_path`` is not under ``installation`` (the
    bind mount we know about), e.g. a room path into an unrelated shared mount.
    """
    cpath = pathlib.PurePosixPath(container_path)
    try:
        rel = cpath.relative_to(pathlib.PurePosixPath(installation))
    except ValueError:
        return None
    return host_environment.joinpath(*rel.parts)


def find_room_configs(room_dir: pathlib.Path) -> list[pathlib.Path]:
    """room_config.yaml files under ``room_dir`` (mirrors soliplex).

    A path that directly holds ``room_config.yaml`` is a single room;
    otherwise its immediate (non-hidden) subdirectories are scanned. A path
    that does not exist (listed in room_paths but absent) yields nothing.
    """
    direct = room_dir / "room_config.yaml"
    if direct.is_file():
        return [direct]
    if not room_dir.is_dir():
        return []
    configs = []
    for sub in sorted(room_dir.glob("*")):
        if sub.name.startswith("."):
            continue
        cfg = sub / "room_config.yaml"
        if cfg.is_file():
            configs.append(cfg)
    return configs


def read_room_meta(text: str) -> dict | None:
    """A room_config.yaml's ``{room_id, name, description}``, or None.

    Returns None when the document is not a mapping or has no (truthy) ``id``.
    ``name``/``description`` default to None when the room omits them.
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return {out: data.get(src) for out, src in _ROOM_FIELDS}


def _resolve_room_configs(
    project: pathlib.Path,
    service: str,
    cli: str,
    installation: str,
    host_environment: str,
) -> tuple[list[tuple[dict, pathlib.Path]], list[str]]:
    """Locate the loaded rooms' host ``room_config.yaml`` files.

    Returns ``(entries, unmapped_container_paths)`` where ``entries`` is a list
    of ``(meta, path)`` in ``room_paths`` order -- ``meta`` is the room's
    ``{room_id, name, description}`` and ``path`` its host config file. Room id
    conflicts resolve first-past-the-post, matching soliplex.
    """
    config = parse_config(run_config(project, service, cli, installation))
    if "room_paths" not in config:
        raise NoRoomPaths()

    host_env = (project / host_environment).resolve()
    entries: list[tuple[dict, pathlib.Path]] = []
    seen: set[str] = set()
    unmapped: list[str] = []
    for container_path in config["room_paths"]:
        host_dir = map_to_host(container_path, installation, host_env)
        if host_dir is None:
            unmapped.append(container_path)
            continue
        for cfg in find_room_configs(host_dir):
            meta = read_room_meta(cfg.read_text())
            if meta and meta["room_id"] not in seen:
                seen.add(meta["room_id"])
                entries.append((meta, cfg))
    return entries, unmapped


def resolve_rooms(
    project: pathlib.Path,
    service: str,
    cli: str,
    installation: str,
    host_environment: str,
) -> tuple[list[dict], list[str]]:
    """Collect the loaded rooms' ``{room_id, name, description}`` mappings.

    Returns ``(rooms, unmapped_container_paths)`` where ``rooms`` is one
    mapping per loaded room in ``room_paths`` order.
    """
    entries, unmapped = _resolve_room_configs(
        project, service, cli, installation, host_environment
    )
    return [meta for meta, _ in entries], unmapped


def do_show(args: argparse.Namespace) -> int:
    _require_docker()
    project = resolve_project(args.project_dir)

    stdout = run_config(project, args.service, args.cli, args.installation)

    print(stdout, end="")
    return 0


def do_get(args: argparse.Namespace) -> int:
    _require_docker()
    project = resolve_project(args.project_dir)

    config = parse_config(
        run_config(project, args.service, args.cli, args.installation)
    )
    value = navigate(config, args.key)

    print(render_value(value, args.format))
    return 0


def do_rooms(args: argparse.Namespace) -> int:
    _require_docker()
    project = resolve_project(args.project_dir)

    rooms, unmapped = resolve_rooms(
        project,
        args.service,
        args.cli,
        args.installation,
        args.host_environment,
    )

    for container_path in unmapped:
        print(
            f"warning: room path {container_path!r} is outside "
            f"{args.installation!r}; cannot map to a host file -- skipped",
            file=sys.stderr,
        )
    print(render_value(rooms, "yaml"))
    return 0


def do_room(args: argparse.Namespace) -> int:
    _require_docker()
    project = resolve_project(args.project_dir)

    entries, _ = _resolve_room_configs(
        project,
        args.service,
        args.cli,
        args.installation,
        args.host_environment,
    )

    for meta, cfg in entries:
        if meta["room_id"] == args.room_id:
            print(cfg.read_text(), end="")
            return 0
    raise RoomNotFound(args.room_id)


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    """Args shared by every subcommand: which stack/service/config to query."""
    parser.add_argument(
        "--project-dir",
        default=".",
        help="stack directory (default: current directory)",
    )
    parser.add_argument(
        "--service",
        default=DEFAULT_SERVICE,
        help=(
            "compose service running soliplex-cli "
            f"(default: {DEFAULT_SERVICE})"
        ),
    )
    parser.add_argument(
        "--cli",
        default=DEFAULT_CLI,
        help=f"in-container soliplex-cli path (default: {DEFAULT_CLI})",
    )
    parser.add_argument(
        "--installation",
        default=DEFAULT_INSTALLATION,
        help=(
            f"in-container installation path (default: {DEFAULT_INSTALLATION})"
        ),
    )


def _add_host_environment(parser: argparse.ArgumentParser) -> None:
    """The extra arg the room-mapping subcommands (rooms/room) need."""
    parser.add_argument(
        "--host-environment",
        default=DEFAULT_HOST_ENVIRONMENT,
        help=(
            "host dir bind-mounted onto --installation "
            f"(default: {DEFAULT_HOST_ENVIRONMENT})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query a Soliplex stack's resolved installation config."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser(
        "show", help="print the whole resolved installation config"
    )
    _add_config_args(show)
    show.set_defaults(func=do_show)

    get = sub.add_parser(
        "get", help="print one config value by dotted path into the config"
    )
    get.add_argument(
        "key",
        help="dotted path, e.g. 'room_paths', 'room_paths.0', 'agents.chat'",
    )
    get.add_argument(
        "--format",
        choices=("plain", "yaml"),
        default="plain",
        help=(
            "plain (default): scalars bare, list-of-scalars one per line; "
            "yaml: dump any value as YAML"
        ),
    )
    _add_config_args(get)
    get.set_defaults(func=do_get)

    rooms = sub.add_parser(
        "rooms",
        help="print a {room_id, name, description} mapping per loaded room",
    )
    _add_config_args(rooms)
    _add_host_environment(rooms)
    rooms.set_defaults(func=do_rooms)

    room = sub.add_parser(
        "room", help="print the full room_config.yaml of one room by id"
    )
    room.add_argument(
        "room_id", help="the id of the room to print (see 'rooms')"
    )
    _add_config_args(room)
    _add_host_environment(room)
    room.set_defaults(func=do_room)

    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except SoliplexConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc})", file=sys.stderr)
        sys.exit(2)
