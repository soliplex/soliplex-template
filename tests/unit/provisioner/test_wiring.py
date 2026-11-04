"""Unit tests for ``soliplex_template.provisioner.wiring``, and its shims.

The contract test drives the service the wiring builds against a small
stack under ``tmp_path`` (real git and files); the concierge toolkit's
calls to ``soliplex-cli``, the backend and the ingester are replaced.

Each test is laid out in three blank-line-separated phases -- setup, then the
single call under test (the "act"), then the assertions -- and performs that
act exactly once (cases that would repeat it are parametrized or split).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib

import pytest
from mako import template as mako_template
from soliplex import authz
from soliplex import token_mint
from soliplex_concierge.provisioner import access_rules
from soliplex_concierge.provisioner import contract
from soliplex_concierge.provisioner import interfaces
from soliplex_concierge.provisioner import provision
from soliplex_concierge.provisioner import room_ids

from soliplex_template import provision as provision_shim
from soliplex_template import provision_service as service_shim
from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import rendering
from soliplex_template.provisioner import wiring

REPO = pathlib.Path(__file__).resolve().parents[3]
CLAIMS = {"preferred_username": "alice", "email": "alice@example.com"}
ROOM = "alice:q3-plans"
SECRET = "test" * 8


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "ingester_bearer"
    path.write_text("bearer\n")
    return path


def _environ(token_file, **extra):
    return {"PROVISIONER_INGESTER_TOKEN_FILE": str(token_file), **extra}


# ---- Settings.from_env ----


def test_settings_from_env_defaults(token_file):
    result = wiring.Settings.from_env(_environ(token_file))

    assert result == wiring.Settings(ingester_token="bearer")
    assert result.ingester_fragments_dir == (
        "/app/rooms-provisioned/databases.d"
    )


def test_settings_from_env(token_file):
    environ = _environ(
        token_file,
        PROVISIONER_SERVICE_SUBJECT="ops-provisioner",
        PROVISIONER_OWNER_CLAIM="email",
        PROVISIONER_FLATTEN_OWNER="True",
        GITEA_HOST="https://gitea.example.com",
        GITEA_ACCESS_TOKEN="gt",
        GITEA_OWNER="ops",
        GITEA_REPO="requests",
        GITEA_ROOMS_REPO="rooms",
    )

    result = wiring.Settings.from_env(environ)

    assert result == wiring.Settings(
        ingester_token="bearer",
        service_subject="ops-provisioner",
        owner_claim="email",
        flatten_owner=True,
        gitea=adapters.GiteaSettings(
            host="https://gitea.example.com",
            token="gt",
            owner="ops",
            repo="requests",
            rooms_repo="rooms",
        ),
    )


def test_settings_from_env_gitea_defaults(token_file):
    environ = _environ(
        token_file, GITEA_HOST="http://gitea:3000", GITEA_ACCESS_TOKEN="gt"
    )

    result = wiring.Settings.from_env(environ)

    assert result.gitea == adapters.GiteaSettings(
        host="http://gitea:3000",
        token="gt",
        owner="soliplex-admin",
        repo="soliplex-requests",
        rooms_repo="rooms-provisioned",
    )


def test_settings_from_env_reads_the_secret_by_default(monkeypatch):
    monkeypatch.setattr(wiring, "INGESTER_TOKEN_FILE", "/nonexistent/x")

    with pytest.raises(wiring.IngesterTokenMissing) as caught:
        wiring.Settings.from_env({})

    assert caught.value.path == pathlib.Path("/nonexistent/x")


@pytest.mark.parametrize(
    "extra, exc_type",
    [
        ({"GITEA_HOST": "http://gitea:3000"}, wiring.GiteaTokenMissing),
        ({"PROVISIONER_FLATTEN_OWNER": "maybe"}, wiring.BadFlag),
        ({"PROVISIONER_OWNER_CLAIM": "a-b"}, access_rules.BadOwnerClaim),
    ],
)
def test_settings_from_env_rejects(token_file, extra, exc_type):
    with pytest.raises(exc_type):
        wiring.Settings.from_env(_environ(token_file, **extra))


def test_settings_repr_hides_tokens():
    settings = wiring.Settings(
        ingester_token="bearer-secret",
        gitea=adapters.GiteaSettings(host="h", token="gitea-secret"),
    )

    result = repr(settings)

    assert "bearer-secret" not in result
    assert "gitea-secret" not in result


# ---- build_provisioner / build_admission ----


def test_build_provisioner():
    settings = wiring.Settings(
        ingester_token="bearer",
        owner_claim="email",
        flatten_owner=True,
        ingester_data_dir="/srv/db",
        gitea=adapters.GiteaSettings(host="h", token="t"),
    )

    result = wiring.build_provisioner(settings)

    assert result.owner_claim == "email"
    assert result.flatten_owner is True
    assert result.store == adapters.GitRoomStore(
        clone=settings.clone,
        environment=settings.environment,
        identity_claim="email",
        gitea=settings.gitea,
        data_dir="/srv/db",
        uploads_root=settings.ingester_uploads_root,
    )
    assert result.registrar.ingester_token == "bearer"
    assert result.registrar.data_dir == "/srv/db"
    assert result.uploads == adapters.LocalUploads(root=settings.uploads_dir)
    assert result.authz == adapters.SoliplexCliAuthz(
        cli=settings.soliplex_cli, environment=settings.environment
    )
    assert result.backend.service_subject == settings.service_subject
    assert result.tracker == adapters.ConciergeGiteaTracker(
        settings=settings.gitea,
        identity_claim="email",
        data_dir="/srv/db",
        uploads_root=settings.ingester_uploads_root,
    )


def test_build_provisioner_without_gitea():
    result = wiring.build_provisioner(wiring.Settings(ingester_token="b"))

    assert result.tracker is None
    assert result.store.gitea is None


def test_build_admission():
    settings = wiring.Settings(ingester_token="b")

    result = wiring.build_admission(settings)

    assert result == adapters.TemplateAdmission(
        secret_file=settings.service_token_secret_file,
        cli=settings.soliplex_cli,
        environment=settings.environment,
    )


# ---- create_app ----


def test_create_app():
    settings = wiring.Settings(ingester_token="b")

    result = wiring.create_app(settings)

    assert result.state.provisioner == wiring.build_provisioner(settings)
    assert result.state.admission == wiring.build_admission(settings)


def test_create_app_from_the_process_environment(token_file, monkeypatch):
    monkeypatch.setenv("PROVISIONER_INGESTER_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PROVISIONER_SERVICE_SUBJECT", "from-env")

    result = wiring.create_app()

    assert result.state.provisioner.backend.service_subject == "from-env"


# ---- the contract, against the wired service ----


@pytest.fixture
def jsonpath_registry(monkeypatch):
    """Soliplex's JSONPath function registry, restored after the test."""
    environment = authz.the_jsonpath_environment
    monkeypatch.setattr(
        environment,
        "function_extensions",
        dict(environment.function_extensions),
    )


@pytest.fixture
def stack_settings(tmp_path, monkeypatch, jsonpath_registry):
    """Settings for a small stack under ``tmp_path``, in an empty git env."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)

    environment = tmp_path / "environment"
    environment.mkdir()
    clone = tmp_path / "src" / "rooms-provisioned"
    (environment / "installation.yaml").write_text(
        f'id: "test"\nroom_paths:\n  - "./rooms"\n  - "{clone}"\n'
        "meta:\n  jsonpath_functions:\n"
        '    - {name: "is_foreman", func: "json.dumps"}\n'
    )
    ingester_config = tmp_path / "haiku.rag.yaml"
    ingester_config.write_text(
        mako_template.Template(
            filename=str(REPO / "haiku.rag" / "haiku.rag.yaml.mako"),
            strict_undefined=True,
        ).render(chunk_size=256, rag_embed_model="m", rag_embed_dim=8)
    )
    (tmp_path / "db").mkdir()
    secret_file = tmp_path / "url_safe_token_secret"
    secret_file.write_text(SECRET)
    return wiring.Settings(
        ingester_token="bearer",
        environment=environment,
        clone=clone,
        uploads_dir=tmp_path / "uploads" / "rooms",
        db_dir=tmp_path / "db",
        ingester_config=ingester_config,
        service_token_secret_file=secret_file,
    )


class _Stack:
    """The toolkit's calls to soliplex-cli, the backend and the ingester.

    ``admins`` and ``writers`` hold the ``preferred_username`` values that
    are admins, and that hold WRITE on ``__provision__``.
    """

    def __init__(self, monkeypatch, settings):
        self.settings = settings
        self.admins = {"root"}
        self.writers = {"alice", "bob"}
        cli = adapters.soliplex_cli
        monkeypatch.setattr(cli, "stale_policy_ids", lambda *a: set())
        monkeypatch.setattr(cli, "set_policy", lambda *a: None)
        monkeypatch.setattr(cli, "remove_policy", lambda *a: None)
        monkeypatch.setattr(cli, "audit_rooms", lambda *a: None)
        monkeypatch.setattr(cli, "is_admin", self._is_admin)
        monkeypatch.setattr(cli, "may", self._may)
        api = adapters.soliplex_api
        monkeypatch.setattr(api, "check_access", lambda *a, **k: None)
        monkeypatch.setattr(api, "reload_rooms", self._reload_rooms)
        monkeypatch.setattr(adapters.ingester, "reload", self._reload)

    def _user(self, token):
        claims, _reason = token_mint.load_service_token(SECRET, token)
        return (claims or {}).get("preferred_username")

    def _is_admin(self, cli, environment, token):
        return self._user(token) in self.admins

    def _may(self, cli, environment, token, room_id, permission):
        return self._user(token) in self.writers

    def _rooms(self):
        """Every room the clone's fragments register."""
        fragments = self.settings.clone / rendering.FRAGMENTS_DIR
        return [path.stem for path in fragments.glob("*.yaml")]

    def _reload(self, url, token):
        return {
            "outcome": "applied",
            "databases": [
                {"database": room, "change": "added", "error": None}
                for room in self._rooms()
            ],
        }

    def _reload_rooms(self, base_url, *, token=None):
        return {"added": self._rooms(), "removed": [], "changed": []}


def _token(user):
    claims = {"preferred_username": user, "sub": f"{user}-sub"}
    return token_mint.mint_service_token(SECRET, claims, expires_in=300)


class _Harness:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.admitted_token = _token("alice")
        self.other_token = _token("bob")
        self.admin_token = _token("root")
        self.not_admitted_token = _token("carol")

    def occupy(self, name):
        room = f"alice:{room_ids.slugify(name)}"
        (self.settings.clone / room).mkdir(parents=True)


@pytest.mark.anyio
async def test_the_wired_service_keeps_the_contract(
    stack_settings, monkeypatch
):
    _Stack(monkeypatch, stack_settings)
    app = wiring.create_app(stack_settings)
    app.state.access.update(True)
    harness = _Harness(app, stack_settings)

    result = await contract.check_all(harness)

    assert result is None


# ---- main ----


def _fake_provision(monkeypatch, *, result=None, error=None):
    calls = []

    def provision_room(claims, **kwargs):
        calls.append((claims, kwargs))
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(wiring.provision, "provision_room", provision_room)
    return calls


def _provisioned(**changes):
    owner = room_ids.owner_from_claims(CLAIMS)
    request = interfaces.RoomRequest(
        requester_claims=CLAIMS,
        owner=owner,
        room_id=room_ids.make_room_id(owner, "q3-plans"),
        name="Q3 Plans",
        description="Q3 Plans",
        requested_at=datetime.datetime(2026, 11, 3, tzinfo=datetime.UTC),
    )
    return dataclasses.replace(
        interfaces.Provisioned(request=request, commit="0123456789abcdef"),
        **changes,
    )


def test_main(token_file, monkeypatch, capsys):
    calls = _fake_provision(monkeypatch, result=_provisioned())

    result = wiring.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"],
        _environ(token_file),
    )

    assert result == 0
    [(claims, kwargs)] = calls
    assert claims == CLAIMS
    assert kwargs == {
        "name": "Q3 Plans",
        "provisioner": wiring.build_provisioner(
            wiring.Settings(ingester_token="bearer")
        ),
        "description": None,
        "slug": None,
    }
    assert capsys.readouterr().out == (
        f"provisioned room '{ROOM}' (commit 0123456789ab)\n"
    )


def test_main_incomplete(token_file, monkeypatch, capsys):
    issue = adapters.concierge_gitea.CreatedGiteaIssue(
        number=7,
        url="https://gitea.example.com/ops/requests/issues/7",
        title="t",
        profile="attached",
    )
    _fake_provision(
        monkeypatch,
        result=_provisioned(issue=issue, unfinished=("push it", "reload it")),
    )

    result = wiring.main(
        [
            "--claims",
            json.dumps(CLAIMS),
            "--name",
            "Q3 Plans",
            "--description",
            "D",
            "--slug",
            "plans",
        ],
        _environ(token_file),
    )

    assert result == 1
    assert capsys.readouterr().out == (
        f"provisioned room '{ROOM}' (commit 0123456789ab)\n"
        "tracking issue: https://gitea.example.com/ops/requests/issues/7\n"
        "still to do:\n"
        "  - push it\n"
        "  - reload it\n"
    )


def test_main_error(token_file, monkeypatch, capsys):
    _fake_provision(monkeypatch, error=provision.RoomExists(ROOM))

    result = wiring.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"],
        _environ(token_file),
    )

    assert result == 2
    assert capsys.readouterr().err == (
        f"error: room '{ROOM}' already exists; choose another name\n"
    )


def test_main_settings_error(tmp_path, capsys):
    environ = {"PROVISIONER_INGESTER_TOKEN_FILE": str(tmp_path / "missing")}

    result = wiring.main(
        ["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"], environ
    )

    assert result == 2
    assert "cannot read the ingester's bearer token" in capsys.readouterr().err


def test_main_claims_not_an_object(capsys):
    result = wiring.main(["--claims", "[1, 2]", "--name", "Q3 Plans"], {})

    assert result == 2
    assert capsys.readouterr().err == (
        "error: --claims must be a JSON object\n"
    )


def test_main_uses_the_process_environment(token_file, monkeypatch):
    monkeypatch.setenv("PROVISIONER_INGESTER_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PROVISIONER_SERVICE_SUBJECT", "from-env")
    calls = _fake_provision(monkeypatch, error=provision.RoomExists(ROOM))

    wiring.main(["--claims", json.dumps(CLAIMS), "--name", "Q3 Plans"])

    [(_claims, kwargs)] = calls
    assert kwargs["provisioner"].backend.service_subject == "from-env"


# ---- the shims ----


def test_provision_shim_main():
    result = provision_shim.main

    assert result is wiring.main


def test_provision_shim_service_subject():
    result = provision_shim.DEFAULT_SERVICE_SUBJECT

    assert result == adapters.DEFAULT_SERVICE_SUBJECT


def test_provision_service_shim():
    result = service_shim.create_app

    assert result is wiring.create_app
