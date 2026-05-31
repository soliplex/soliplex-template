"""Tests for :mod:`${package_name}.views`."""

from ${package_name} import views


def test_router_exposes_ping_route():
    paths = {route.path for route in views.router.routes}

    assert "/custom/ping" in paths


def test_ping_returns_pong():
    result = views.ping()

    assert result == {"ping": "pong"}
