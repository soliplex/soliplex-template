"""Custom agent tools for the ``${package_name}`` Soliplex install.

A Soliplex "tool" is just a dotted name resolving to a plain callable (see a
room's ``tools:`` list). Reference :func:`greeting` from a room config as
``tool_name: "${package_name}.tools.greeting"``.
"""


def greeting(name: str) -> str:
    """Return a friendly greeting for ``name``.

    A minimal example tool: a plain, type-annotated function with a
    docstring (the LLM uses the docstring as the tool's description).
    """
    return f"Hello, {name}! This greeting came from your own package's tool."
