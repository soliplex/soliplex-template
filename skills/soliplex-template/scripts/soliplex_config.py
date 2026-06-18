#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["soliplex-plumber>=0.3"]
# ///
"""Query a running Soliplex stack's resolved installation config.

A thin front end over ``soliplex_plumber.soliplex_config``, installed from
PyPI by ``uv run`` per the inline metadata above.
``soliplex_plumber.soliplex_config.run`` is the package's ``soliplex-config``
console-script entry point -- it parses ``sys.argv`` and prints a user-facing
error (no traceback) on failure.

``soliplex-cli config <installation>`` exports the *resolved* installation
config as YAML, but ``soliplex-cli`` only exists inside the backend image, so
this runs it in a one-off backend container via ``docker compose run --rm`` and
parses the output. The subcommands expose that config at four levels of
granularity -- ``show`` (the whole config), ``get <key>`` (one dotted-path
value), ``rooms`` (a ``{room_id, name, description}`` mapping per loaded room),
and ``room <room_id>`` (one room's full ``room_config.yaml``)::

    uv run soliplex_config.py show       --project-dir /path/to/stack
    uv run soliplex_config.py get room_paths --project-dir /path/to/stack
    uv run soliplex_config.py rooms      --project-dir /path/to/stack
    uv run soliplex_config.py room chat  --project-dir /path/to/stack
"""

from __future__ import annotations

import sys

from soliplex_plumber.soliplex_config import run

if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
