"""The bundled ``soliplex_config.py`` is a thin shim over soliplex_plumber.

The implementation lives in ``soliplex_plumber.soliplex_config`` and is
unit-tested there; the bundled script just re-exposes its ``run`` entry point
as the skill's front end. The script ships inside the skill and is not an
importable package, so it is loaded here by file path via ``importlib.util`` --
which also exercises its module body -- and the one test checks the wiring.
"""

from __future__ import annotations

import importlib.util
import pathlib

from soliplex_plumber import soliplex_config as plumber_config

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "skills"
    / "soliplex-template"
    / "scripts"
    / "soliplex_config.py"
)
_spec = importlib.util.spec_from_file_location("soliplex_config", _MODULE_PATH)
soliplex_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(soliplex_config)


def test_shim_delegates_to_plumber_run():
    entry_point = soliplex_config.run

    assert entry_point is plumber_config.run
