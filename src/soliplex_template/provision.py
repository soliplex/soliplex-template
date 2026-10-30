"""Provision a self-serve RAG room on a generated stack, in one call.

:func:`provision_room` runs inside the stack's ``soliplex-dev`` container,
where the backend's installation tree is ``/environment``, the stack's
``src/`` is ``/app/src`` (read-write), and the backend, the ingester and
(optionally) Gitea are reachable over the compose network. Holding the
provisioned-rooms clone's lock throughout, it:

1. refuses the room, changing nothing, when anything named for its id
   already exists (:func:`check_clashes`):

   - its directory in the clone
   - its database directory
   - an entry for it in either map of the ingester config (or another
     database placed at its location)
   - a non-empty uploads directory
   - an authz policy

2. files the tracking issue, when a Gitea is configured and the concierge
   is installed
3. writes the room's ``room_config.yaml`` and ``REQUEST.md`` into the clone
4. creates the room's uploads directory
5. registers the room's database with the ingester and reloads it (which
   creates the database)
6. makes the room private, granting the requester READ and WRITE
7. audits the installation's rooms
8. commits the room (the requester as author) and pushes the clone
9. reloads the backend's room configs
10. comments on and closes the tracking issue

A failure before the commit undoes what was done (and comments the failure
on the tracking issue, leaving it open). After the commit the room is
valid as committed, so a failure there is reported, with what remains to
do, rather than undone.

It authenticates to the backend with a service token it mints for each
call (``soliplex-cli service-token mint``, subject ``soliplex-provisioner``
by default, five minutes' lifetime); an ``admin_users`` entry matching that
subject admits it. Before taking the lock, it checks that the backend does
(a backend in no-auth mode admits any caller).

The ``soliplex-provision-room`` console script (:func:`main`) runs it from
the command line, configured from the environment (see
:meth:`Settings.from_env`).
"""

from __future__ import annotations

import argparse
import collections.abc
import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from soliplex_plumber import rooms

from soliplex_template import gitea
from soliplex_template import ingester
from soliplex_template import landing_clone
from soliplex_template import provision_record
from soliplex_template import provisioned_room
from soliplex_template import room_ids

REQUEST_TYPE = "new-room"
HTTP_TIMEOUT = 30
DEFAULT_SERVICE_SUBJECT = "soliplex-provisioner"
SERVICE_TOKEN_LIFETIME = "5m"
INGESTER_TOKEN_FILE = "/run/secrets/ingester_bearer"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})

# A claim usable in an ACL entry's JSONPath query ('$.<claim>').
_CLAIM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ProvisionError(Exception):
    """A user-facing error (printed without a traceback)."""


class BadOwnerClaim(ProvisionError):
    def __init__(self, claim):
        self.claim = claim
        super().__init__(
            f"owner claim {claim!r} must be an identifier "
            f"({_CLAIM_RE.pattern})"
        )


class IngesterTokenMissing(ProvisionError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"cannot read the ingester's bearer token from {path} (is the "
            "'ingester_bearer' secret mounted?)"
        )


class GiteaTokenMissing(ProvisionError):
    def __init__(self):
        super().__init__(
            "GITEA_HOST is set but GITEA_ACCESS_TOKEN is not: the provisioner "
            "needs a token that may write issues and push the rooms repository"
        )


class BadFlag(ProvisionError):
    def __init__(self, name, value):
        self.name, self.value = name, value
        super().__init__(
            f"{name}={value!r}: expected one of "
            f"{', '.join(sorted(_TRUE | _FALSE - {''}))}"
        )


class IssueFilingFailed(ProvisionError):
    def __init__(self, error):
        self.error = error
        super().__init__(f"filing the tracking issue failed: {error}")


class AdminAccessDenied(ProvisionError):
    def __init__(self, code, subject, mint_error=None):
        self.code, self.subject, self.mint_error = code, subject, mint_error
        message = (
            f"the backend refused the provisioner's admin access (HTTP "
            f"{code}): add an 'admin_users' entry matching its service "
            f"tokens, '$[?$.sub == {json.dumps(subject)}]'"
        )
        if mint_error is not None:
            message += f"; minting its service token failed: {mint_error}"
        super().__init__(message)


class BackendRequestFailed(ProvisionError):
    def __init__(self, url, detail):
        self.url, self.detail = url, detail
        super().__init__(f"backend request {url} failed: {detail}")


class RoomExists(ProvisionError):
    def __init__(self, room_id):
        self.room_id = room_id
        super().__init__(
            f"room {room_id!r} already exists; choose another name"
        )


class ResourcesExist(ProvisionError):
    """Resources named for the room already exist, so it is not created.

    ``found`` describes each one; an operator decides whether to remove
    them before the room is provisioned again.
    """

    def __init__(self, room_id, found):
        self.room_id, self.found = room_id, tuple(found)
        super().__init__(
            f"room {room_id!r} was not provisioned: resources named for it "
            f"already exist: {'; '.join(self.found)}. Remove them, or choose "
            "another name"
        )


class CliFailed(ProvisionError):
    def __init__(self, command, output):
        self.command, self.output = command, output
        super().__init__(
            f"'soliplex-cli {' '.join(command)}' failed:\n{output}"
        )


class ProvisioningFailed(ProvisionError):
    """A step before the commit failed; what was done has been undone.

    ``rollback_failures`` names each undo step that itself failed, which
    leaves its part for an operator to clean up.
    """

    def __init__(self, room_id, error, rollback_failures=()):
        self.room_id, self.error = room_id, error
        self.rollback_failures = tuple(rollback_failures)
        message = f"provisioning room {room_id!r} failed: {error}"
        if self.rollback_failures:
            message += "; rolling back, these steps failed: " + "; ".join(
                self.rollback_failures
            )
        else:
            message += "; rolled back"
        super().__init__(message)


@dataclasses.dataclass(frozen=True)
class GiteaSettings:
    host: str
    token: str = dataclasses.field(repr=False)
    owner: str = gitea.ADMIN_USER
    repo: str = gitea.REPO_NAME
    rooms_repo: str = gitea.ROOMS_REPO_NAME

    @property
    def origin_url(self) -> str:
        """The provisioned-rooms clone's ``origin``."""
        return f"{self.host.rstrip('/')}/{self.owner}/{self.rooms_repo}.git"


@dataclasses.dataclass(frozen=True)
class Settings:
    """Where the provisioner finds the stack, from inside ``soliplex-dev``.

    The ``ingester_*`` paths are as the ingester sees them; the others as
    the provisioner does.
    """

    ingester_token: str = dataclasses.field(repr=False)
    environment: pathlib.Path = pathlib.Path("/environment")
    clone: pathlib.Path = pathlib.Path("/app/src/rooms-provisioned")
    uploads_dir: pathlib.Path = pathlib.Path("/uploads/rooms")
    db_dir: pathlib.Path = pathlib.Path("/db")
    ingester_config: pathlib.Path = pathlib.Path("/app/haiku.rag.yaml")
    ingester_url: str = "http://haiku-ingester:8765"
    ingester_data_dir: str = ingester.DEFAULT_DATA_DIR
    ingester_uploads_root: str = ingester.DEFAULT_UPLOADS_ROOT
    backend_url: str = "http://backend:8000"
    service_subject: str = DEFAULT_SERVICE_SUBJECT
    owner_claim: str = room_ids.DEFAULT_OWNER_CLAIM
    flatten_owner: bool = False
    soliplex_cli: str = "/app/.venv/bin/soliplex-cli"
    gitea: GiteaSettings | None = None

    def __post_init__(self):
        if not _CLAIM_RE.fullmatch(self.owner_claim):
            raise BadOwnerClaim(self.owner_claim)

    @classmethod
    def from_env(cls, environ: collections.abc.Mapping[str, str]) -> Settings:
        """Settings for ``soliplex-dev``, from its environment.

        - the ingester's bearer token, read from the file
          ``PROVISIONER_INGESTER_TOKEN_FILE`` names (default: the
          ``ingester_bearer`` secret)
        - ``PROVISIONER_SERVICE_SUBJECT``: the ``sub`` claim of the service
          tokens the provisioner mints for itself
        - ``PROVISIONER_OWNER_CLAIM`` / ``PROVISIONER_FLATTEN_OWNER``: the
          claim a room's owner comes from, and whether to flatten it
        - ``GITEA_HOST`` / ``GITEA_ACCESS_TOKEN`` / ``GITEA_OWNER`` /
          ``GITEA_REPO`` / ``GITEA_ROOMS_REPO``: the Gitea for tracking issues
          and the provisioned-rooms clone's ``origin``; unset host means none
        """
        token_file = pathlib.Path(
            environ.get("PROVISIONER_INGESTER_TOKEN_FILE")
            or INGESTER_TOKEN_FILE
        )
        try:
            ingester_token = token_file.read_text().strip()
        except OSError as error:
            raise IngesterTokenMissing(token_file) from error

        def setting(name, default=None):
            return environ.get(name, "").strip() or default

        gitea_settings = None
        if setting("GITEA_HOST"):
            if not setting("GITEA_ACCESS_TOKEN"):
                raise GiteaTokenMissing()
            gitea_settings = GiteaSettings(
                host=setting("GITEA_HOST"),
                token=setting("GITEA_ACCESS_TOKEN"),
                owner=setting("GITEA_OWNER", gitea.ADMIN_USER),
                repo=setting("GITEA_REPO", gitea.REPO_NAME),
                rooms_repo=setting("GITEA_ROOMS_REPO", gitea.ROOMS_REPO_NAME),
            )

        flatten = setting("PROVISIONER_FLATTEN_OWNER", "").lower()
        if flatten not in _TRUE | _FALSE:
            raise BadFlag("PROVISIONER_FLATTEN_OWNER", flatten)

        return cls(
            ingester_token=ingester_token,
            service_subject=setting(
                "PROVISIONER_SERVICE_SUBJECT", DEFAULT_SERVICE_SUBJECT
            ),
            owner_claim=setting(
                "PROVISIONER_OWNER_CLAIM", room_ids.DEFAULT_OWNER_CLAIM
            ),
            flatten_owner=flatten in _TRUE,
            gitea=gitea_settings,
        )


@dataclasses.dataclass(frozen=True)
class Provisioned:
    """What :func:`provision_room` did.

    ``incomplete`` names each step after the commit that failed, with how
    to finish it; it is empty when the room is fully live.
    """

    room_id: str
    room_dir: pathlib.Path
    commit: str
    issue_url: str | None
    incomplete: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Collaborators: the backend's HTTP API, soliplex-cli, the concierge
# --------------------------------------------------------------------------
def mint_service_token(settings: Settings) -> str:
    """A short-lived service token for the provisioner's subject."""
    minted = _cli(
        settings,
        "service-token",
        "mint",
        str(settings.environment),
        "--claims",
        json.dumps({"sub": settings.service_subject}),
        "--expires-in",
        SERVICE_TOKEN_LIFETIME,
        "--quiet",
    )
    return minted.strip()


def _backend(settings: Settings, method: str, path: str) -> dict:
    """Call the backend's API with a freshly minted service token.

    When minting fails, the call goes out without a token (which a backend
    in no-auth mode accepts); a refusal then reports the minting failure.
    """
    url = f"{settings.backend_url.rstrip('/')}/api{path}"
    headers = {"Accept": "application/json"}
    mint_error = None
    try:
        headers["Authorization"] = f"Bearer {mint_service_token(settings)}"
    except CliFailed as error:
        mint_error = error.output
    request = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AdminAccessDenied(
                error.code, settings.service_subject, mint_error
            ) from error
        body = error.read().decode(errors="replace")
        raise BackendRequestFailed(
            url, f"HTTP {error.code}: {body}"
        ) from error
    except urllib.error.URLError as error:
        raise BackendRequestFailed(url, error.reason) from error


def check_admin_access(settings: Settings) -> None:
    """Raise :class:`AdminAccessDenied` unless the backend admits us.

    That is, unless it admits the provisioner's service tokens as an admin's
    (or runs in no-auth mode).
    """
    _backend(settings, "GET", "/v1/installation")


def reload_backend_rooms(settings: Settings) -> dict:
    """Have the backend reload its room configs; return the delta."""
    return _backend(settings, "POST", "/v1/installation/reload/rooms")


def _cli(
    settings: Settings,
    *command: str,
    stdin: str | None = None,
    ok_codes: tuple[int, ...] = (0,),
) -> str:
    try:
        result = subprocess.run(
            [settings.soliplex_cli, *command],
            input=stdin,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise CliFailed(command, str(error)) from error
    if result.returncode not in ok_codes:
        raise CliFailed(command, (result.stdout + result.stderr).strip())
    return result.stdout


def stale_policy_ids(settings: Settings) -> set[str]:
    """The ids of authz policies for rooms that are not configured.

    ``audit room-authz`` reports them as "stale", exiting 1 with a JSON
    error report (``--quiet``); a clean audit prints nothing.
    """
    command = (
        "audit",
        "--quiet",
        "room-authz",
        str(settings.environment),
    )
    output = _cli(settings, *command, ok_codes=(0, 1))
    if not output.strip():
        return set()
    try:
        report = json.loads(output).get("room_authz", {})
    except (json.JSONDecodeError, AttributeError) as error:
        raise CliFailed(command, output.strip()) from error
    if "unreachable" in report:
        raise CliFailed(command, report["unreachable"])
    return set(report.get("stale_rooms", ()))


def check_clashes(settings: Settings, room: str) -> None:
    """Refuse ``room`` when anything named for it already exists.

    That is: its database directory, an entry for it in either map of the
    ingester config, another database placed at its location, a non-empty
    uploads directory, or an authz policy. Raises :class:`ResourcesExist`
    naming each one found.
    """
    found = []

    database = settings.db_dir / f"{room}.lancedb"
    if database.exists():
        found.append(f"database directory {database}")

    registered = ingester.registrations(settings.ingester_config.read_text())
    if room in registered.sources:
        found.append(f"ingester config entry 'ingester.databases.{room}'")
    if room in registered.locations:
        found.append(f"ingester config entry 'lancedb.databases.{room}'")
    location = posixpath.normpath(
        f"{settings.ingester_data_dir}/{room}.lancedb"
    )
    for name, placed in sorted(registered.locations.items()):
        if name != room and placed is not None:
            if posixpath.normpath(placed) == location:
                found.append(
                    f"ingester config entry 'lancedb.databases.{name}' "
                    f"placed at {location}"
                )

    uploads = settings.uploads_dir / room
    if uploads.is_dir() and any(uploads.iterdir()):
        found.append(f"non-empty uploads directory {uploads}")

    if room in stale_policy_ids(settings):
        found.append(f"an authz policy for {room!r}")

    if found:
        raise ResourcesExist(room, found)


def owner_policy(room_id: str, owner: room_ids.RoomOwner) -> str:
    """The room's authz policy: private, READ and WRITE for its owner.

    The grant matches the owner's raw claim value; the id's owner part may
    be flattened, and so cannot stand in for it.
    """
    json_path = f"$[?$.{owner.claim} == {json.dumps(owner.claim_value)}]"
    return json.dumps(
        {
            "room_id": room_id,
            "default_allow_deny": "DENY",
            "acl_entries": [
                {
                    "allow_deny": "ALLOW",
                    "permission": permission,
                    "json_path": json_path,
                }
                for permission in ("READ", "WRITE")
            ],
        }
    )


def _set_policy(settings: Settings, room_id: str, policy: str) -> None:
    _cli(
        settings,
        "room-authz",
        "from-yaml",
        str(settings.environment),
        room_id,
        stdin=policy,
    )


def _audit_rooms(settings: Settings) -> None:
    _cli(settings, "audit", "--quiet", "rooms", str(settings.environment))


def concierge_issues():
    """The concierge's Gitea functions, or None when it is not installed.

    Returns ``(create_gitea_issue_sync, comment_issue, close_issue)``;
    concierge releases before 0.12 lack the first, and count as absent.
    """
    try:
        from soliplex_concierge import gitea_admin
        from soliplex_concierge.tools import gitea as concierge_gitea

        create = concierge_gitea.create_gitea_issue_sync
    except (ImportError, AttributeError):
        return None
    return create, gitea_admin.comment_issue, gitea_admin.close_issue


# --------------------------------------------------------------------------
# The composition
# --------------------------------------------------------------------------
class _Rollback:
    """Undo steps, run newest first; each runs even if another fails."""

    def __init__(self):
        self._steps = []

    def add(self, description: str, step, *args) -> None:
        self._steps.append((description, step, args))

    def run(self) -> list[str]:
        failures = []
        for description, step, args in reversed(self._steps):
            try:
                step(*args)
            except Exception as error:
                failures.append(f"{description} ({error})")
        return failures


def _author(claims, owner: room_ids.RoomOwner) -> tuple[str, str]:
    name = str(claims.get("name") or owner.claim_value)
    email = str(claims.get("email") or f"{owner.owner}@users.soliplex.invalid")
    return name, email


def _commit_message(room_id, name, gitea_settings, issue_number) -> str:
    message = f"Provision room {room_id}\n\n{name}\n"
    if issue_number is not None:
        reference = (
            f"{gitea_settings.owner}/{gitea_settings.repo}#{issue_number}"
        )
        message += f"\nTracking issue: {reference}\n"
    return message


def _undo_ingester(settings: Settings, original: str, room_id: str) -> None:
    settings.ingester_config.write_text(original)
    ingester.reload(settings.ingester_url, settings.ingester_token)
    shutil.rmtree(settings.db_dir / f"{room_id}.lancedb", ignore_errors=True)


def provision_room(
    claims: collections.abc.Mapping,
    *,
    name: str,
    settings: Settings,
    description: str | None = None,
    slug: str | None = None,
    now: datetime.datetime | None = None,
) -> Provisioned:
    """Provision a private RAG room for the requester ``claims`` name.

    ``name`` is the room's human-readable name; ``slug`` (derived from it
    when omitted) completes the room id ``<owner>:<slug>``. Raises
    :class:`room_ids.RoomIdError` for an unusable owner, name or slug,
    :class:`AdminAccessDenied` before touching anything when the backend
    refuses the provisioner's service token, :class:`RoomExists` for a
    taken id, :class:`ResourcesExist` when resources named for the id
    already exist, :class:`IssueFilingFailed` when the tracking issue cannot be
    filed, and
    :class:`ProvisioningFailed` (after rolling back) when a step before the
    commit fails.
    """
    owner = room_ids.owner_from_claims(
        claims,
        claim=settings.owner_claim,
        flatten_value=settings.flatten_owner,
    )
    room_id = room_ids.make_room_id(
        owner, slug if slug is not None else room_ids.slugify(name)
    )
    description = name if description is None else description
    config_text = provisioned_room.render_room_config(
        room_id, name=name, description=description
    )
    request = provision_record.RoomRequest(
        owner=owner,
        room_id=room_id,
        name=name,
        description=description,
        requested_at=now or datetime.datetime.now(datetime.UTC),
        requester_name=str(claims["name"]) if claims.get("name") else None,
    )
    record_kwargs = {
        "data_dir": settings.ingester_data_dir,
        "uploads_root": settings.ingester_uploads_root,
    }

    check_admin_access(settings)

    gitea_settings = settings.gitea
    issues = concierge_issues() if gitea_settings is not None else None
    room = str(room_id)
    room_dir = settings.clone / room

    with landing_clone.locked(settings.clone):
        landing_clone.ensure_clone(
            settings.clone,
            origin_url=gitea_settings.origin_url if gitea_settings else None,
        )
        if room_dir.exists():
            raise RoomExists(room)
        check_clashes(settings, room)

        issue = None
        if issues is not None:
            create, _comment, _close = issues
            try:
                issue = create(
                    host=gitea_settings.host,
                    token=gitea_settings.token,
                    owner=gitea_settings.owner,
                    repo=gitea_settings.repo,
                    user=claims,
                    title=provision_record.title(request),
                    body=provision_record.render(request, **record_kwargs),
                    request_type=REQUEST_TYPE,
                )
            except Exception as error:
                raise IssueFilingFailed(error) from error

        rollback = _Rollback()
        try:
            rooms.install_room(
                environment=settings.environment,
                parent_path=str(settings.clone),
                room_id=room,
                config_text=config_text,
            )
            rollback.add("remove the room directory", shutil.rmtree, room_dir)
            (room_dir / provision_record.RECORD_FILE).write_text(
                provision_record.render(
                    request,
                    issue_url=issue.url if issue else None,
                    **record_kwargs,
                )
            )

            uploads = settings.uploads_dir / room
            uploads.mkdir(parents=True, exist_ok=True)
            rollback.add(
                "remove the uploads directory", _rmdir_if_empty, uploads
            )

            # -----------------------------------------------------------------
            # BEGIN: ingester integration
            #
            # Note that updating the ingester service config with the new
            # database config and triggering its reload has a side effect
            # that we rely on:  it ensures that the empty database is created.
            # -----------------------------------------------------------------
            original = settings.ingester_config.read_text()
            registered, _changed = ingester.register_database(
                original,
                room_id,
                data_dir=settings.ingester_data_dir,
                uploads_root=settings.ingester_uploads_root,
            )
            # Write in place: the config is a single-file bind mount.
            settings.ingester_config.write_text(registered)
            rollback.add(
                "restore the ingester config",
                _undo_ingester,
                settings,
                original,
                room,
            )
            ingester.reload(settings.ingester_url, settings.ingester_token)
            # -----------------------------------------------------------------
            # END: ingester integration
            # -----------------------------------------------------------------

            _set_policy(settings, room, owner_policy(room, owner))
            rollback.add(
                "remove the room's authz policy",
                _set_policy,
                settings,
                room,
                "null",
            )

            _audit_rooms(settings)

            author_name, author_email = _author(claims, owner)
            commit = landing_clone.commit_room(
                settings.clone,
                room,
                author_name=author_name,
                author_email=author_email,
                message=_commit_message(
                    room, name, gitea_settings, issue and issue.number
                ),
            )
        except Exception as error:
            failures = rollback.run()
            failed = ProvisioningFailed(room, error, failures)
            if issue is not None:
                _create, comment, _close = issues
                with contextlib.suppress(Exception):
                    comment(
                        gitea_settings.host,
                        gitea_settings.token,
                        gitea_settings.owner,
                        gitea_settings.repo,
                        issue.number,
                        str(failed),
                    )
            raise failed from error

        incomplete = list(_finish(settings, issues, issue, room))

    return Provisioned(
        room_id=room,
        room_dir=room_dir,
        commit=commit,
        issue_url=issue.url if issue else None,
        incomplete=tuple(incomplete),
    )


def _rmdir_if_empty(path: pathlib.Path) -> None:
    with contextlib.suppress(OSError):
        path.rmdir()


def _finish(settings, issues, issue, room) -> collections.abc.Iterator[str]:
    """Run the steps after the commit; yield how to finish each that fails."""
    if settings.gitea is not None:
        try:
            landing_clone.push(settings.clone, token=settings.gitea.token)
        except landing_clone.LandingCloneError as error:
            yield f"push the provisioned-rooms clone ({error})"

    try:
        delta = reload_backend_rooms(settings)
    except ProvisionError as error:
        yield (
            f"reload the backend's rooms: POST /api/v1/installation/reload/"
            f"rooms as an admin, or restart the backend ({error})"
        )
    else:
        loaded = [*delta.get("added", ()), *delta.get("changed", ())]
        if room not in loaded:
            yield (
                f"make the backend load room {room!r}: its rooms reload did "
                f"not (does 'room_paths' list {str(settings.clone)!r}?)"
            )

    if issue is not None:
        _create, comment, close = issues
        try:
            comment(
                settings.gitea.host,
                settings.gitea.token,
                settings.gitea.owner,
                settings.gitea.repo,
                issue.number,
                f"Provisioned room `{room}`.",
            )
            close(
                settings.gitea.host,
                settings.gitea.token,
                settings.gitea.owner,
                settings.gitea.repo,
                issue.number,
            )
        except Exception as error:
            yield f"comment on and close {issue.url} ({error})"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
_USER_ERRORS = (
    ProvisionError,
    room_ids.RoomIdError,
    ingester.IngesterError,
    landing_clone.LandingCloneError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soliplex-provision-room",
        description="Provision a private RAG room for a requester.",
    )
    parser.add_argument(
        "--claims",
        required=True,
        type=json.loads,
        help="the requester's token claims, as a JSON object",
    )
    parser.add_argument(
        "--name", required=True, help="the room's human-readable name"
    )
    parser.add_argument("--description", help="defaults to the name")
    parser.add_argument("--slug", help="defaults to one derived from the name")
    return parser


def main(argv=None, environ=None) -> int:
    """Provision one room.

    Exits 0 when the room is live, 1 when steps remain, 2 on an error.
    """
    args = build_parser().parse_args(argv)
    if not isinstance(args.claims, dict):
        print("error: --claims must be a JSON object", file=sys.stderr)
        return 2
    try:
        settings = Settings.from_env(
            os.environ if environ is None else environ
        )
        result = provision_room(
            args.claims,
            name=args.name,
            settings=settings,
            description=args.description,
            slug=args.slug,
        )
    except _USER_ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"provisioned room {result.room_id!r} (commit {result.commit[:12]})")
    if result.issue_url is not None:
        print(f"tracking issue: {result.issue_url}")
    if result.incomplete:
        print("still to do:")
        for step in result.incomplete:
            print(f"  - {step}")
        return 1
    return 0
