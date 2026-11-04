"""Deprecated: the room provisioner is :mod:`soliplex_template.provisioner`.

The provisioner now runs on soliplex-concierge's provisioner framework,
with the template's adapters and wiring in the
:mod:`soliplex_template.provisioner` package. This module stays for one
release so that stacks generated before the move keep working:

- :func:`main`, which their ``soliplex-provision-room`` command line runs
- :data:`DEFAULT_SERVICE_SUBJECT`, which their ``stack-init`` imports

Use :mod:`soliplex_template.provisioner.wiring` (and ``.adapters``)
instead.
"""

from soliplex_template.provisioner import adapters
from soliplex_template.provisioner import wiring

DEFAULT_SERVICE_SUBJECT = adapters.DEFAULT_SERVICE_SUBJECT
main = wiring.main
