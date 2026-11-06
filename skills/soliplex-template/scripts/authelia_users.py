# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml",
# ]
# ///
"""Manage Authelia users for an installed in-stack OIDC provider.

Run from the stack directory (the one containing ``docker-compose.yml``):

    python3 scripts/authelia_users.py add --email user@example.local
    python3 scripts/authelia_users.py add --admin --email admin@example.local
    python3 scripts/authelia_users.py list
    python3 scripts/authelia_users.py remove user@example.local
    python3 scripts/authelia_users.py reset user@example.local
    python3 scripts/authelia_users.py code

Passwords are generated randomly and written to
``.secrets/authelia_user_*.gen`` with mode ``0600``; only the hash is stored
in ``authelia/users.yml``.

``add --quiet`` prints a JSON summary (keys: ``email``, ``sub``, ``admin``,
``password_file``) on stdout, for scripted callers.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import secrets
import string
import subprocess
import sys
import uuid

import yaml

USERS_FILE = pathlib.PurePosixPath("authelia/users.yml")
IDENTIFIERS_FILE = pathlib.PurePosixPath("authelia/identifiers.yml")
NOTIFICATION_FILE = pathlib.PurePosixPath("authelia/notification.txt")
SECRETS_DIR = pathlib.PurePosixPath(".secrets")

_ALPHANUM = string.ascii_letters + string.digits


class AutheliaUsersError(Exception):
    """A user-facing error (printed without a traceback)."""


class StackNotFound(AutheliaUsersError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"not a Soliplex stack: {path} (no docker-compose.yml)"
        )


class UserNotFound(AutheliaUsersError):
    def __init__(self, email):
        self.email = email
        super().__init__(f"no Authelia user {email!r}")


class NoNotificationFile(AutheliaUsersError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"no notification file at {path}")


class DockerCommandFailed(AutheliaUsersError):
    def __init__(self, cmd, returncode, stderr):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"command failed ({returncode}): {' '.join(cmd)}\n{stderr}"
        )


def _run_cmd(
    cmd: list[str],
    *,
    cwd: pathlib.Path,
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture or check,
        text=True,
        input=input_text,
        check=False,
    )
    if check and proc.returncode != 0:
        raise DockerCommandFailed(
            cmd, proc.returncode, proc.stderr or proc.stdout or ""
        )
    return proc


def _generate_password(length: int = 32) -> str:
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _password_file(stack: pathlib.Path, email: str) -> pathlib.Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", email)
    return stack / SECRETS_DIR / f"authelia_user_{safe}.gen"


def _generate_hash(stack: pathlib.Path, password: str) -> str:
    """Generate an Authelia-compatible argon2 password hash."""
    proc = _run_cmd(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "authelia",
            "authelia",
            "crypto",
            "hash",
            "generate",
            "argon2",
            "--password",
            password,
        ],
        cwd=stack,
        capture=True,
    )
    # The CLI prints 'Digest: $argon2...': keep the digest only.
    match = re.search(r"\$argon2\S*", proc.stdout)
    return match.group(0) if match else proc.stdout.strip()


def _load_yaml(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    return yaml.safe_load(text) or {}


def _save_yaml(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False, default_flow_style=False))


def _username(email: str) -> str:
    """Authelia file-backend username for an email address."""
    return email


def _read_sub(stack: pathlib.Path, email: str) -> str | None:
    """Return the opaque identifier (sub) Authelia assigned to ``email``."""
    data = _load_yaml(stack / IDENTIFIERS_FILE)
    for mapping in data.get("identifiers", []):
        if mapping.get("username") == email or mapping.get("email") == email:
            return mapping.get("identifier")
    return None


def _export_identifiers(stack: pathlib.Path) -> None:
    """Export Authelia's opaque identifiers to ``authelia/identifiers.yml``."""
    # The export refuses to overwrite an existing file; the bind mount
    # makes the container and host paths the same file.
    (stack / IDENTIFIERS_FILE).unlink(missing_ok=True)
    _run_cmd(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "authelia",
            "authelia",
            "-c",
            "/config/configuration.yml",
            "storage",
            "user",
            "identifiers",
            "export",
            "--file",
            "/config/identifiers.yml",
        ],
        cwd=stack,
    )


def _upsert_identifier(stack: pathlib.Path, username: str) -> str:
    """The user's opaque identifier, adding one if storage lacks it.

    Storage persists identifiers across runs, so a partial earlier run may
    have assigned one already: reusing it beats refusing as a duplicate.
    """
    _export_identifiers(stack)
    sub = _read_sub(stack, username)
    if sub is not None:
        return sub
    sub = str(uuid.uuid4())
    _run_cmd(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "authelia",
            "authelia",
            "-c",
            "/config/configuration.yml",
            "storage",
            "user",
            "identifiers",
            "add",
            username,
            "--identifier",
            sub,
        ],
        cwd=stack,
    )
    _export_identifiers(stack)
    return sub


def add_user(stack: pathlib.Path, email: str, *, admin: bool = False) -> str:
    """Add an Authelia user and return their opaque ``sub`` identifier.

    ``admin=True`` also adds a soliplex ``admin_users`` entry for that ``sub``.
    """
    users_path = stack / USERS_FILE
    users_data = _load_yaml(users_path)
    users = users_data.setdefault("users", {})
    username = _username(email)

    password = _generate_password()
    password_path = _password_file(stack, email)
    password_path.parent.mkdir(parents=True, exist_ok=True)
    password_path.write_text(password)
    password_path.chmod(0o600)

    hash_value = _generate_hash(stack, password)
    users[username] = {
        "displayname": email.split("@")[0],
        "password": hash_value,
        "email": email,
    }
    _save_yaml(users_path, users_data)

    sub = _upsert_identifier(stack, username)

    if admin:
        _add_admin(stack, sub)

    return sub


def _add_admin(stack: pathlib.Path, sub: str) -> None:
    """Add a soliplex admin entry matching ``sub``."""
    json_path = f'$[?$.sub == "{sub}"]'
    _run_cmd(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--quiet-build",
            "soliplex-dev",
            "/app/.venv/bin/soliplex-cli",
            "admin-users",
            "add",
            "/environment",
            "--json-path",
            json_path,
        ],
        cwd=stack,
    )


def _remove_admin(stack: pathlib.Path, sub: str) -> None:
    """Remove the soliplex admin entry matching ``sub``."""
    json_path = f'$[?$.sub == "{sub}"]'
    _run_cmd(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--quiet-build",
            "soliplex-dev",
            "/app/.venv/bin/soliplex-cli",
            "admin-users",
            "delete",
            "/environment",
            "--json-path",
            json_path,
        ],
        cwd=stack,
        check=False,
    )


def list_users(stack: pathlib.Path) -> list[dict]:
    """Return the current Authelia users."""
    data = _load_yaml(stack / USERS_FILE)
    users = data.get("users", {})
    result = []
    for username, info in users.items():
        if not isinstance(info, dict):
            continue
        result.append(
            {
                "username": username,
                "email": info.get("email"),
                "displayname": info.get("displayname"),
            }
        )
    return result


def remove_user(stack: pathlib.Path, email: str) -> None:
    """Remove an Authelia user and any matching soliplex admin entry.

    Authelia 4.39 has no "storage user identifiers delete": the user's
    opaque identifier row stays in storage and in the exported
    ``identifiers.yml``, so re-adding the same email later reuses the same
    ``sub``. Any password file for the user is removed.
    """
    sub = _read_sub(stack, email)
    users_path = stack / USERS_FILE
    data = _load_yaml(users_path)
    users = data.get("users", {})
    username = _username(email)
    if username not in users:
        raise UserNotFound(email)
    del users[username]
    _save_yaml(users_path, data)

    if sub:
        _remove_admin(stack, sub)

    password_path = _password_file(stack, email)
    password_path.unlink(missing_ok=True)
    print(f"removed Authelia user {email!r}")


def reset_password(stack: pathlib.Path, email: str) -> None:
    """Generate a new password for an existing Authelia user."""
    users_path = stack / USERS_FILE
    data = _load_yaml(users_path)
    users = data.get("users", {})
    username = _username(email)
    if username not in users:
        raise UserNotFound(email)

    password = _generate_password()
    password_path = _password_file(stack, email)
    password_path.write_text(password)
    password_path.chmod(0o600)

    hash_value = _generate_hash(stack, password)
    users[username]["password"] = hash_value
    _save_yaml(users_path, data)

    print(f"reset password for {email!r}")
    print(f"  new password file: {password_path}")


def show_code(stack: pathlib.Path) -> str:
    """Return the latest Authelia notification content."""
    path = stack / NOTIFICATION_FILE
    if not path.is_file():
        raise NoNotificationFile(path)
    return path.read_text()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="authelia_users.py",
        description="Manage Authelia users for the in-stack OIDC provider.",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="the stack directory (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add a user")
    add.add_argument("--email", required=True)
    add.add_argument(
        "--admin", action="store_true", help="also add as a soliplex admin"
    )
    add.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print a JSON summary on stdout instead of human output",
    )
    add.set_defaults(func=do_add)

    sub.add_parser("list", help="list users").set_defaults(func=do_list)

    remove = sub.add_parser("remove", help="remove a user")
    remove.add_argument("email")
    remove.set_defaults(func=do_remove)

    reset = sub.add_parser("reset", help="reset a user's password")
    reset.add_argument("email")
    reset.set_defaults(func=do_reset)

    code = sub.add_parser(
        "code", help="show the latest notification code/link"
    )
    code.set_defaults(func=do_code)

    return parser


def do_add(stack: pathlib.Path, args: argparse.Namespace) -> None:
    sub = add_user(stack, args.email, admin=args.admin)
    if args.quiet:
        summary = {
            "email": args.email,
            "sub": sub,
            "admin": args.admin,
            "password_file": str(_password_file(stack, args.email)),
        }
        print(json.dumps(summary, indent=2))
        return
    print(f"added Authelia user {args.email!r}")
    print(f"  sub: {sub}")
    print(f"  password file: {_password_file(stack, args.email)}")
    if args.admin:
        print("  added soliplex admin entry for this sub")


def do_list(stack: pathlib.Path, _args: argparse.Namespace) -> None:
    users = list_users(stack)
    if not users:
        print("no Authelia users")
        return
    for user in users:
        username = user["username"]
        email = user.get("email", "")
        display = user.get("displayname", "")
        print(f"{username}  {email}  ({display})")


def do_remove(stack: pathlib.Path, args: argparse.Namespace) -> None:
    remove_user(stack, args.email)


def do_reset(stack: pathlib.Path, args: argparse.Namespace) -> None:
    reset_password(stack, args.email)


def do_code(stack: pathlib.Path, _args: argparse.Namespace) -> None:
    print(show_code(stack))


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    stack = pathlib.Path(args.project_dir).resolve()
    if not (stack / "docker-compose.yml").is_file():
        raise StackNotFound(stack)
    args.func(stack, args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AutheliaUsersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
