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

Pass ``--push-to-gitea`` to also back this stack's own git repository with
Gitea: your SSH key(s) are registered on the service account, an empty repo is
created, ``origin`` is set to its SSH URL, and the current branch is pushed.
The repo is named after the stack directory unless ``--stack-repo NAME`` is
given; ``--ssh-key PATH`` overrides which public key is registered (default:
the keys loaded in your ssh-agent, else ``~/.ssh/*.pub``).
"""

from __future__ import annotations

import argparse
import getpass
import pathlib
import sys

from soliplex_template import gitea


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
    parser.add_argument(
        "--push-to-gitea",
        action="store_true",
        help="back this stack's git repo with Gitea: register your SSH "
        "key(s), create a repo, set 'origin', and push the initial commit.",
    )
    parser.add_argument(
        "--stack-repo",
        default=None,
        metavar="NAME",
        help="name for the backing repo (with --push-to-gitea); default: the "
        "stack directory name. Pass this when that name is not a valid Gitea "
        "repo name.",
    )
    parser.add_argument(
        "--ssh-key",
        default=None,
        metavar="PATH",
        help="public key to register with Gitea (with --push-to-gitea); "
        "default: ssh-agent keys, else ~/.ssh/*.pub.",
    )
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _prompt_admin_password(username: str) -> str:
    prompt = f"Password for Gitea web-UI user {username!r}: "
    password = getpass.getpass(prompt)
    if password != getpass.getpass("Confirm password: "):
        raise gitea.PasswordMismatch()
    return password


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    webui_password = None
    if args.admin_user is not None:
        if args.admin_user == gitea.ADMIN_USER:
            raise gitea.ReservedUser(args.admin_user)
        webui_password = _prompt_admin_password(args.admin_user)
    gitea.provision_gitea(
        args.project_dir or default_project(),
        webui_user=args.admin_user,
        webui_password=webui_password,
        push_to_gitea=args.push_to_gitea,
        stack_repo=args.stack_repo,
        ssh_key=args.ssh_key,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv[1:]))
    except gitea.GiteaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
