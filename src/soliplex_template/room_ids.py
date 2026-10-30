"""Ids for rooms the provisioner creates on request.

A provisioned room's id is ``<owner>:<slug>``: ``owner`` derives from one of
the requester's token claims, ``slug`` is the name the requester chose. The
id doubles as the room's directory name in the landing clone, its LanceDB
stem (``<id>.lancedb``), and its uploads directory, so each part must be a
safe single path segment of at most :data:`MAX_SEGMENT` characters.

The owner part is technical identity: the value of a configured claim
(default ``preferred_username``), optionally *flattened* (``@`` -> ``_``) so
email-shaped values fit the segment rule. Flattening is neither invertible
nor injective, so :class:`RoomOwner` keeps the raw claim value alongside the
owner part: an access grant matches the raw value, never the owner part.

The slug is the requester's explicit choice or, by default, derived from
the room's human-readable name by :func:`slugify`.

Operator-curated rooms keep flat ids (no ``:``), so they never collide with
a provisioned one.

Stdlib only.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import re
import unicodedata

SEPARATOR = ":"
DEFAULT_OWNER_CLAIM = "preferred_username"

# One path segment: no '/', no leading dot (so neither '.' nor '..'), and
# no SEPARATOR, which keeps the split of an id into its parts unique.
SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Keeps '<owner>:<slug>.lancedb' well inside a 255-byte file name.
MAX_SEGMENT = 64
_SEGMENT_RULE = f"{SEGMENT_RE.pattern}, at most {MAX_SEGMENT} characters"

# What 'slugify' collapses into one '-'.
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


class RoomIdError(Exception):
    """A user-facing error (printed without a traceback)."""


class OwnerClaimMissing(RoomIdError):
    def __init__(self, claim):
        self.claim = claim
        super().__init__(f"the requester's token carries no {claim!r} claim")


class BadOwner(RoomIdError):
    def __init__(self, claim, value):
        self.claim, self.value = claim, value
        super().__init__(
            f"claim {claim!r} value {value!r} cannot own rooms: it must "
            f"match {_SEGMENT_RULE} (after any flattening)"
        )


class BadSlug(RoomIdError):
    def __init__(self, slug):
        self.slug = slug
        super().__init__(f"room slug {slug!r} must match {_SEGMENT_RULE}")


class SlugEmpty(RoomIdError):
    def __init__(self, name):
        self.name = name
        super().__init__(
            f"room name {name!r} yields no slug; choose one explicitly "
            f"({_SEGMENT_RULE})"
        )


class NotAProvisionedRoomId(RoomIdError):
    def __init__(self, room_id):
        self.room_id = room_id
        super().__init__(
            f"{room_id!r} is not a provisioned room id "
            f"('<owner>{SEPARATOR}<name>')"
        )


@dataclasses.dataclass(frozen=True)
class RoomOwner:
    """Who owns a provisioned room.

    ``owner`` is the id part; ``claim`` / ``claim_value`` are the raw claim
    an access grant matches.
    """

    claim: str
    claim_value: str
    owner: str


@dataclasses.dataclass(frozen=True)
class RoomId:
    """A provisioned room's id, split into its parts."""

    owner: str
    slug: str

    def __str__(self) -> str:
        return f"{self.owner}{SEPARATOR}{self.slug}"


def is_segment(value: str) -> bool:
    """Tell whether ``value`` is usable as one part of a room id."""
    return len(value) <= MAX_SEGMENT and bool(SEGMENT_RE.fullmatch(value))


def flatten(value: str) -> str:
    """Map an email-shaped claim value onto the segment character set."""
    return value.replace("@", "_")


def owner_from_claims(
    claims: collections.abc.Mapping,
    *,
    claim: str = DEFAULT_OWNER_CLAIM,
    flatten_value: bool = False,
) -> RoomOwner:
    """Derive the room owner from the requester's token ``claims``.

    Reads ``claims[claim]``, flattening it when ``flatten_value`` is true.
    Raises :class:`OwnerClaimMissing` when the claim is absent, and
    :class:`BadOwner` when its value is not a string or does not fit the
    segment rule.
    """
    if claim not in claims:
        raise OwnerClaimMissing(claim)

    value = claims[claim]
    if not isinstance(value, str):
        raise BadOwner(claim, value)

    owner = flatten(value) if flatten_value else value
    if not is_segment(owner):
        raise BadOwner(claim, value)

    return RoomOwner(claim=claim, claim_value=value, owner=owner)


def validate_slug(slug: str) -> None:
    """Raise :class:`BadSlug` unless ``slug`` fits the segment rule."""
    if not is_segment(slug):
        raise BadSlug(slug)


def slugify(name: str) -> str:
    """Derive a slug from a room's human-readable ``name``.

    Drops accents, lowercases, collapses each run of other characters into
    one ``-``, strips ``-`` from both ends, and truncates to
    :data:`MAX_SEGMENT`. Raises :class:`SlugEmpty` when nothing is left.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = _NON_SLUG_RE.sub("-", ascii_name.lower()).strip("-")
    slug = slug[:MAX_SEGMENT].rstrip("-")
    if not slug:
        raise SlugEmpty(name)
    return slug


def make_room_id(owner: RoomOwner, slug: str) -> RoomId:
    """Compose the id of ``owner``'s room named ``slug``."""
    validate_slug(slug)
    return RoomId(owner=owner.owner, slug=slug)


def parse_room_id(room_id: str) -> RoomId:
    """Split a provisioned room id into its parts.

    Raises :class:`NotAProvisionedRoomId` unless ``room_id`` is exactly two
    segments joined by :data:`SEPARATOR`.
    """
    owner, sep, slug = room_id.partition(SEPARATOR)
    if not (sep and is_segment(owner) and is_segment(slug)):
        raise NotAProvisionedRoomId(room_id)
    return RoomId(owner=owner, slug=slug)
