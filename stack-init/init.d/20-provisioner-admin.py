"""Admit the room provisioner's service tokens as an admin's.

The provisioner calls the backend with service tokens it mints for itself,
whose subject ('sub' claim) is 'PROVISIONER_SERVICE_SUBJECT' (default
'soliplex-provisioner'). With an OIDC provider, the backend admits them only
through an 'admin_users' entry matching that subject; without one, it treats
every call as the no-auth user, and the entry does no harm. This step adds
the entry when it is missing, for the subject the provisioner itself uses:
'stack-init' reads the same '.env' and environment.
"""

import json
import os
import subprocess
import sys

from soliplex_template.provisioner import adapters

CLI = "/app/.venv/bin/soliplex-cli"
ENVIRONMENT = "/environment"
SUBJECT = (
    os.environ.get("PROVISIONER_SERVICE_SUBJECT")
    or adapters.DEFAULT_SERVICE_SUBJECT
)
JSON_PATH = f"$[?$.sub == {json.dumps(SUBJECT)}]"


def main() -> int:
    listed = subprocess.run(
        [CLI, "admin-users", "list", ENVIRONMENT],
        capture_output=True,
        text=True,
        check=True,
    )
    # 'admin-users list' prints its report as one JSON line, the last line of
    # its output: '{"admin_users": [...]}'. Entries not keyed by email show
    # as their JSONPath. Any position in that list counts as already present.
    report = json.loads(listed.stdout.strip().splitlines()[-1])
    if JSON_PATH in report["admin_users"]:
        print(f"  the provisioner ({SUBJECT!r}) is already an admin")
        return 0

    subprocess.run(
        [CLI, "admin-users", "add", ENVIRONMENT, "--json-path", JSON_PATH],
        check=True,
    )
    print(f"  the provisioner ({SUBJECT!r}) is now an admin: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
