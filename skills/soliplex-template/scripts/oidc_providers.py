# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""Add, remove and list a stack's OIDC providers.

A stack's providers are the ``auth_systems`` entries in
``backend/environment/oidc/config.yaml``. With none, the backend runs
without authentication; adding the first turns it on. The backend watches
that file (``--reload=config``), so a change takes effect without a restart.

The ``/ingester/`` gate in nginx signs browsers in with a single provider.
Its id lives in the fragment ``nginx/conf.d/signin-provider.conf``:

- adding the first provider always selects it;
- adding another selects it only when asked to;
- removing the selected provider needs a successor while others remain,
  and removes the fragment when none do.

nginx reads its fragments only at start: restart it after a change.

This module is the core: every choice is an explicit argument, and nothing
prompts. Edits to the config are made line by line, so comments and the
other entries are kept; each edit is checked by parsing the result before
the file is replaced.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import tempfile

import yaml

CONFIG_PATH = pathlib.PurePosixPath("backend/environment/oidc/config.yaml")
SIGNIN_FRAGMENT_PATH = pathlib.PurePosixPath(
    "nginx/conf.d/signin-provider.conf"
)

# Provider ids appear in URL paths ('/api/login/<id>', '/api/auth/<id>')
# and inside a quoted nginx string: keep them to a safe alphabet.
PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEFAULT_SCOPE = "openid email profile"
DEFAULT_INDENT = "  "

_LIST_KEY = "auth_systems"
_LIST_KEY_RE = re.compile(
    r"^auth_systems:[ \t]*(?P<flow>\[[ \t]*\])?[ \t]*(?:#.*)?$"
)
# A top-level key: anything but indentation, a comment or a list item.
_TOP_LEVEL_RE = re.compile(r"^[^\s#-]")
_ENTRY_RE = re.compile(r"^(?P<indent>[ \t]*)- ")
_SIGNIN_RE = re.compile(
    r'^[ \t]*set \$soliplex_signin_provider "(?P<id>[^"]*)";[ \t]*$',
    re.MULTILINE,
)

_SIGNIN_FRAGMENT = """\
# The OIDC provider the '/ingester/' gate signs browsers in with: an
# 'auth_systems' id in 'backend/environment/oidc/config.yaml'. Written by
# the soliplex-template skill's 'oidc_providers.py'. nginx reads it only at
# start: 'docker compose restart nginx' after changing it.
set $soliplex_signin_provider "{provider_id}";
"""


class OIDCProvidersError(Exception):
    """A user-facing error (printed without a traceback)."""


class ConfigNotFound(OIDCProvidersError):
    def __init__(self, path):
        self.path = path
        super().__init__(f"no OIDC config at {path}")


class BadConfig(OIDCProvidersError):
    def __init__(self, path, reason):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}; edit it by hand")


class BadProviderId(OIDCProvidersError):
    def __init__(self, provider_id):
        self.provider_id = provider_id
        super().__init__(
            f"provider id {provider_id!r} must match {PROVIDER_ID_RE.pattern}"
            " (lowercase letters, digits, '_', '-')"
        )


class ProviderExists(OIDCProvidersError):
    def __init__(self, provider_id):
        self.provider_id = provider_id
        super().__init__(f"provider {provider_id!r} already exists")


class ProviderNotFound(OIDCProvidersError):
    def __init__(self, provider_id):
        self.provider_id = provider_id
        super().__init__(f"no provider {provider_id!r}")


class SigninSuccessorRequired(OIDCProvidersError):
    def __init__(self, provider_id, remaining):
        self.provider_id = provider_id
        self.remaining = remaining
        super().__init__(
            f"provider {provider_id!r} signs browsers in to '/ingester/': "
            f"name the provider to take over, one of {remaining!r}"
        )


class EditMismatch(OIDCProvidersError):
    def __init__(self, path):
        self.path = path
        super().__init__(
            f"{path}: the edited config did not parse as expected, so it "
            "was left unchanged; edit it by hand"
        )


@dataclasses.dataclass(frozen=True)
class Provider:
    """An ``auth_systems`` entry, as written by :func:`add_provider`."""

    id: str
    title: str
    server_url: str
    client_id: str
    token_validation_pem: str
    scope: str = DEFAULT_SCOPE
    # Empty for a public client; else a 'secret:NAME' reference.
    client_secret: str = ""

    def as_entry(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "server_url": self.server_url,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.scope,
            "token_validation_pem": self.token_validation_pem.strip() + "\n",
        }


@dataclasses.dataclass(frozen=True)
class AddResult:
    # True when the stack had no provider before: authentication is now on.
    first: bool
    signin: str


@dataclasses.dataclass(frozen=True)
class RemoveResult:
    # Ids left; empty means the stack now runs without authentication.
    remaining: list[str]
    # None when no provider is left.
    signin: str | None


def validate_provider_id(provider_id: str) -> None:
    if not PROVIDER_ID_RE.match(provider_id):
        raise BadProviderId(provider_id)


def load_providers(stack: pathlib.Path) -> list[dict]:
    """The stack's ``auth_systems`` entries, in file order."""
    _text, data = _read_config(stack)
    return _entries(stack / CONFIG_PATH, data)


def signin_provider(stack: pathlib.Path) -> str | None:
    """The id nginx's ``/ingester/`` gate signs in with, if one is set."""
    path = stack / SIGNIN_FRAGMENT_PATH
    if not path.is_file():
        return None
    match = _SIGNIN_RE.search(path.read_text())
    return match["id"] if match else None


def set_signin_provider(stack: pathlib.Path, provider_id: str) -> None:
    validate_provider_id(provider_id)
    if provider_id not in _ids(load_providers(stack)):
        raise ProviderNotFound(provider_id)
    _write_signin(stack, provider_id)


def add_provider(
    stack: pathlib.Path,
    provider: Provider,
    *,
    use_for_signin: bool = False,
) -> AddResult:
    """Append ``provider`` to the stack's ``auth_systems``.

    The first provider always becomes the sign-in provider; a later one
    only with ``use_for_signin``.
    """
    validate_provider_id(provider.id)
    path = stack / CONFIG_PATH
    text, data = _read_config(stack)
    entries = _entries(path, data)
    ids = _ids(entries)
    if provider.id in ids:
        raise ProviderExists(provider.id)
    layout = _layout(path, text, len(entries))

    lines = list(layout.lines)
    indent = DEFAULT_INDENT if layout.indent is None else layout.indent
    if layout.flow_empty:
        lines[layout.key_line] = f"{_LIST_KEY}:"
    insert_at = (
        layout.entries[-1][1] if layout.entries else layout.key_line + 1
    )
    lines[insert_at:insert_at] = ["", *_render_entry(provider, indent)]

    _replace_config(path, lines, data, [*entries, provider.as_entry()])

    current = signin_provider(stack)
    signin = current
    if current is None or use_for_signin:
        signin = provider.id
        _write_signin(stack, signin)
    return AddResult(first=not entries, signin=signin)


def remove_provider(
    stack: pathlib.Path,
    provider_id: str,
    *,
    signin_successor: str | None = None,
) -> RemoveResult:
    """Remove ``provider_id`` from the stack's ``auth_systems``.

    Removing the sign-in provider while others remain needs
    ``signin_successor``; removing the last one removes the sign-in
    fragment.
    """
    validate_provider_id(provider_id)
    path = stack / CONFIG_PATH
    text, data = _read_config(stack)
    entries = _entries(path, data)
    ids = _ids(entries)
    if provider_id not in ids:
        raise ProviderNotFound(provider_id)
    remaining = [i for i in ids if i != provider_id]
    current = signin_provider(stack)

    signin = current if current != provider_id else None
    if signin_successor is not None:
        validate_provider_id(signin_successor)
        if signin_successor not in remaining:
            raise ProviderNotFound(signin_successor)
        signin = signin_successor
    elif current == provider_id and remaining:
        raise SigninSuccessorRequired(provider_id, remaining)

    layout = _layout(path, text, len(entries))
    lines = list(layout.lines)
    start, end = layout.entries[ids.index(provider_id)]
    # An entry always follows the 'auth_systems:' line, so 'start - 1' is
    # in range; a blank separator before the entry goes with it.
    if not lines[start - 1].strip():
        start -= 1
    del lines[start:end]
    if not remaining:
        lines[layout.key_line] = f"{_LIST_KEY}: []"

    _replace_config(
        path,
        lines,
        data,
        [e for e in entries if e["id"] != provider_id],
    )

    if not remaining:
        (stack / SIGNIN_FRAGMENT_PATH).unlink(missing_ok=True)
        signin = None
    elif signin != current:
        _write_signin(stack, signin)
    return RemoveResult(remaining=remaining, signin=signin)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class _Layout:
    lines: list[str]
    key_line: int
    flow_empty: bool
    # The list items' indentation, or None when there are none.
    indent: str | None
    # [start, end) line ranges, one per entry, trailing blank lines excluded.
    entries: list[tuple[int, int]]


def _read_config(stack: pathlib.Path) -> tuple[str, dict]:
    path = stack / CONFIG_PATH
    if not path.is_file():
        raise ConfigNotFound(path)
    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BadConfig(path, f"not valid YAML ({exc})") from exc
    if not isinstance(data, dict) or _LIST_KEY not in data:
        raise BadConfig(path, f"no top-level '{_LIST_KEY}' key")
    return text, data


def _entries(path: pathlib.Path, data: dict) -> list[dict]:
    entries = data[_LIST_KEY]
    if entries is None:
        return []
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and isinstance(e.get("id"), str) for e in entries
    ):
        raise BadConfig(path, f"'{_LIST_KEY}' is not a list of entries")
    return entries


def _ids(entries: list[dict]) -> list[str]:
    return [e["id"] for e in entries]


def _layout(path: pathlib.Path, text: str, count: int) -> _Layout:
    lines = text.splitlines()
    key_lines = [i for i, line in enumerate(lines) if _LIST_KEY_RE.match(line)]
    if len(key_lines) != 1:
        raise BadConfig(path, f"expected one plain '{_LIST_KEY}:' line")
    key_line = key_lines[0]
    flow_empty = _LIST_KEY_RE.match(lines[key_line])["flow"] is not None

    block_end = next(
        (
            i
            for i in range(key_line + 1, len(lines))
            if _TOP_LEVEL_RE.match(lines[i])
        ),
        len(lines),
    )
    indent = None
    starts = []
    for i in range(key_line + 1, block_end):
        match = _ENTRY_RE.match(lines[i])
        if match and (indent is None or match["indent"] == indent):
            indent = match["indent"]
            starts.append(i)

    entries = []
    nexts = [*starts[1:], block_end] if starts else []
    for start, nxt in zip(starts, nexts, strict=True):
        end = nxt
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        entries.append((start, end))

    if len(entries) != count:
        raise BadConfig(path, f"could not find each '{_LIST_KEY}' entry")
    return _Layout(lines, key_line, flow_empty, indent, entries)


class _Literal(str):
    """A string to dump as a YAML literal block ('|')."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    _Literal,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|"
    ),
)


def _render_entry(provider: Provider, indent: str) -> list[str]:
    entry = provider.as_entry()
    entry["token_validation_pem"] = _Literal(entry["token_validation_pem"])
    text = yaml.dump(
        [entry],
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )
    return [indent + line for line in text.splitlines()]


def _replace_config(
    path: pathlib.Path,
    lines: list[str],
    data: dict,
    expected: list[dict],
) -> None:
    # Always end with a newline: a literal block ('|') at the very end of a
    # file without one would lose its own.
    text = "\n".join(lines) + "\n"
    try:
        edited = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EditMismatch(path) from exc
    others = {k: v for k, v in data.items() if k != _LIST_KEY}
    if (
        not isinstance(edited, dict)
        or {k: v for k, v in edited.items() if k != _LIST_KEY} != others
        or (edited.get(_LIST_KEY) or []) != expected
    ):
        raise EditMismatch(path)
    _atomic_write(path, text)


def _write_signin(stack: pathlib.Path, provider_id: str) -> None:
    path = stack / SIGNIN_FRAGMENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, _SIGNIN_FRAGMENT.format(provider_id=provider_id))


def _atomic_write(path: pathlib.Path, text: str) -> None:
    """Replace ``path`` in one rename, keeping an existing file's mode."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
