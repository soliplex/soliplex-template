"""Fixtures shared by the unit tests."""

from __future__ import annotations

import sys
import types

import pytest


class FakeTokenMint(types.ModuleType):
    """Stands in for ``soliplex.token_mint``: soliplex is in the stack's
    image, not among this package's dependencies.

    Mints ``minted-token``; ``tokens`` maps each token it validates to its
    claims. ``minted`` and ``loaded`` record the calls.
    """

    BAD_SIGNATURE = "bad signature"

    def __init__(self):
        super().__init__("soliplex.token_mint")
        self.tokens = {}
        self.minted = []
        self.loaded = []

    def mint_service_token(self, secret, claims, *, expires_in):
        self.minted.append((secret, claims, expires_in))
        return "minted-token"

    def load_service_token(self, secret, token):
        self.loaded.append((secret, token))
        if token in self.tokens:
            return self.tokens[token], None
        return None, self.BAD_SIGNATURE


@pytest.fixture
def token_mint(monkeypatch):
    """A fake ``soliplex.token_mint``, importable for the test's duration."""
    fake = FakeTokenMint()
    package = types.ModuleType("soliplex")
    package.token_mint = fake
    monkeypatch.setitem(sys.modules, "soliplex", package)
    monkeypatch.setitem(sys.modules, "soliplex.token_mint", fake)
    return fake


@pytest.fixture
def secret_file(tmp_path):
    """A signing-secret file, written as generate_secrets.py writes it."""
    path = tmp_path / "url_safe_token_secret.gen"
    path.write_text("s3cret\n")
    return path
