#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.17"]
# ///
"""Migrate this stack's LanceDB vector stores (PEP 723 shim).

Thin front end over ``soliplex_template.rag_migrations`` (provisioned by
``uv run`` from the PEP 723 dependency above): it runs the pinned
``haiku.rag-slim`` image's own ``haiku-rag`` CLI against every ``*.lancedb``
under ``rag/db/``, reporting or applying whatever store migrations that release
turns out to need -- usually none, since haiku.rag ships an upgrade only for
the few releases that changed the store layout.

Report what a bumped image would demand (safe while the stack is up; exits 1
if anything is pending, so it gates the bump)::

    uv run scripts/migrate_rag_dbs.py --check

Apply it, which needs the single writer stopped::

    docker compose stop haiku-ingester
    uv run scripts/migrate_rag_dbs.py
    docker compose start haiku-ingester

By default it operates on the parent of this script (the stack root) and on
every database it finds; pass ``--project-dir`` to point elsewhere, or
``--db-name`` (repeatable) to select stores by stem.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from soliplex_template.rag_migrations import RagMigrationsError
from soliplex_template.rag_migrations import migrate_rag_dbs


def default_project() -> pathlib.Path:
    # This file is '<stack>/scripts/migrate_rag_dbs.py'; the stack root is the
    # parent of 'scripts/'.
    return pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate this Soliplex stack's LanceDB vector stores."
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="stack directory (default: the parent of this script)",
    )
    parser.add_argument(
        "--db-name",
        action="append",
        default=None,
        metavar="STEM",
        help=(
            "database stem to operate on, e.g. 'handbook' for "
            "rag/db/handbook.lancedb (repeatable; default: all of them)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "report pending migrations without applying any; "
            "exits 1 if any database is behind"
        ),
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    return migrate_rag_dbs(
        args.project_dir or default_project(),
        names=args.db_name,
        check=args.check,
    )


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except RagMigrationsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
