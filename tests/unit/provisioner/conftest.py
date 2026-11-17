"""Skip the POSIX-only provisioner test modules on Windows.

The provisioner runs only inside the stack's Linux container, never on an
operator's host.  These modules import ``fcntl`` -- directly, and/or via
``soliplex_concierge.provisioner.landing_clone`` -- which Windows lacks, so
they fail at collection there.  ``test_rendering.py`` has no such import and
still runs on Windows.
"""

import sys

POSIX_ONLY = ["test_adapters.py", "test_reconcile.py", "test_wiring.py"]

collect_ignore = POSIX_ONLY if sys.platform == "win32" else []
