"""Make the stack's live state match the provisioned-rooms clone.

The clone records everything provisioning set up for each room: its
config, its ingester fragment (``databases.d/<room>.yaml``) and its authz
policy (``<room>/authz.yaml``). Reconcile replays those records, so that
after ``docker compose down -v`` (or a database restore), every provisioned
room is private again, and ingesting.

It holds the clone's lock, and:

1. for each provisioned room in the clone, applies its ``authz.yaml`` when
   the stored policy differs, and creates its uploads directory when
   missing; a room with no ``authz.yaml`` (provisioned before the clone
   recorded policies) is reported, and its policy left alone
2. has the ingester reload its config, which opens the database of each
   room whose fragment is in ``databases.d/``
3. has the backend reload its room configs, which loads each room in the
   clone

Deprovisioning is out of its scope: it neither removes rooms nor their
policies.

A second run changes nothing.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import yaml
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import ingester
from soliplex_concierge.provisioner import landing_clone
from soliplex_concierge.provisioner import room_ids
from soliplex_concierge.provisioner import soliplex_cli

from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import rendering

# 'room-authz as-yaml' shows a rule on one of these claims as that field.
_SHORTCUT_CLAIMS = ("preferred_username", "email")


class BadRecord(adapters.AdapterError):
    def __init__(self, path, error):
        self.path, self.error = path, error
        super().__init__(f"cannot read the policy record {path}: {error}")


@dataclasses.dataclass(frozen=True)
class Reconciled:
    """What reconcile did (``changes``), and what it left alone (``notes``).

    Each is a sentence; ``changes`` is empty when the stack already matched
    the clone.
    """

    changes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def provisioned_rooms(
    clone: pathlib.Path, separator: str = room_ids.DEFAULT_SEPARATOR
) -> list[str]:
    """The ids of the provisioned rooms in ``clone``, sorted.

    That is, its directories whose names parse as provisioned room ids
    under ``separator``, and that hold a ``room_config.yaml``.
    """
    rooms = []
    for path in clone.iterdir():
        try:
            room_ids.parse_room_id(path.name, separator)
        except room_ids.RoomIdError:
            continue
        if (path / "room_config.yaml").is_file():
            rooms.append(path.name)
    return sorted(rooms)


def _rule(entry: dict) -> tuple[str, str | None]:
    if entry.get("everyone"):
        return ("everyone", None)
    if entry.get("authenticated"):
        return ("authenticated", None)
    for claim in _SHORTCUT_CLAIMS:
        if entry.get(claim) is not None:
            return ("json_path", access_rules.claim_rule(claim, entry[claim]))
    return ("json_path", entry.get("json_path"))


def normalized_policy(policy: dict | None) -> tuple | None:
    """A policy document, comparable whichever spelling it was read in.

    ``room-authz as-yaml`` shows every discriminator field, and a rule on
    ``preferred_username`` / ``email`` as that field; ``authz.yaml`` holds
    the rule itself. Both normalize to the default and the ordered
    ``(allow, permission, rule)`` entries.
    """
    if policy is None:
        return None
    return (
        policy.get("default_allow_deny", "DENY"),
        tuple(
            (
                entry.get("allow_deny", "DENY"),
                entry.get("permission", "READ"),
                _rule(entry),
            )
            for entry in policy.get("acl_entries", ())
        ),
    )


def stored_policy(cli: str, environment: str, room: str) -> dict | None:
    """The room's stored policy document (None when it has none)."""
    output = soliplex_cli.run(
        cli, "room-authz", "as-yaml", environment, room, "--allow-stale"
    )
    return yaml.safe_load(output)


def _recorded_policy(record: pathlib.Path) -> dict:
    try:
        return yaml.safe_load(record.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise BadRecord(record, error) from error


def reconcile(settings) -> Reconciled:
    """Make the stack match the clone ``settings`` names; say what changed.

    ``settings`` is a :class:`soliplex_template.provisioner.wiring.Settings`.
    """
    cli, environment = settings.soliplex_cli, str(settings.environment)
    changes, notes = [], []

    with landing_clone.locked(settings.clone):
        rooms = provisioned_rooms(settings.clone, settings.room_id_separator)

        for room in rooms:
            record = settings.clone / room / rendering.AUTHZ_FILE
            if not record.is_file():
                notes.append(
                    f"room {room!r} has no {rendering.AUTHZ_FILE} (it was "
                    "provisioned before the clone recorded policies): its "
                    "policy is left as it is"
                )
            else:
                recorded = _recorded_policy(record)
                stored = stored_policy(cli, environment, room)
                if normalized_policy(recorded) != normalized_policy(stored):
                    soliplex_cli.set_policy(
                        cli, environment, room, json.dumps(recorded)
                    )
                    changes.append(
                        f"room {room!r}: applied its {rendering.AUTHZ_FILE}"
                    )
            uploads = settings.uploads_dir / room
            if not uploads.exists():
                uploads.mkdir(parents=True)
                changes.append(f"room {room!r}: created {uploads}")

        report = ingester.reload(
            settings.ingester_url, settings.ingester_token
        )
        for change in report.get("databases", ()):
            changes.append(
                f"ingester: database {change.get('database')!r} "
                f"{change.get('change')}"
            )

        delta = adapters.SoliplexBackend(
            base_url=settings.backend_url,
            secret_file=settings.service_token_secret_file,
            clone=settings.clone,
            service_subject=settings.service_subject,
        ).reload_rooms_delta()
        for kind in ("added", "removed", "changed"):
            for room in delta.get(kind, ()):
                changes.append(f"backend: room {room!r} {kind}")

    return Reconciled(changes=tuple(changes), notes=tuple(notes))
