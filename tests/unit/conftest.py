"""Fixtures shared across the unit suite."""

import os

import pytest


@pytest.fixture
def expected_mode():
    """Map a POSIX permission mode to what ``os.stat`` reports here.

    Windows tracks only the read-only flag: ``chmod`` keeps or clears the
    owner-write bit, and ``stat`` reports a regular file as ``0o666``
    (writable) or ``0o444`` (read-only), whatever mode was requested.
    """

    def _expected_mode(posix_mode: int) -> int:
        writable = 0o666 if posix_mode & 0o200 else 0o444
        return posix_mode if os.name != "nt" else writable

    return _expected_mode
