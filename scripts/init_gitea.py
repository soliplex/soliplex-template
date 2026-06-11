#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-template>=0.12"]
# ///
"""Provision this stack's local Gitea service (PEP 723 shim).

Thin front end over ``soliplex_template.gitea`` (provisioned by ``uv run`` from
the PEP 723 dependency above): it waits for Gitea, ensures an admin user, mints
a scoped access token and a tracking repository, and writes ``GITEA_HOST`` /
``GITEA_ACCESS_TOKEN`` into ``.env``.

Run it AFTER ``docker compose up -d`` (postgres + gitea healthy)::

    uv run scripts/init_gitea.py

Idempotent: re-running resets the service account's password and reuses the
existing repo. By default it operates on the parent of this script (the stack
root); pass ``--project-dir`` to point elsewhere.

Pass ``--admin-user NAME`` to also create (or update) a *distinct* Gitea
site-admin account for web-UI login -- you are prompted for its password (it is
never taken on the command line). The rotating service account is unaffected,
and ``NAME`` may not be that service account.
"""

from __future__ import annotations

import argparse
import getpass
import pathlib
import sys

from soliplex_template.gitea import ADMIN_USER
from soliplex_template.gitea import GiteaError
from soliplex_template.gitea import provision_gitea


def default_project() -> pathlib.Path:
    # This file is '<stack>/scripts/init_gitea.py'; the stack root is the
    # parent of 'scripts/'.
    return pathlib.Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision the local Gitea service for this stack."
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="stack directory (default: the parent of this script)",
    )
    parser.add_argument(
        "--admin-user",
        default=None,
        metavar="NAME",
        help="also create/update a distinct Gitea site-admin user with this "
        "name for web-UI login (you are prompted for its password); omit to "
        "skip. May not be the rotating service account.",
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _prompt_admin_password(username: str) -> str:
    prompt = f"Password for Gitea web-UI user {username!r}: "
    password = getpass.getpass(prompt)
    if password != getpass.getpass("Confirm password: "):
        raise GiteaError.password_mismatch()
    return password


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    webui_password = None
    if args.admin_user is not None:
        if args.admin_user == ADMIN_USER:
            raise GiteaError.reserved_user(args.admin_user)
        webui_password = _prompt_admin_password(args.admin_user)
    provision_gitea(
        args.project_dir or default_project(),
        webui_user=args.admin_user,
        webui_password=webui_password,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except GiteaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
