#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.19"]
# ///
"""Generate the Docker secrets this stack needs (PEP 723 shim).

Thin front end over ``soliplex_template.secrets`` (provisioned by ``uv run``
from the PEP 723 dependency above): it scans this stack's
``docker-compose.yml`` for the ``*.gen`` secret files and provisions them.
By default it writes fresh random passwords (mode ``0600``, re-owned to the
container ``PUID:PGID`` when the operator's uid/gid differs) only where the
file is still missing, so re-running never replaces an existing value.

Replacing existing values (rotation) is explicit: ``--rotate NAME ...``
overwrites just the named secrets, ``--rotate-all`` every declared one.
Rotated Postgres role passwords print their matching ``ALTER ROLE``
commands (Postgres reads those secrets only at initdb). Bare ``--rotate``
refuses to rotate everything: it prints a dry-run preview instead, flagging
the secrets which would need ``ALTER ROLE`` handling. ``--dry-run``
previews any mode without writing anything. Run it from the stack::

    uv run scripts/generate_secrets.py

By default it operates on the parent of this script (the stack root); pass
``--project-dir`` to point elsewhere.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from soliplex_template.secrets import ROTATE_ALL
from soliplex_template.secrets import SecretsError
from soliplex_template.secrets import generate_secrets


def default_project() -> pathlib.Path:
    # This file is '<stack>/scripts/generate_secrets.py'; the stack root is the
    # parent of 'scripts/'.
    return pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the Docker secrets for this Soliplex stack."
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="stack directory (default: the parent of this script)",
    )
    rotate = parser.add_mutually_exclusive_group()
    rotate.add_argument(
        "--rotate",
        nargs="*",
        default=None,
        metavar="NAME",
        help="rotate the named *.gen secrets instead of generating only the "
        "missing ones (bare '--rotate': dry-run preview of rotating all)",
    )
    rotate.add_argument(
        "--rotate-all",
        action="store_true",
        help="rotate every declared *.gen secret",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be written; write nothing",
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.rotate == []:
        print(
            "Refusing to rotate every declared secret by accident; "
            "previewing the run:"
        )
        print(
            "(real rotations: '--rotate NAME ...' for named, "
            "'--rotate-all' for all)"
        )
        generate_secrets(
            args.project_dir or default_project(),
            rotate=ROTATE_ALL,
            dry_run=True,
        )
        return 0
    generate_secrets(
        args.project_dir or default_project(),
        rotate=ROTATE_ALL if args.rotate_all else args.rotate,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except SecretsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
