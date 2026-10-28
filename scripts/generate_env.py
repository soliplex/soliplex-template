#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.19"]
# ///
"""Recover this stack's .env (PEP 723 shim).

Thin front end over ``soliplex_template.env`` (provisioned by ``uv run``
from the PEP 723 dependency above): it reads the stack's generation
manifest (``[tool.soliplex-template.params]`` in ``pyproject.toml``) and
recreates a fresh clone's missing ``.env`` with ``OLLAMA_BASE_URL``,
``PUID``, and ``PGID`` -- commented out, at mode ``0600``. It never
rewrites an existing ``.env``. Run it from the stack::

    uv run scripts/generate_env.py

By default it operates on the parent of this script (the stack root); pass
``--project-dir`` to point elsewhere. ``generate_secrets.py`` is its
sibling for the ``.secrets/*.gen`` files (also gitignored).
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from soliplex_template.env import EnvError
from soliplex_template.env import generate_env


def default_project() -> pathlib.Path:
    # This file is '<stack>/scripts/generate_env.py'; the stack root is the
    # parent of 'scripts/'.
    return pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover this stack's .env from its generation manifest."
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
    generate_env(args.project_dir or default_project())
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except EnvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
