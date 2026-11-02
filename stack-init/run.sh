#!/bin/sh
# Prepares the stack (see README.md): runs in the 'stack-init' service,
# with the backend's environment at /environment.
set -eu

SOLIPLEX_CLI=/app/.venv/bin/soliplex-cli
PYTHON=/app/.venv/bin/python

echo "stack-init: Soliplex databases to their current schema"
"$SOLIPLEX_CLI" database upgrade /environment

# Steps run in name order; anything other than '*.sh' / '*.py' is skipped.
for step in /stack-init/init.d/*; do
    case "$step" in
        *.sh)
            echo "stack-init: $(basename "$step")"
            sh "$step"
            ;;
        *.py)
            echo "stack-init: $(basename "$step")"
            "$PYTHON" "$step"
            ;;
    esac
done

echo "stack-init: done"
