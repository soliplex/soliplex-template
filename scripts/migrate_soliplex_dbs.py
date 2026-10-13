#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.17"]
# ///
"""Migrate this stack's Soliplex databases (PEP 723 shim).

Thin front end over ``soliplex_template.soliplex_migrations`` (provisioned by
``uv run`` from the PEP 723 dependency above): it runs the backend image's own
``soliplex-cli database`` commands against the ``agui`` and ``authz``
databases, stamping any created by soliplex 0.81 or earlier (via soliplex's
one-time ``bootstrap_alembic_version.py``) before bringing both to head.

Report what the backend image would demand (safe while the stack is up; exits
1 if anything is owed, so it gates a ``soliplex`` bump)::

    uv run scripts/migrate_soliplex_dbs.py --check

Apply it, which needs the backend stopped::

    docker compose stop backend
    uv run scripts/migrate_soliplex_dbs.py
    docker compose start backend

By default it operates on the parent of this script (the stack root); pass
``--project-dir`` to point elsewhere.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from soliplex_template.soliplex_migrations import SoliplexMigrationsError
from soliplex_template.soliplex_migrations import migrate_soliplex_dbs


def default_project() -> pathlib.Path:
    # This file is '<stack>/scripts/migrate_soliplex_dbs.py'; the stack root
    # is the parent of 'scripts/'.
    return pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate this Soliplex stack's agui / authz databases."
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="stack directory (default: the parent of this script)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "report pending migrations without applying any; "
            "exits 1 if any database is unstamped, behind, or broken"
        ),
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return migrate_soliplex_dbs(
        args.project_dir or default_project(), check=args.check
    )


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except SoliplexMigrationsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
