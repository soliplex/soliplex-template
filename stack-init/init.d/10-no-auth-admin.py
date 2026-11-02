"""Make the no-auth user an admin while the stack has no OIDC provider.

With no 'auth_systems' in 'oidc/config.yaml', the backend runs without
authentication and treats every request as Soliplex's fixed no-auth user.
Admin-only routes (e.g. nginx's '/ingester/' gate) admit that user only once
it is an admin. A stack with a provider gets no such entry: its admins are
real accounts, added with 'soliplex-cli admin-users add'.
"""

import json
import pathlib
import subprocess
import sys

import yaml
from soliplex import installation

CLI = "/app/.venv/bin/soliplex-cli"
ENVIRONMENT = "/environment"
OIDC_CONFIG = pathlib.Path(ENVIRONMENT) / "oidc" / "config.yaml"
EMAIL = installation.NO_AUTH_MODE_USER_TOKEN["email"]


def main() -> int:
    config = yaml.safe_load(OIDC_CONFIG.read_text()) or {}
    if config.get("auth_systems"):
        print("  OIDC providers configured: no no-auth admin")
        return 0

    listed = subprocess.run(
        [CLI, "admin-users", "list", ENVIRONMENT],
        capture_output=True,
        text=True,
        check=True,
    )
    # 'admin-users list' prints its report as one JSON line, the last line of
    # its output: '{"admin_users": [...]}', with email-keyed admins shown as
    # the email. Any position in that list counts as already present.
    report = json.loads(listed.stdout.strip().splitlines()[-1])
    if EMAIL in report["admin_users"]:
        print(f"  {EMAIL} is already an admin")
        return 0

    subprocess.run([CLI, "admin-users", "add", ENVIRONMENT, EMAIL], check=True)
    print(f"  {EMAIL} (the no-auth user) is now an admin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
