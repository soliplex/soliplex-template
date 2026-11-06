"""Unit tests for the bundled ``skill/scripts/authelia_users.py``.

The script ships inside the ``soliplex-template`` skill and is not part of an
importable package, so it is loaded here by file path via ``importlib.util``
(and registered in ``sys.modules``). Tests write stack files under
``tmp_path`` and mock the Docker calls that hash passwords and manage
Authelia storage.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from unittest import mock

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "authelia_users.py"
)
_spec = importlib.util.spec_from_file_location("authelia_users", _MODULE_PATH)
authelia_users = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = authelia_users
_spec.loader.exec_module(authelia_users)


def _stack(tmp_path) -> pathlib.Path:
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "docker-compose.yml").write_text("name: test\n")
    (stack / "authelia").mkdir()
    (stack / ".secrets").mkdir()
    return stack


# --------------------------------------------------------------------------
# _generate_password / _password_file
# --------------------------------------------------------------------------
def test_generate_password_shape():
    pw = authelia_users._generate_password()

    assert len(pw) == 32
    assert all(c.isalnum() for c in pw)


def test_password_file_uses_safe_email_name(tmp_path):
    stack = _stack(tmp_path)

    path = authelia_users._password_file(stack, "admin+tag@example.local")

    assert path.name == "authelia_user_admin_tag_example.local.gen"


# --------------------------------------------------------------------------
# _load_yaml / _save_yaml
# --------------------------------------------------------------------------
def test_load_yaml_missing_is_empty(tmp_path):
    stack = _stack(tmp_path)

    data = authelia_users._load_yaml(stack / "authelia/no.yml")

    assert data == {}


def test_save_yaml_round_trips(tmp_path):
    stack = _stack(tmp_path)
    path = stack / "authelia/users.yml"

    authelia_users._save_yaml(path, {"users": {"a": {"email": "a@b"}}})
    data = authelia_users._load_yaml(path)

    assert data["users"]["a"]["email"] == "a@b"


def test_load_yaml_whitespace_only_document_returns_empty_dict(tmp_path):
    stack = _stack(tmp_path)
    path = stack / "authelia/users.yml"
    path.write_text("   \n\n")

    data = authelia_users._load_yaml(path)

    assert data == {}


def test_load_yaml_empty_document_returns_empty_dict(tmp_path):
    stack = _stack(tmp_path)
    path = stack / "authelia/users.yml"
    path.write_text("---\n")

    data = authelia_users._load_yaml(path)

    assert data == {}


# --------------------------------------------------------------------------
# list_users
# --------------------------------------------------------------------------
def test_list_users_empty(tmp_path):
    stack = _stack(tmp_path)

    users = authelia_users.list_users(stack)

    assert users == []


def test_list_users_returns_entries(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local:\n"
        "    displayname: alice\n    email: alice@example.local\n"
        "    password: argon2id$...\n"
    )

    users = authelia_users.list_users(stack)

    assert len(users) == 1
    assert users[0]["username"] == "alice@example.local"
    assert users[0]["email"] == "alice@example.local"


# --------------------------------------------------------------------------
# add_user
# --------------------------------------------------------------------------
def test_add_user_writes_password_file_and_hash(tmp_path):
    stack = _stack(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        sub = authelia_users.add_user(stack, "alice@example.local")

    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert "alice@example.local" in users["users"]
    assert users["users"]["alice@example.local"]["password"] == "argon2id$hash"
    password_path = (
        stack / ".secrets" / "authelia_user_alice_example.local.gen"
    )
    assert password_path.is_file()
    assert len(password_path.read_text()) == 32
    assert password_path.stat().st_mode & 0o777 == 0o600
    assert len(sub) == 36  # UUID length


def test_add_user_admin_adds_soliplex_admin(tmp_path):
    stack = _stack(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        authelia_users.add_user(stack, "admin@example.local", admin=True)

    admin_cmd = [c for c in calls if "admin-users" in c and "add" in c]
    assert len(admin_cmd) == 1
    assert "--json-path" in admin_cmd[0]
    json_path_arg = admin_cmd[0][admin_cmd[0].index("--json-path") + 1]
    assert '$[?$.sub == "' in json_path_arg


# --------------------------------------------------------------------------
# reset_password
# --------------------------------------------------------------------------
def test_reset_password_updates_hash(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local:\n"
        "    displayname: alice\n    email: alice@example.local\n"
        "    password: old-hash\n"
    )
    (stack / ".secrets" / "authelia_user_alice_example.local.gen").write_text(
        "old-password"
    )

    def fake_run(cmd, **kwargs):
        return mock.Mock(
            returncode=0, stdout="Digest: $argon2id$new\n", stderr=""
        )

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        authelia_users.reset_password(stack, "alice@example.local")

    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert users["users"]["alice@example.local"]["password"] == "$argon2id$new"
    assert not users["users"]["alice@example.local"]["password"].startswith(
        "Digest"
    )
    password_path = (
        stack / ".secrets" / "authelia_user_alice_example.local.gen"
    )
    assert password_path.read_text() != "old-password"


def test_reset_password_missing_user(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text("users: {}\n")

    with pytest.raises(authelia_users.UserNotFound):
        authelia_users.reset_password(stack, "missing@example.local")


# --------------------------------------------------------------------------
# remove_user
# --------------------------------------------------------------------------
def test_remove_user_deletes_entry_and_password(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local:\n"
        "    displayname: alice\n    email: alice@example.local\n"
        "    password: old-hash\n"
    )
    (stack / ".secrets" / "authelia_user_alice_example.local.gen").write_text(
        "secret"
    )
    (stack / "authelia/identifiers.yml").write_text(
        "identifiers:\n  - username: alice@example.local\n    identifier: u1\n"
    )

    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        authelia_users.remove_user(stack, "alice@example.local")

    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert "alice@example.local" not in users["users"]
    assert not (
        stack / ".secrets" / "authelia_user_alice_example.local.gen"
    ).exists()


def test_remove_user_missing(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text("users: {}\n")

    with pytest.raises(authelia_users.UserNotFound):
        authelia_users.remove_user(stack, "nobody@example.local")


# --------------------------------------------------------------------------
# show_code
# --------------------------------------------------------------------------
def test_show_code_returns_notification(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/notification.txt").write_text("code: 123456")

    text = authelia_users.show_code(stack)

    assert text == "code: 123456"


def test_show_code_missing(tmp_path):
    stack = _stack(tmp_path)

    with pytest.raises(authelia_users.NoNotificationFile):
        authelia_users.show_code(stack)


# --------------------------------------------------------------------------
# internals and edge cases
# --------------------------------------------------------------------------
def test_run_cmd_raises_on_failure(tmp_path):
    stack = _stack(tmp_path)
    proc = mock.Mock(returncode=1, stdout="out", stderr="err")

    with (
        mock.patch.object(authelia_users.subprocess, "run", return_value=proc),
        pytest.raises(authelia_users.DockerCommandFailed) as caught,
    ):
        authelia_users._run_cmd(["docker", "bad"], cwd=stack)

    assert caught.value.returncode == 1
    assert "err" in str(caught.value)


def test_list_users_skips_non_dict_entry(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local: not-a-dict\n"
    )

    users = authelia_users.list_users(stack)

    assert users == []


def test_upsert_identifier_reuses_existing(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/identifiers.yml").write_text(
        "identifiers:\n  - username: alice@example.local\n"
        "    identifier: 883ba2fc-2d27-4ae0-9727-2de1b84024dd\n"
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "export" in cmd:
            # The export (through the bind mount) recreates the file it
            # deleted beforehand.
            (stack / "authelia/identifiers.yml").write_text(
                "identifiers:\n"
                "  - username: alice@example.local\n"
                "    identifier: 883ba2fc-2d27-4ae0-9727-2de1b84024dd\n"
            )
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        sub = authelia_users._upsert_identifier(stack, "alice@example.local")
        # A non-export call exercises the mock's other branch.
        authelia_users._run_cmd(["docker", "compose", "ps"], cwd=stack)

    assert sub == "883ba2fc-2d27-4ae0-9727-2de1b84024dd"
    # No 'identifiers add' call: only the two exports.
    adds = [c for c in calls if c[-2:] == ["identifiers", "add"]]
    assert adds == []


def test_upsert_identifier_adds_when_missing(tmp_path):
    stack = _stack(tmp_path)
    additions = []

    def fake_run(cmd, **kwargs):
        if "identifiers" in cmd and "add" in cmd:
            additions.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        sub = authelia_users._upsert_identifier(stack, "alice@example.local")

    assert len(additions) == 1
    assert additions[0][-1] == sub
    assert sub != ""


def test_read_sub_returns_none_when_missing(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/identifiers.yml").write_text(
        "identifiers:\n  - username: other@example.local\n    identifier: u1\n"
    )

    sub = authelia_users._read_sub(stack, "alice@example.local")

    assert sub is None


def test_remove_user_without_sub(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local:\n"
        "    displayname: alice\n    email: alice@example.local\n"
        "    password: hash\n"
    )
    (stack / ".secrets" / "authelia_user_alice_example.local.gen").write_text(
        "secret"
    )

    authelia_users.remove_user(stack, "alice@example.local")

    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert "alice@example.local" not in users["users"]
    assert not (
        stack / ".secrets" / "authelia_user_alice_example.local.gen"
    ).exists()


def test_remove_user_deletes_soliplex_admin(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  alice@example.local:\n"
        "    displayname: alice\n    email: alice@example.local\n"
        "    password: hash\n"
    )
    (stack / "authelia/identifiers.yml").write_text(
        "identifiers:\n  - username: alice@example.local\n"
        "    identifier: sub-9\n"
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        authelia_users.remove_user(stack, "alice@example.local")

    admin_cmd = [c for c in calls if "admin-users" in c and "delete" in c]
    assert len(admin_cmd) == 1
    json_path_arg = admin_cmd[0][admin_cmd[0].index("--json-path") + 1]
    assert '$[?$.sub == "sub-9"]' in json_path_arg


# --------------------------------------------------------------------------
# main / CLI
# --------------------------------------------------------------------------
def test_main_add_prints_human_summary(tmp_path, capsys):
    stack = _stack(tmp_path)

    def fake_run(cmd, **kwargs):
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "add",
                "--email",
                "bob@example.local",
            ]
        )

    out = capsys.readouterr().out
    assert result == 0
    assert "added Authelia user 'bob@example.local'" in out
    assert "sub: " in out
    assert "password file: " in out
    assert "--quiet" not in out


def test_main_add_quiet_prints_json_summary(tmp_path, capsys):
    stack = _stack(tmp_path)

    def fake_run(cmd, **kwargs):
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "add",
                "--quiet",
                "--admin",
                "--email",
                "bob@example.local",
            ]
        )

    out = capsys.readouterr().out
    summary = json.loads(out)
    assert result == 0
    assert set(summary) == {
        "email",
        "sub",
        "admin",
        "password_file",
    }
    assert summary["email"] == "bob@example.local"
    assert summary["admin"] is True
    assert len(summary["sub"]) == 36
    assert summary["password_file"] == str(
        stack / ".secrets" / "authelia_user_bob_example.local.gen"
    )


def test_main_list_empty(tmp_path):
    stack = _stack(tmp_path)

    result = authelia_users.main(["--project-dir", str(stack), "list"])

    assert result == 0


def test_main_list(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  bob@example.local:\n"
        "    displayname: bob\n    email: bob@example.local\n"
        "    password: hash\n"
    )

    result = authelia_users.main(["--project-dir", str(stack), "list"])

    assert result == 0


def test_main_add(tmp_path):
    stack = _stack(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "add",
                "--email",
                "bob@example.local",
            ]
        )

    assert result == 0
    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert "bob@example.local" in users["users"]


def test_main_add_admin(tmp_path):
    stack = _stack(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "crypto" in cmd:
            return mock.Mock(returncode=0, stdout="argon2id$hash", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "add",
                "--admin",
                "--email",
                "admin@example.local",
            ]
        )

    assert result == 0
    admin_cmd = [c for c in calls if "admin-users" in c and "add" in c]
    assert len(admin_cmd) == 1


def test_main_remove(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  bob@example.local:\n"
        "    displayname: bob\n    email: bob@example.local\n"
        "    password: hash\n"
    )
    (stack / "authelia/identifiers.yml").write_text(
        "identifiers:\n  - username: bob@example.local\n    identifier: u1\n"
    )

    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "remove",
                "bob@example.local",
            ]
        )

    assert result == 0
    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert "bob@example.local" not in users["users"]


def test_main_reset(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text(
        "users:\n  bob@example.local:\n"
        "    displayname: bob\n    email: bob@example.local\n"
        "    password: old\n"
    )

    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="argon2id$new", stderr="")

    with mock.patch.object(authelia_users.subprocess, "run", fake_run):
        result = authelia_users.main(
            [
                "--project-dir",
                str(stack),
                "reset",
                "bob@example.local",
            ]
        )

    assert result == 0
    users = authelia_users._load_yaml(stack / "authelia/users.yml")
    assert users["users"]["bob@example.local"]["password"] == "argon2id$new"


def test_main_code(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/notification.txt").write_text("reset link")

    result = authelia_users.main(["--project-dir", str(stack), "code"])

    assert result == 0


def test_main_bad_stack(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(authelia_users.StackNotFound):
        authelia_users.main(["--project-dir", str(empty), "list"])


def test_main_user_not_found(tmp_path):
    stack = _stack(tmp_path)
    (stack / "authelia/users.yml").write_text("users: {}\n")

    with pytest.raises(authelia_users.UserNotFound):
        authelia_users.main(
            ["--project-dir", str(stack), "reset", "nobody@example.local"]
        )
