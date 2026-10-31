"""The room provisioner as an HTTP service, provisioning on a user's behalf.

A caller holding the stack's ``url_safe_token_secret`` (the concierge's
tool, in the backend) mints a short-lived service token carrying the
requesting user's claims, and sends it as the bearer token:

- ``POST /rooms`` with ``{"name": ..., "description": ..., "slug": ...}``
  (the last two optional) answers ``202`` with ``{"room_id": ...,
  "issue_url": ...}`` once the request is admitted (see
  :func:`provision.admit_room`), then creates the room in the background
  (:func:`provision.create_room`)
- ``GET /rooms/{room_id}`` answers the request's state: ``pending``,
  ``live``, ``incomplete`` (with the steps still to do) or ``failed`` (with
  the error)
- ``GET /ok`` answers ``OK``, for health checks, once the backend admits
  the provisioner's own service tokens; until then it answers 503, saying
  why (see :func:`watch_access`)

A requester is admitted when they are an admin, or hold WRITE on the
reserved room id ``__provision__``. The token is validated in-process,
against the stack's ``url_safe_token_secret`` as the backend reads it;
admission is checked with ``soliplex-cli``'s ``admin-users check`` and
``room-authz check``, each reading the token on stdin.

Request state lives in memory: a restart loses it, while the tracking
issue remains the record.

``uvicorn --factory soliplex_template.provision_service:create_app`` serves
it, configured from the environment (see :meth:`provision.Settings.from_env`).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import dataclasses
import enum
import json
import logging
import os
import threading
import time
import typing

import fastapi
import pydantic

from soliplex_template import provision
from soliplex_template import room_ids

ADMISSION_ROOM = "__provision__"
# Seconds between checks of the provisioner's own backend access: while
# the backend refuses it, and while the backend cannot be reached.
ACCESS_RETRY = 15
BACKEND_RETRY = 5

logger = logging.getLogger(__name__)


class TokenInvalid(provision.ProvisionError):
    def __init__(self, output):
        self.output = output
        super().__init__(f"the service token is not valid: {output}")


class CheckFailed(provision.ProvisionError):
    """A check gave no verdict (e.g., a stale ACL entry)."""

    def __init__(self, command, output):
        self.command, self.output = command, output
        super().__init__(
            f"'soliplex-cli {' '.join(command)}' gave no verdict:\n{output}"
        )


# --------------------------------------------------------------------------
# Checks, through soliplex-cli
# --------------------------------------------------------------------------
def validate_token(settings: provision.Settings, token: str) -> dict:
    """The claims of the service token ``token``.

    Raises :class:`TokenInvalid` when it is not valid, and
    :class:`provision.SigningSecretUnreadable` when the signing secret
    cannot be read.
    """
    # Imported here: soliplex is in the stack's image, where this runs,
    # not among this package's dependencies.
    from soliplex import token_mint

    secret = provision.service_token_secret(settings)
    claims, reason = token_mint.load_service_token(secret, token)
    if claims is None:
        raise TokenInvalid(reason)
    return claims


def _verdict(settings, command, key, token) -> bool:
    output = provision.run_cli(
        settings, *command, stdin=token, ok_codes=(0, 1)
    )
    try:
        verdict = json.loads(output)[key]
    except (json.JSONDecodeError, TypeError, KeyError) as error:
        raise CheckFailed(command, output.strip()) from error
    return verdict is True


def is_admin(settings: provision.Settings, token: str) -> bool:
    """Is the user of the service token ``token`` an admin?"""
    command = ("admin-users", "--quiet", "check", str(settings.environment))
    return _verdict(settings, command, "is_admin", token)


def may_write_admission_room(settings: provision.Settings, token: str) -> bool:
    """Does the user of the service token ``token`` hold WRITE on
    :data:`ADMISSION_ROOM`?"""
    command = (
        "room-authz",
        "--quiet",
        "check",
        str(settings.environment),
        ADMISSION_ROOM,
        "--permission",
        "write",
    )
    return _verdict(settings, command, "allowed", token)


def is_admitted(settings: provision.Settings, token: str) -> bool:
    """May the user of the service token ``token`` request a room?

    That is, are they an admin, or do they hold WRITE on
    :data:`ADMISSION_ROOM`? The two checks run concurrently; an admin is
    admitted whatever the other says. Raises :class:`CheckFailed` when a
    check that decides gives no verdict.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        admin = pool.submit(is_admin, settings, token)
        writer = pool.submit(may_write_admission_room, settings, token)
        if admin.result():
            return True
        return writer.result()


# --------------------------------------------------------------------------
# The provisioner's own access
# --------------------------------------------------------------------------
class Access:
    """Whether the backend admits the provisioner's own service tokens.

    ``problem`` says why not, while it does not.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._admitted = False
        self._problem = "the backend has not been checked yet"

    def state(self) -> tuple[bool, str | None]:
        with self._lock:
            return self._admitted, self._problem

    def update(self, admitted: bool, problem: str | None = None) -> None:
        with self._lock:
            self._admitted, self._problem = admitted, problem


def watch_access(
    settings: provision.Settings, access: Access, *, sleep=time.sleep
) -> None:
    """Check the provisioner's backend access until the backend admits it.

    Records each outcome in ``access``, and logs each new reason it is
    refused: an error, naming the ``admin_users`` entry it needs, when the
    backend refuses it; a warning while the backend cannot be reached.
    """
    logged = None
    while True:
        try:
            provision.check_admin_access(settings)
        except provision.AdminAccessDenied as error:
            problem, level, delay = str(error), logging.ERROR, ACCESS_RETRY
        except provision.ProvisionError as error:
            problem, level, delay = str(error), logging.WARNING, BACKEND_RETRY
        else:
            access.update(True)
            logger.info("the backend admits the provisioner")
            return
        access.update(False, problem)
        if problem != logged:
            logger.log(level, "%s", problem)
            logged = problem
        sleep(delay)


# --------------------------------------------------------------------------
# Request state
# --------------------------------------------------------------------------
class State(enum.StrEnum):
    PENDING = "pending"
    LIVE = "live"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclasses.dataclass
class Request:
    """A room request's state; ``owner`` is its room id's owner part."""

    room_id: str
    owner: str
    issue_url: str | None = None
    state: State = State.PENDING
    incomplete: tuple[str, ...] = ()
    error: str | None = None


class Requests:
    """The service's room requests, by room id; safe across threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, Request] = {}

    def get(self, room_id: str) -> Request | None:
        with self._lock:
            return self._requests.get(room_id)

    def reserve(self, room_id: str, owner: str) -> bool:
        """Record a pending request; False when one is pending already."""
        with self._lock:
            current = self._requests.get(room_id)
            if current is not None and current.state == State.PENDING:
                return False
            self._requests[room_id] = Request(room_id=room_id, owner=owner)
            return True

    def release(self, room_id: str) -> None:
        """Forget a request that was not admitted."""
        with self._lock:
            self._requests.pop(room_id, None)

    def update(self, room_id: str, **changes) -> None:
        with self._lock:
            request = self._requests[room_id]
            self._requests[room_id] = dataclasses.replace(request, **changes)


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------
class RoomRequestBody(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    slug: str | None = None


class Accepted(pydantic.BaseModel):
    room_id: str
    issue_url: str | None


class RequestState(pydantic.BaseModel):
    room_id: str
    state: State
    issue_url: str | None
    incomplete: list[str]
    error: str | None


def _http_error(status: int, error: Exception) -> fastapi.HTTPException:
    return fastapi.HTTPException(status_code=status, detail=str(error))


def _settings(request: fastapi.Request) -> provision.Settings:
    return request.app.state.settings


def _requests(request: fastapi.Request) -> Requests:
    return request.app.state.requests


def _access(request: fastapi.Request) -> Access:
    return request.app.state.access


def _bearer_token(
    authorization: typing.Annotated[str | None, fastapi.Header()] = None,
) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise fastapi.HTTPException(
            status_code=401,
            detail="a bearer service token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


SettingsDep = typing.Annotated[provision.Settings, fastapi.Depends(_settings)]
RequestsDep = typing.Annotated[Requests, fastapi.Depends(_requests)]
AccessDep = typing.Annotated[Access, fastapi.Depends(_access)]
TokenDep = typing.Annotated[str, fastapi.Depends(_bearer_token)]


def _claims(settings: provision.Settings, token: str) -> dict:
    try:
        return validate_token(settings, token)
    except TokenInvalid as error:
        logger.info("refused a service token: %s", error.output)
        raise fastapi.HTTPException(
            status_code=401,
            detail="the service token is not valid",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except provision.SigningSecretUnreadable as error:
        logger.exception("validating a service token failed")
        raise _http_error(500, error) from error


def _create(
    settings: provision.Settings,
    requests: Requests,
    admission: provision.Admission,
) -> None:
    """Create the admitted room, recording the outcome."""
    room = admission.plan.room
    try:
        result = provision.create_room(admission, settings)
    except Exception as error:
        logger.exception("provisioning room %r failed", room)
        requests.update(room, state=State.FAILED, error=str(error))
        return
    requests.update(
        room,
        state=State.INCOMPLETE if result.incomplete else State.LIVE,
        incomplete=result.incomplete,
    )


router = fastapi.APIRouter()


@router.get("/ok", response_class=fastapi.responses.PlainTextResponse)
def health_check(access: AccessDep) -> fastapi.responses.PlainTextResponse:
    """Is the service up, and does the backend admit the provisioner?

    Answers 503, saying why, until the backend does.
    """
    admitted, problem = access.state()
    if not admitted:
        return fastapi.responses.PlainTextResponse(problem, status_code=503)
    return fastapi.responses.PlainTextResponse("OK")


@router.post("/rooms", status_code=202)
def post_room(
    body: RoomRequestBody,
    background: fastapi.BackgroundTasks,
    settings: SettingsDep,
    requests: RequestsDep,
    token: TokenDep,
) -> Accepted:
    """Admit a room request, then create the room in the background."""
    claims = _claims(settings, token)
    try:
        admitted = is_admitted(settings, token)
    except provision.ProvisionError as error:
        logger.exception("checking admission failed")
        raise _http_error(500, error) from error
    if not admitted:
        raise fastapi.HTTPException(
            status_code=403,
            detail="the requester may not request rooms",
        )

    try:
        plan = provision.plan_room(
            claims,
            name=body.name,
            settings=settings,
            description=body.description,
            slug=body.slug,
        )
    except room_ids.RoomIdError as error:
        raise _http_error(400, error) from error

    room = plan.room
    if not requests.reserve(room, plan.owner.owner):
        raise fastapi.HTTPException(
            status_code=409,
            detail=f"room {room!r} is already being provisioned",
        )
    try:
        admission = provision.admit_room(plan, settings)
    except (provision.RoomExists, provision.ResourcesExist) as error:
        requests.release(room)
        raise _http_error(409, error) from error
    except provision.USER_ERRORS as error:
        requests.release(room)
        logger.exception("admitting room %r failed", room)
        raise _http_error(500, error) from error

    issue_url = admission.issue.url if admission.issue else None
    requests.update(room, issue_url=issue_url)
    background.add_task(_create, settings, requests, admission)
    return Accepted(room_id=room, issue_url=issue_url)


@router.get("/rooms/{room_id}")
def get_room(
    room_id: str,
    settings: SettingsDep,
    requests: RequestsDep,
    token: TokenDep,
) -> RequestState:
    """The state of a room request, for its requester or an admin."""
    claims = _claims(settings, token)
    request = requests.get(room_id)
    not_found = fastapi.HTTPException(
        status_code=404, detail=f"no request for room {room_id!r}"
    )
    if request is None:
        raise not_found
    try:
        owner = room_ids.owner_from_claims(
            claims,
            claim=settings.owner_claim,
            flatten_value=settings.flatten_owner,
        ).owner
    except room_ids.RoomIdError:
        owner = None
    if owner != request.owner:
        try:
            admin = is_admin(settings, token)
        except provision.ProvisionError as error:
            logger.exception("checking admin access failed")
            raise _http_error(500, error) from error
        if not admin:
            raise not_found
    return RequestState(
        room_id=request.room_id,
        state=request.state,
        issue_url=request.issue_url,
        incomplete=list(request.incomplete),
        error=request.error,
    )


@contextlib.asynccontextmanager
async def _lifespan(app: fastapi.FastAPI):
    """Watch the provisioner's backend access while the service runs."""
    threading.Thread(
        target=watch_access,
        args=(app.state.settings, app.state.access),
        name="provisioner-access",
        daemon=True,
    ).start()
    yield


def create_app(settings: provision.Settings | None = None) -> fastapi.FastAPI:
    """The service's application; ``settings`` default to the environment's."""
    app = fastapi.FastAPI(
        title="Soliplex room provisioner", lifespan=_lifespan
    )
    if settings is None:
        settings = provision.Settings.from_env(os.environ)
    app.state.settings = settings
    app.state.requests = Requests()
    app.state.access = Access()
    app.include_router(router)
    return app
