#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Provision a private RAG room on a running stack, for a requester.

Runs the stack's room provisioner (``soliplex-provision-room``, from the
``soliplex-template`` package) in a one-off ``soliplex-dev`` container::

    # a room for 'alice', its id derived from the name ('alice:q3-plans')
    uv run provision_room.py --project-dir /path/to/stack \\
        --username alice --email alice@example.com \\
        --display-name "Alice Example" --name "Q3 Plans"

    # the requester's claims as JSON, an explicit slug ('alice:plans')
    uv run provision_room.py --claims '{"preferred_username": "alice"}' \\
        --name "Q3 Plans" --slug plans

The stack must be up (backend, ingester). ``SOLIPLEX_ADMIN_TOKEN``, when
set here, is passed through: a backend running with OIDC needs it to admit
the provisioner's room-configs reload.

Exits with the provisioner's status: 0 when the room is live, 1 when steps
after its commit remain (each is printed), 2 on an error.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

SERVICE = "soliplex-dev"
COMMAND = "/app/.venv/bin/soliplex-provision-room"
PASSED_THROUGH = ("SOLIPLEX_ADMIN_TOKEN",)


class ProvisionRoomError(Exception):
    """A user-facing error (printed without a traceback)."""


class NotAStack(ProvisionRoomError):
    def __init__(self, project):
        self.project = project
        super().__init__(
            f"{project} has no docker-compose.yml "
            "(pass --project-dir pointing at the stack directory)"
        )


class BadClaims(ProvisionRoomError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(f"--claims must be a JSON object: {detail}")


class ClaimsNotAnObject(BadClaims):
    def __init__(self, value):
        super().__init__(f"got {type(value).__name__}")


class NoRequester(ProvisionRoomError):
    def __init__(self):
        super().__init__("pass --claims, or at least --username")


def requester_claims(args: argparse.Namespace) -> dict:
    """The requester's claims, from ``--claims`` or the per-claim flags."""
    if args.claims is not None:
        try:
            claims = json.loads(args.claims)
        except json.JSONDecodeError as error:
            raise BadClaims(error) from error
        if not isinstance(claims, dict):
            raise ClaimsNotAnObject(claims)
        return claims
    if args.username is None:
        raise NoRequester()
    claims = {"preferred_username": args.username}
    if args.email is not None:
        claims["email"] = args.email
    if args.display_name is not None:
        claims["name"] = args.display_name
    return claims


def compose_argv(
    project: pathlib.Path, args: argparse.Namespace, environ
) -> list[str]:
    """The ``docker compose run`` command provisioning the room."""
    argv = [
        "docker",
        "compose",
        "--project-directory",
        str(project),
        "run",
        "--rm",
        "--no-TTY",
        # 'soliplex-dev' builds from the backend's image ('service:backend'),
        # which each 'run' re-checks: keep that build's progress quiet.
        "--quiet-build",
    ]
    for name in PASSED_THROUGH:
        if environ.get(name):
            argv += ["-e", name]
    argv += [
        SERVICE,
        COMMAND,
        "--claims",
        json.dumps(requester_claims(args)),
        "--name",
        args.name,
    ]
    if args.description is not None:
        argv += ["--description", args.description]
    if args.slug is not None:
        argv += ["--slug", args.slug]
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision a private RAG room for a requester."
    )
    parser.add_argument(
        "--project-dir", default=".", help="the stack directory"
    )
    who = parser.add_argument_group("requester")
    who.add_argument("--claims", help="the requester's claims, as JSON")
    who.add_argument("--username", help="the 'preferred_username' claim")
    who.add_argument("--email", help="the 'email' claim")
    who.add_argument("--display-name", help="the 'name' claim")
    room = parser.add_argument_group("room")
    room.add_argument("--name", required=True, help="human-readable name")
    room.add_argument("--description", help="defaults to the name")
    room.add_argument("--slug", help="defaults to one derived from the name")
    return parser


def main(argv=None, environ=None) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        project = pathlib.Path(args.project_dir).resolve()
        if not (project / "docker-compose.yml").is_file():
            raise NotAStack(project)
        command = compose_argv(project, args, environ)
    except ProvisionRoomError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
