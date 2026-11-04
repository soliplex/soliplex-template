"""The template's adapters for the concierge's provisioner core.

A generated stack provisions a room in its ``soliplex-dev`` image (the
``soliplex-provisioner`` service, or a one-off ``soliplex-provision-room``
run), where the backend's installation is ``/environment``, the stack's
``src/`` is ``/app/src``, and the backend, the ingester and (optionally)
Gitea are reachable over the compose network. Each adapter here implements
one of :mod:`soliplex_concierge.provisioner.interfaces`, over the concierge's
toolkit, writing what :mod:`soliplex_template.provisioner.rendering` renders:

- :class:`GitRoomStore`: the provisioned-rooms clone
  (``src/rooms-provisioned``)
- :class:`HaikuRagRegistrar`: the room's database, registered with the
  ``haiku-ingester`` by a fragment in the clone, then a reload
- :class:`LocalUploads`: the room's directory under the stack's uploads
- :class:`SoliplexCliAuthz`: the room's authz policy, through ``soliplex-cli``
- :class:`SoliplexBackend`: the backend's installation API, as the
  provisioner's own service-token subject
- :class:`ConciergeGiteaTracker`: the request's tracking issue on Gitea
- :class:`TemplateAdmission`: who asks for a room, and whether they may
"""

from __future__ import annotations

import collections.abc
import concurrent.futures
import contextlib
import dataclasses
import json
import pathlib
import posixpath
import shutil

from soliplex.config import installation
from soliplex_concierge import gitea_admin
from soliplex_concierge.provisioner import api
from soliplex_concierge.provisioner import ingester
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import landing_clone
from soliplex_concierge.provisioner import room_ids
from soliplex_concierge.provisioner import soliplex_api
from soliplex_concierge.provisioner import soliplex_cli
from soliplex_concierge.tools import gitea as concierge_gitea
from soliplex_plumber import rooms

from soliplex_template import gitea
from soliplex_template.provisioner import rendering

REQUEST_TYPE = "new-room"
DEFAULT_SERVICE_SUBJECT = "soliplex-provisioner"
RELOAD_ROOMS_PATH = "/api/v1/installation/reload/rooms"


# ---------------------------------------------------------------------------
# Errors, with the template's advice
# ---------------------------------------------------------------------------
class AdapterError(Exception):
    """A user-facing error (printed without a traceback)."""


class AdminAccessDenied(AdapterError, interfaces.AccessRefused):
    def __init__(self, code, subject, mint_error=None):
        self.code, self.subject, self.mint_error = code, subject, mint_error
        message = (
            f"the backend refused the provisioner's admin access (HTTP "
            f"{code}): run 'docker compose run --rm --quiet-build "
            f"stack-init', which adds the 'admin_users' entry matching its "
            f"service tokens, '$[?$.sub == {json.dumps(subject)}]'"
        )
        if mint_error is not None:
            message += f"; minting its service token failed: {mint_error}"
        super().__init__(message)


class DatabaseNotAdded(AdapterError):
    def __init__(self, room_id, report):
        self.room_id, self.report = room_id, report
        super().__init__(
            f"the ingester's reload did not add database {room_id!r} (is the "
            "provisioned-rooms clone mounted where the ingester config's "
            f"'databases_include' looks?): {json.dumps(report)}"
        )


class RegistrationFailed(AdapterError):
    """Registering the room's database failed; the attempt was undone.

    ``undo_error`` is set when undoing it failed too, leaving the room's
    fragment (or database) for an operator to remove. A reload that timed
    out may still apply after the undo, which the message says.
    """

    def __init__(self, room_id, error, undo_error=None):
        self.room_id, self.error, self.undo_error = room_id, error, undo_error
        message = (
            f"registering the database of room {room_id!r} failed: {error}"
        )
        if undo_error is not None:
            message += (
                "; removing its ingester fragment failed too "
                f"({undo_error}): remove it, then reload the ingester"
            )
        if isinstance(error, ingester.ReloadTimedOut):
            message += (
                "; the timed-out reload may still apply: once the ingester "
                "answers, reload it again and remove the room's database "
                "directory if one appears"
            )
        super().__init__(message)


class IssueFilingFailed(AdapterError):
    def __init__(self, error):
        self.error = error
        super().__init__(f"filing the tracking issue failed: {error}")


# ---------------------------------------------------------------------------
# Gitea
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class GiteaSettings:
    """The Gitea holding tracking issues and the provisioned-rooms repo."""

    host: str
    token: str = dataclasses.field(repr=False)
    owner: str = gitea.ADMIN_USER
    repo: str = gitea.REPO_NAME
    rooms_repo: str = gitea.ROOMS_REPO_NAME

    @property
    def origin_url(self) -> str:
        """The provisioned-rooms clone's ``origin``."""
        return f"{self.host.rstrip('/')}/{self.owner}/{self.rooms_repo}.git"

    def reference(self, number: int) -> str:
        """A tracking issue's short reference, e.g. ``ops/requests#7``."""
        return f"{self.owner}/{self.repo}#{number}"


# ---------------------------------------------------------------------------
# The room store: the provisioned-rooms clone
# ---------------------------------------------------------------------------
def _author(
    claims: collections.abc.Mapping, identity_claim: str
) -> tuple[str, str]:
    """The commit author for a requester: their name and email.

    Falls back to their identifying claim's value, and an address made from
    it.
    """
    identity = claims.get(identity_claim)
    identity = identity if isinstance(identity, str) else "unidentified"
    name = claims.get("name")
    email = claims.get("email")
    return (
        name if isinstance(name, str) and name.strip() else identity,
        email
        if isinstance(email, str) and email.strip()
        else f"{room_ids.flatten(identity)}@users.soliplex.invalid",
    )


@dataclasses.dataclass(frozen=True)
class GitRoomStore:
    """Provisioned rooms as directories of a git clone, under its lock.

    ``environment`` is the backend's installation, whose ``room_paths``
    lists ``clone``; ``identity_claim`` names the requester's claim that
    identifies them; ``gitea`` (if any) holds the clone's ``origin``.
    """

    clone: pathlib.Path
    environment: pathlib.Path
    identity_claim: str
    gitea: GiteaSettings | None = None
    data_dir: str = rendering.DEFAULT_DATA_DIR
    uploads_root: str = rendering.DEFAULT_UPLOADS_ROOT

    def locked(self) -> contextlib.AbstractContextManager:
        return landing_clone.locked(self.clone)

    def prepare(self) -> None:
        landing_clone.ensure_clone(
            self.clone,
            origin_url=self.gitea.origin_url if self.gitea else None,
        )

    def has_room(self, request: interfaces.RoomRequest) -> bool:
        return (self.clone / request.room).exists()

    def clashes(self, request: interfaces.RoomRequest) -> list[str]:
        """None: the room's directory is :meth:`has_room`'s to check, and
        its fragment the registrar's."""
        return []

    def add_room(
        self, request: interfaces.RoomRequest, issue: interfaces.Issue | None
    ) -> interfaces.Undo:
        rooms.install_room(
            environment=self.environment,
            parent_path=str(self.clone),
            room_id=request.room,
            config_text=rendering.room_config(request),
        )
        room_dir = self.clone / request.room
        (room_dir / rendering.RECORD_FILE).write_text(
            rendering.record(
                request,
                identity_claim=self.identity_claim,
                data_dir=self.data_dir,
                uploads_root=self.uploads_root,
                issue_url=issue.url if issue is not None else None,
            )
        )
        (room_dir / rendering.PROVISION_FILE).write_text(
            rendering.provision_record(
                request,
                identity_claim=self.identity_claim,
                issue_url=issue.url if issue is not None else None,
            )
        )
        return interfaces.Undo(
            description="remove the room directory",
            run=lambda: shutil.rmtree(room_dir),
        )

    def commit(
        self, request: interfaces.RoomRequest, issue: interfaces.Issue | None
    ) -> str:
        fragment = posixpath.join(
            rendering.FRAGMENTS_DIR, rendering.fragment_name(request.room)
        )
        reference = (
            self.gitea.reference(issue.number)
            if issue is not None and self.gitea is not None
            else None
        )
        author_name, author_email = _author(
            request.requester_claims, self.identity_claim
        )
        return landing_clone.commit_room(
            self.clone,
            request.room,
            author_name=author_name,
            author_email=author_email,
            message=rendering.commit_message(
                request, tracking_reference=reference
            ),
            also=(fragment,) if (self.clone / fragment).exists() else (),
        )

    def publish(self) -> list[str]:
        if self.gitea is None:
            return []
        try:
            landing_clone.push(self.clone, token=self.gitea.token)
        except landing_clone.LandingCloneError as error:
            return [f"push the provisioned-rooms clone ({error})"]
        return []


# ---------------------------------------------------------------------------
# The database registrar: haiku.rag fragments in the clone
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class HaikuRagRegistrar:
    """A room's database, registered with the ingester by a fragment.

    The fragment sits in the clone's :data:`rendering.FRAGMENTS_DIR`, which
    the ingester's config (``ingester_config``, as the provisioner sees it)
    includes as ``ingester_fragments_dir`` (as the ingester sees it, relative
    to ``ingester_config_dir``). Reloading the ingester opens the database,
    creating it under ``db_dir`` (as the provisioner sees it).
    """

    clone: pathlib.Path
    db_dir: pathlib.Path
    ingester_config: pathlib.Path
    ingester_config_dir: str
    ingester_fragments_dir: str
    ingester_url: str
    ingester_token: str = dataclasses.field(repr=False)
    data_dir: str = rendering.DEFAULT_DATA_DIR
    uploads_root: str = rendering.DEFAULT_UPLOADS_ROOT

    def _fragment(self, room: str) -> pathlib.Path:
        name = rendering.fragment_name(room)
        return self.clone / rendering.FRAGMENTS_DIR / name

    def clashes(self, request: interfaces.RoomRequest) -> list[str]:
        """The room's database directory, its fragment, an entry for it in
        either map of the ingester config or its fragments, or another
        database placed at its location.

        Raises :class:`ingester.UnusableConfig` for an ingester config the
        fragments cannot work with.
        """
        room = request.room
        found = []

        database = self.db_dir / f"{room}.lancedb"
        if database.exists():
            found.append(f"database directory {database}")

        config = self.ingester_config.read_text()
        ingester.check_config(
            config,
            config_dir=self.ingester_config_dir,
            fragments_dir=self.ingester_fragments_dir,
        )
        fragment = self._fragment(room)
        if fragment.exists():
            found.append(f"ingester fragment {fragment}")
        registered = ingester.registrations(
            config,
            *ingester.fragment_texts(self.clone / rendering.FRAGMENTS_DIR),
        )
        if room in registered.sources:
            found.append(f"ingester config entry 'ingester.databases.{room}'")
        if room in registered.locations:
            found.append(f"ingester config entry 'lancedb.databases.{room}'")
        location = posixpath.normpath(f"{self.data_dir}/{room}.lancedb")
        for name, placed in sorted(registered.locations.items()):
            if name != room and placed is not None:
                if posixpath.normpath(placed) == location:
                    found.append(
                        f"ingester config entry 'lancedb.databases.{name}' "
                        f"placed at {location}"
                    )
        return found

    def _undo(
        self, fragment: pathlib.Path, database: pathlib.Path | None
    ) -> None:
        """Remove the fragment and reload; remove ``database`` too, when
        given (this run's reload created it)."""
        fragment.unlink(missing_ok=True)
        ingester.reload(self.ingester_url, self.ingester_token)
        if database is not None:
            shutil.rmtree(database, ignore_errors=True)

    def register(self, request: interfaces.RoomRequest) -> interfaces.Undo:
        """Write the room's fragment, and reload the ingester.

        The reload creates the empty database. Raises
        :class:`RegistrationFailed` (having undone the attempt) when the
        reload fails, or does not add the room's database.
        """
        room = request.room
        fragment = self._fragment(room)
        database = self.db_dir / f"{room}.lancedb"
        created = None if database.exists() else database
        fragment.parent.mkdir(exist_ok=True)
        fragment.write_text(
            rendering.fragment(
                request, data_dir=self.data_dir, uploads_root=self.uploads_root
            )
        )
        try:
            report = ingester.reload(self.ingester_url, self.ingester_token)
            _check_added(report, room)
        except Exception as error:
            try:
                self._undo(fragment, created)
            except Exception as undo_error:
                raise RegistrationFailed(room, error, undo_error) from error
            raise RegistrationFailed(room, error) from error
        return interfaces.Undo(
            description="remove the room's ingester fragment",
            run=lambda: self._undo(fragment, created),
        )


def _check_added(report, room: str) -> None:
    """Raise :class:`DatabaseNotAdded` unless the reload added ``room``."""
    changes = report.get("databases", []) if isinstance(report, dict) else []
    for change in changes:
        if change.get("database") == room and change.get("change") == "added":
            return
    raise DatabaseNotAdded(room, report)


# ---------------------------------------------------------------------------
# The uploads area
# ---------------------------------------------------------------------------
def _rmdir_if_empty(path: pathlib.Path) -> None:
    with contextlib.suppress(OSError):
        path.rmdir()


@dataclasses.dataclass(frozen=True)
class LocalUploads:
    """A room's uploads: a directory under ``root``, named for the room."""

    root: pathlib.Path

    def clashes(self, request: interfaces.RoomRequest) -> list[str]:
        uploads = self.root / request.room
        if uploads.is_dir() and any(uploads.iterdir()):
            return [f"non-empty uploads directory {uploads}"]
        return []

    def create(
        self, request: interfaces.RoomRequest
    ) -> interfaces.Undo | None:
        uploads = self.root / request.room
        if uploads.exists():
            return None
        uploads.mkdir(parents=True)
        return interfaces.Undo(
            description="remove the uploads directory",
            run=lambda: _rmdir_if_empty(uploads),
        )


# ---------------------------------------------------------------------------
# Authorization, through soliplex-cli
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class SoliplexCliAuthz:
    """Room policies, through ``cli`` against the installation.

    ``environment`` is the installation's directory as ``cli`` sees it;
    ``clone`` the provisioned-rooms clone, where each room's policy is also
    recorded, as its ``authz.yaml``.
    """

    cli: str
    environment: pathlib.Path
    clone: pathlib.Path

    def clashes(self, request: interfaces.RoomRequest) -> list[str]:
        stale = soliplex_cli.stale_policy_ids(self.cli, str(self.environment))
        if request.room in stale:
            return [f"an authz policy for {request.room!r}"]
        return []

    def grant_owner(self, request: interfaces.RoomRequest) -> interfaces.Undo:
        """Record the room's owner-only policy in the clone, then store it.

        When storing it fails, the record is removed again.
        """
        environment = str(self.environment)
        policy = soliplex_cli.owner_policy(
            request.room, request.owner.json_path
        )
        record = self.clone / request.room / rendering.AUTHZ_FILE
        record.write_text(rendering.authz_document(policy))
        try:
            soliplex_cli.set_policy(
                self.cli, environment, request.room, policy
            )
        except Exception:
            record.unlink(missing_ok=True)
            raise

        def undo():
            soliplex_cli.remove_policy(self.cli, environment, request.room)
            record.unlink(missing_ok=True)

        return interfaces.Undo(
            description="remove the room's authz policy", run=undo
        )

    def audit(self) -> None:
        soliplex_cli.audit_rooms(self.cli, str(self.environment))

    def predicates(self) -> frozenset[str]:
        """The predicates the installation registers, defaults included.

        Loading the installation's config registers its
        ``meta.jsonpath_functions`` with soliplex's JSONPath environment, as
        the backend's loading does; the defaults count too, unless the
        installation's ``$$CLEAR$$`` wipes them. ``environment`` is read
        here as the provisioner sees it.
        """
        installation.load_installation(self.environment)
        return soliplex_api.registered_predicates()


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class SoliplexBackend:
    """The backend's installation API, as the provisioner.

    Each call carries a service token for ``service_subject``, minted with
    the secret in ``secret_file``; when it cannot be minted, the call goes
    without one (which a backend with no OIDC provider accepts as its no-auth
    user), and a refusal reports why. ``clone`` names the rooms' parent in
    the advice when a reload does not load a room.
    """

    base_url: str
    secret_file: pathlib.Path
    clone: pathlib.Path
    service_subject: str = DEFAULT_SERVICE_SUBJECT

    def _token(self) -> tuple[str | None, str | None]:
        try:
            secret = soliplex_api.read_secret(self.secret_file)
        except soliplex_api.SecretUnreadable as error:
            return None, str(error)
        token = soliplex_api.mint_service_token(
            secret, {"sub": self.service_subject}
        )
        return token, None

    def check_access(self) -> None:
        """Raise :class:`AdminAccessDenied` unless the backend admits us."""
        token, mint_error = self._token()
        try:
            soliplex_api.check_access(self.base_url, token=token)
        except soliplex_api.AccessDenied as error:
            raise AdminAccessDenied(
                error.code, self.service_subject, mint_error
            ) from error

    def reload_rooms(self, request: interfaces.RoomRequest) -> list[str]:
        token, _mint_error = self._token()
        try:
            delta = soliplex_api.reload_rooms(self.base_url, token=token)
        except soliplex_api.BackendError as error:
            return [
                f"reload the backend's rooms: POST {RELOAD_ROOMS_PATH} as an "
                f"admin, or restart the backend ({error})"
            ]
        if not soliplex_api.loaded(delta, request.room):
            return [
                f"make the backend load room {request.room!r}: its rooms "
                f"reload did not (does 'room_paths' list "
                f"{str(self.clone)!r}?)"
            ]
        return []


# ---------------------------------------------------------------------------
# The tracking issue, on Gitea
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ConciergeGiteaTracker:
    """Tracking issues on Gitea, through the concierge's Gitea calls."""

    settings: GiteaSettings
    identity_claim: str
    data_dir: str = rendering.DEFAULT_DATA_DIR
    uploads_root: str = rendering.DEFAULT_UPLOADS_ROOT

    def _repo(self) -> tuple[str, str, str, str]:
        s = self.settings
        return s.host, s.token, s.owner, s.repo

    def file(
        self, request: interfaces.RoomRequest
    ) -> concierge_gitea.CreatedGiteaIssue:
        host, token, owner, repo = self._repo()
        try:
            return concierge_gitea.create_gitea_issue_sync(
                host=host,
                token=token,
                owner=owner,
                repo=repo,
                user=request.requester_claims,
                title=rendering.issue_title(request),
                body=rendering.record(
                    request,
                    identity_claim=self.identity_claim,
                    data_dir=self.data_dir,
                    uploads_root=self.uploads_root,
                ),
                request_type=REQUEST_TYPE,
            )
        except Exception as error:
            raise IssueFilingFailed(error) from error

    def report_failure(self, issue, error: Exception) -> None:
        gitea_admin.comment_issue(*self._repo(), issue.number, str(error))

    def resolve(self, issue, provisioned: interfaces.Provisioned) -> list[str]:
        try:
            gitea_admin.comment_issue(
                *self._repo(),
                issue.number,
                rendering.provisioned_comment(provisioned),
            )
            gitea_admin.close_issue(*self._repo(), issue.number)
        except Exception as error:
            return [f"comment on and close {issue.url} ({error})"]
        return []


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class TemplateAdmission:
    """Who is asking, from their service token, and whether they may.

    Tokens are validated with the secret in ``secret_file``; admission is
    checked with ``cli`` against the installation (``environment``, as
    ``cli`` sees it).
    """

    secret_file: pathlib.Path
    cli: str
    environment: pathlib.Path

    def claims(self, token: str) -> dict:
        secret = soliplex_api.read_secret(self.secret_file)
        return soliplex_api.validate_service_token(secret, token)

    def is_admin(self, token: str) -> bool:
        return soliplex_cli.is_admin(self.cli, str(self.environment), token)

    def _may_request(self, token: str) -> bool:
        return soliplex_cli.may(
            self.cli,
            str(self.environment),
            token,
            api.ADMISSION_ROOM_ID,
            "write",
        )

    def is_admitted(self, token: str) -> bool:
        """An admin, or WRITE on ``__provision__``.

        The two checks run concurrently; an admin is admitted whatever the
        other says.
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            admin = pool.submit(self.is_admin, token)
            writer = pool.submit(self._may_request, token)
            if admin.result():
                return True
            return writer.result()
