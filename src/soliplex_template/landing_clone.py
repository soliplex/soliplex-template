"""The provisioned-rooms clone: the git repository rooms land in.

The provisioner is the clone's only writer. Each provisioned room is one
directory (named for the room id) committed with the requester as author,
then pushed to ``origin`` when one is configured.

Git configuration applies as usual, so an operator can set policy for the
provisioner's commits -- e.g. by pointing ``GIT_CONFIG_GLOBAL`` at a
mounted file: its ``user.name`` / ``user.email`` make the committer (the
provisioner's own identity fills in whichever is unset), and its hooks or
signing settings apply. Every git call uses literal pathspecs (room ids
carry ':') and never prompts for credentials.

Stdlib only -- git via :mod:`subprocess`, the lock via :mod:`fcntl`.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import shutil
import subprocess

BRANCH = "main"
COMMITTER_NAME = "Soliplex provisioner"
COMMITTER_EMAIL = "provisioner@soliplex.invalid"


class LandingCloneError(Exception):
    """A user-facing error (printed without a traceback)."""


class GitMissing(LandingCloneError):
    def __init__(self):
        super().__init__("the provisioner needs 'git', which is not installed")


class GitFailed(LandingCloneError):
    def __init__(self, args, stderr):
        self.command, self.stderr = args, stderr
        super().__init__(f"'git {' '.join(args)}' failed: {stderr}")


class NothingToCommit(LandingCloneError):
    def __init__(self, room_id):
        self.room_id = room_id
        super().__init__(f"nothing to commit for room {room_id!r}")


class PushFailed(LandingCloneError):
    def __init__(self, stderr):
        self.stderr = stderr
        super().__init__(
            f"pushing the provisioned-rooms clone failed: {stderr}"
        )


def _env(extra_config: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for one git call.

    ``extra_config`` is appended to any ``GIT_CONFIG_KEY_<n>`` /
    ``GIT_CONFIG_VALUE_<n>`` entries the environment already carries.
    """
    env = {
        **os.environ,
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if extra_config:
        start = int(env.get("GIT_CONFIG_COUNT") or 0)
        for i, (key, value) in enumerate(extra_config.items(), start):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
        env["GIT_CONFIG_COUNT"] = str(start + len(extra_config))
    return env


def _git(path, *args, extra_config=None) -> subprocess.CompletedProcess:
    if shutil.which("git") is None:
        raise GitMissing()
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env=_env(extra_config),
    )


def _check(path, *args, extra_config=None) -> str:
    result = _git(path, *args, extra_config=extra_config)
    if result.returncode != 0:
        raise GitFailed(args, result.stderr.strip())
    return result.stdout


def _committer_defaults(path) -> dict[str, str]:
    """The provisioner's identity for whichever of name / email is unset."""
    defaults = {"user.name": COMMITTER_NAME, "user.email": COMMITTER_EMAIL}
    return {
        key: value
        for key, value in defaults.items()
        if _git(path, "config", "--get", key).returncode != 0
    }


def lock_path(path: pathlib.Path) -> pathlib.Path:
    """The lock file serializing writers of the clone at ``path``."""
    return path.parent / f".{path.name}.lock"


@contextlib.contextmanager
def locked(path: pathlib.Path):
    """Hold the exclusive writer's lock on the clone at ``path``.

    The lock file sits beside the clone, so it is taken before the clone
    exists and never shows in the clone's working tree.
    """
    lock = lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ensure_clone(path: pathlib.Path, *, origin_url: str | None = None) -> None:
    """Make ``path`` a git repository on :data:`BRANCH`.

    Initializes it when it is not one yet. With ``origin_url``, points
    ``origin`` at it (adding or updating the remote).
    """
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _check(path, "init", "--quiet", "--initial-branch", BRANCH)
    if origin_url is not None:
        remotes = _check(path, "remote").split()
        verb = "set-url" if "origin" in remotes else "add"
        _check(path, "remote", verb, "origin", origin_url)


def commit_room(
    path: pathlib.Path,
    room_id: str,
    *,
    author_name: str,
    author_email: str,
    message: str,
) -> str:
    """Commit the ``room_id`` directory of the clone at ``path``.

    Stages only that directory. The requester is the author; the committer
    is the configured ``user.name`` / ``user.email``, with the
    provisioner's identity for whichever is unset. Returns the new commit's
    id; raises :class:`NothingToCommit` when the directory holds no change.
    """
    _check(path, "add", "--all", "--", room_id)
    staged = _git(path, "diff", "--cached", "--quiet", "--", room_id)
    if staged.returncode == 0:
        raise NothingToCommit(room_id)
    _check(
        path,
        "commit",
        "--quiet",
        "--author",
        f"{author_name} <{author_email}>",
        "--message",
        message,
        "--",
        room_id,
        extra_config=_committer_defaults(path),
    )
    return _check(path, "rev-parse", "HEAD").strip()


def push(
    path: pathlib.Path, *, branch: str = BRANCH, token: str | None = None
) -> None:
    """Push ``branch`` of the clone at ``path`` to ``origin``.

    A Gitea ``token`` travels as an ``http.extraHeader`` set through the
    environment, never on the command line or in the clone's config.
    """
    extra_config = {}
    if token is not None:
        extra_config["http.extraHeader"] = f"Authorization: token {token}"
    result = _git(
        path,
        "push",
        "--quiet",
        "origin",
        f"{branch}:{branch}",
        extra_config=extra_config,
    )
    if result.returncode != 0:
        raise PushFailed(result.stderr.strip())
