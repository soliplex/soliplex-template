"""Deprecated: the provisioner service is built by
:func:`soliplex_template.provisioner.wiring.create_app`.

This module stays for one release so that stacks generated before the
move keep working: their ``soliplex-provisioner`` service runs
``uvicorn --factory soliplex_template.provision_service:create_app``.
"""

from soliplex_template.provisioner import wiring

create_app = wiring.create_app
