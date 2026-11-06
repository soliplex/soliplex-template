"""The template's room provisioner, wired from the stack's environment.

:class:`Settings` says where the provisioner finds the stack, from inside
the ``soliplex-dev`` image; :func:`build_provisioner` and
:func:`build_admission` make the template's adapters
(:mod:`soliplex_template.provisioner.adapters`) from it, for the concierge's
core. Two entry points use them:

- :func:`create_app`: the ``soliplex-provisioner`` service, for
  ``uvicorn --factory soliplex_template.provisioner.wiring:create_app``
- :func:`main`: the ``soliplex-provision-room`` command line, provisioning
  one room, or (``--reconcile``) making the stack match the clone (see
  :mod:`soliplex_template.provisioner.reconcile`)
"""

from __future__ import annotations

import argparse
import collections.abc
import dataclasses
import json
import os
import pathlib
import sys

import fastapi
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import ingester
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import landing_clone
from soliplex_concierge.provisioner import provision
from soliplex_concierge.provisioner import room_ids
from soliplex_concierge.provisioner import service
from soliplex_concierge.provisioner import soliplex_api
from soliplex_concierge.provisioner import soliplex_cli

from soliplex_template import gitea
from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import reconcile
from soliplex_template.provisioner import rendering

SERVICE_TOKEN_SECRET_FILE = "/run/secrets/url_safe_token_secret"
INGESTER_TOKEN_FILE = "/run/secrets/ingester_bearer"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


class SettingsError(Exception):
    """A user-facing error (printed without a traceback)."""


class IngesterTokenMissing(SettingsError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"cannot read the ingester's bearer token from {path} (is the "
            "'ingester_bearer' secret mounted?)"
        )


class GiteaTokenMissing(SettingsError):
    def __init__(self):
        super().__init__(
            "GITEA_HOST is set but GITEA_ACCESS_TOKEN is not: the provisioner "
            "needs a token that may write issues and push the rooms repository"
        )


class BadFlag(SettingsError):
    def __init__(self, name, value):
        self.name, self.value = name, value
        super().__init__(
            f"{name}={value!r}: expected one of "
            f"{', '.join(sorted(_TRUE | _FALSE - {''}))}"
        )


@dataclasses.dataclass(frozen=True)
class Settings:
    """Where the provisioner finds the stack, from inside ``soliplex-dev``.

    The ``ingester_*`` paths are as the ingester sees them, except
    ``ingester_config`` (the file both services mount); the others as the
    provisioner does. ``owner_claim`` names the requester's claim a room's
    owner part (and their identity in its records) comes from;
    ``lowercase_owner`` lowercases that part; ``room_id_separator`` joins it
    and the slug in the room id.
    """

    ingester_token: str = dataclasses.field(repr=False)
    environment: pathlib.Path = pathlib.Path("/environment")
    clone: pathlib.Path = pathlib.Path("/app/src/rooms-provisioned")
    uploads_dir: pathlib.Path = pathlib.Path("/uploads/rooms")
    db_dir: pathlib.Path = pathlib.Path("/db")
    ingester_config: pathlib.Path = pathlib.Path("/app/haiku.rag.yaml")
    ingester_config_dir: str = "/app"
    ingester_fragments_dir: str = (
        f"/app/rooms-provisioned/{rendering.FRAGMENTS_DIR}"
    )
    ingester_url: str = "http://haiku-ingester:8765"
    ingester_data_dir: str = rendering.DEFAULT_DATA_DIR
    ingester_uploads_root: str = rendering.DEFAULT_UPLOADS_ROOT
    backend_url: str = "http://backend:8000"
    service_subject: str = adapters.DEFAULT_SERVICE_SUBJECT
    owner_claim: str = room_ids.DEFAULT_OWNER_CLAIM
    flatten_owner: bool = False
    lowercase_owner: bool = True
    room_id_separator: str = room_ids.DEFAULT_SEPARATOR
    soliplex_cli: str = "/app/.venv/bin/soliplex-cli"
    service_token_secret_file: pathlib.Path = pathlib.Path(
        SERVICE_TOKEN_SECRET_FILE
    )
    gitea: adapters.GiteaSettings | None = None

    def __post_init__(self):
        access_rules.check_claim(self.owner_claim)
        room_ids.validate_separator(self.room_id_separator)

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
        - ``PROVISIONER_LOWERCASE_OWNER``: whether a room id's owner part
          is lowercased (default: yes)
        - ``PROVISIONER_ROOM_ID_SEPARATOR``: what joins the owner part and
          the slug (default: the concierge's, ``'--'``; ``':'`` keeps the
          legacy form)
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
            gitea_settings = adapters.GiteaSettings(
                host=setting("GITEA_HOST"),
                token=setting("GITEA_ACCESS_TOKEN"),
                owner=setting("GITEA_OWNER", gitea.ADMIN_USER),
                repo=setting("GITEA_REPO", gitea.REPO_NAME),
                rooms_repo=setting("GITEA_ROOMS_REPO", gitea.ROOMS_REPO_NAME),
            )

        flatten = setting("PROVISIONER_FLATTEN_OWNER", "").lower()
        if flatten not in _TRUE | _FALSE:
            raise BadFlag("PROVISIONER_FLATTEN_OWNER", flatten)

        lowercase = setting("PROVISIONER_LOWERCASE_OWNER", "").lower()
        if lowercase not in _TRUE | _FALSE:
            raise BadFlag("PROVISIONER_LOWERCASE_OWNER", lowercase)

        return cls(
            ingester_token=ingester_token,
            service_subject=setting(
                "PROVISIONER_SERVICE_SUBJECT", adapters.DEFAULT_SERVICE_SUBJECT
            ),
            owner_claim=setting(
                "PROVISIONER_OWNER_CLAIM", room_ids.DEFAULT_OWNER_CLAIM
            ),
            flatten_owner=flatten in _TRUE,
            lowercase_owner=(lowercase in _TRUE) if lowercase else True,
            room_id_separator=setting(
                "PROVISIONER_ROOM_ID_SEPARATOR", room_ids.DEFAULT_SEPARATOR
            ),
            gitea=gitea_settings,
        )


def build_provisioner(settings: Settings) -> provision.Provisioner:
    """The template's adapters, for the concierge's core."""
    data = {
        "data_dir": settings.ingester_data_dir,
        "uploads_root": settings.ingester_uploads_root,
    }
    tracker = (
        adapters.ConciergeGiteaTracker(
            settings=settings.gitea,
            identity_claim=settings.owner_claim,
            **data,
        )
        if settings.gitea is not None
        else None
    )
    return provision.Provisioner(
        store=adapters.GitRoomStore(
            clone=settings.clone,
            environment=settings.environment,
            identity_claim=settings.owner_claim,
            gitea=settings.gitea,
            **data,
        ),
        registrar=adapters.HaikuRagRegistrar(
            clone=settings.clone,
            db_dir=settings.db_dir,
            ingester_config=settings.ingester_config,
            ingester_config_dir=settings.ingester_config_dir,
            ingester_fragments_dir=settings.ingester_fragments_dir,
            ingester_url=settings.ingester_url,
            ingester_token=settings.ingester_token,
            **data,
        ),
        uploads=adapters.LocalUploads(root=settings.uploads_dir),
        authz=adapters.SoliplexCliAuthz(
            cli=settings.soliplex_cli,
            environment=settings.environment,
            clone=settings.clone,
        ),
        backend=adapters.SoliplexBackend(
            base_url=settings.backend_url,
            secret_file=settings.service_token_secret_file,
            clone=settings.clone,
            service_subject=settings.service_subject,
        ),
        tracker=tracker,
        owner_claim=settings.owner_claim,
        flatten_owner=settings.flatten_owner,
        lowercase_owner=settings.lowercase_owner,
        room_id_separator=settings.room_id_separator,
    )


def build_admission(settings: Settings) -> adapters.TemplateAdmission:
    """Who asks the service for a room, and whether they may."""
    return adapters.TemplateAdmission(
        secret_file=settings.service_token_secret_file,
        cli=settings.soliplex_cli,
        environment=settings.environment,
    )


def create_app(settings: Settings | None = None) -> fastapi.FastAPI:
    """The ``soliplex-provisioner`` service's application.

    ``settings`` default to the process environment's.
    """
    if settings is None:
        settings = Settings.from_env(os.environ)
    return service.create_app(
        build_provisioner(settings), build_admission(settings)
    )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
USER_ERRORS = (
    SettingsError,
    provision.ProvisionError,
    room_ids.RoomIdError,
    access_rules.AccessRuleError,
    adapters.AdapterError,
    interfaces.AccessRefused,
    ingester.IngesterError,
    landing_clone.LandingCloneError,
    soliplex_api.BackendError,
    soliplex_cli.CliFailed,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soliplex-provision-room",
        description=(
            "Provision a private RAG room for a requester, or (with "
            "--reconcile) make the stack match the provisioned-rooms clone."
        ),
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "make every provisioned room's policy, uploads directory and "
            "database match the clone, instead of provisioning one"
        ),
    )
    parser.add_argument(
        "--claims",
        type=json.loads,
        help="the requester's token claims, as a JSON object",
    )
    parser.add_argument("--name", help="the room's human-readable name")
    parser.add_argument("--description", help="defaults to the name")
    parser.add_argument("--slug", help="defaults to one derived from the name")
    return parser


def _reconcile(environ) -> int:
    try:
        reconciled = reconcile.reconcile(Settings.from_env(environ))
    except USER_ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for note in reconciled.notes:
        print(f"note: {note}")
    for change in reconciled.changes:
        print(change)
    if not reconciled.changes:
        print("nothing to change: the stack matches the clone")
        return 0
    return 1


def main(argv=None, environ=None) -> int:
    """Provision one room, or reconcile the stack with the clone.

    Provisioning exits 0 when the room is live, 1 when steps remain, 2 on
    an error. Reconciling exits 0 when nothing needed changing, 1 when it
    changed something, 2 on an error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.reconcile:
        return _reconcile(environ)
    if args.claims is None or args.name is None:
        parser.error("--claims and --name are required to provision a room")
    if not isinstance(args.claims, dict):
        print("error: --claims must be a JSON object", file=sys.stderr)
        return 2
    try:
        settings = Settings.from_env(environ)
        result = provision.provision_room(
            args.claims,
            name=args.name,
            provisioner=build_provisioner(settings),
            description=args.description,
            slug=args.slug,
        )
    except USER_ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    room = result.request.room
    print(f"provisioned room {room!r} (commit {result.commit[:12]})")
    if result.issue is not None:
        print(f"tracking issue: {result.issue.url}")
    if result.unfinished:
        print("still to do:")
        for step in result.unfinished:
            print(f"  - {step}")
        return 1
    return 0
