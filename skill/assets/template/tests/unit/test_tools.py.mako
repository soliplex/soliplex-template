"""Tests for :mod:`${package_name}.tools`."""

from ${package_name} import tools


def test_greeting_includes_name():
    result = tools.greeting("Ada")

    assert "Ada" in result
