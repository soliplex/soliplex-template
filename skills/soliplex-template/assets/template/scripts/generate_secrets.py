#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.12"]
# ///
"""Generate the Docker secrets this stack needs (PEP 723 shim).

Thin front end over ``soliplex_template.secrets`` (provisioned by ``uv run``
from the PEP 723 dependency above): it scans this stack's
``docker-compose.yml`` for the ``*.gen`` secret files, writes a fresh random
password into each at mode ``0600``, and re-owns them to the container
``PUID:PGID`` when the operator's uid/gid differs. Run it from the stack::

    uv run scripts/generate_secrets.py

By default it operates on the parent of this script (the stack root); pass
``--project-dir`` to point elsewhere.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

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
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    generate_secrets(args.project_dir or default_project())
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except SecretsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
