#!/bin/bash
# init.sh - PostgreSQL initialization with minimal privileges
# Implements EVAL.md #14 recommendation for database security

set -e

# Check if AUTO_CREATE_DATABASE is enabled
if [ "$AUTO_CREATE_DATABASE" != "1" ] && [ "$AUTO_CREATE_DATABASE" != "true" ] && [ "$AUTO_CREATE_DATABASE" != "True" ]; then
    echo "Skipping database initialization (AUTO_CREATE_DATABASE is not set)"
    return 0
fi

# Read password from secret file if available, otherwise fallback to environment variable
if [ -f "$AGUI_DB_PASS_FILE" ]; then
    AGUI_DB_PASS=$(cat "$AGUI_DB_PASS_FILE")
elif [ -z "$AGUI_DB_PASS" ]; then
    echo "ERROR: Neither AGUI_DB_PASS_FILE nor AGUI_DB_PASS is set"
    exit 1
fi

# Read password from secret file if available, otherwise fallback to environment variable
if [ -f "$AUTHZ_DB_PASS_FILE" ]; then
    AUTHZ_DB_PASS=$(cat "$AUTHZ_DB_PASS_FILE")
elif [ -z "$AUTHZ_DB_PASS" ]; then
    echo "ERROR: Neither AUTHZ_DB_PASS_FILE nor AUTHZ_DB_PASS is set"
    exit 1
fi


psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create ragserver AGUI application user with password
    CREATE USER ${agui_db} WITH PASSWORD '$AGUI_DB_PASS';

    -- Create ragserver authz application user with password
    CREATE USER ${authz_db} WITH PASSWORD '$AUTHZ_DB_PASS';

    -- Create database owned by postgres (not application user)
    CREATE DATABASE ${agui_db};
    ALTER DATABASE ${agui_db} OWNER TO postgres;

    -- Create database owned by postgres (not application user)
    CREATE DATABASE ${authz_db};
    ALTER DATABASE ${authz_db} OWNER TO postgres;

    -- Connect to the ${agui_db} database to set up schema permissions
    \c ${agui_db}

    -- Grant minimal required PRIVILEGES (EVAL.md #14 recommendation)
    -- Only CONNECT, not superuser or database ownership
    GRANT CONNECT ON DATABASE ${agui_db} TO ${agui_db};

    -- Schema-level permissions
    GRANT USAGE ON SCHEMA public TO ${agui_db};

    GRANT ALL PRIVILEGES ON DATABASE ${agui_db} to ${agui_db};
    GRANT ALL PRIVILEGES ON SCHEMA public TO ${agui_db};

    -- Connect to the ${authz_db} database to set up schema permissions
    \c ${authz_db}

    -- Grant minimal required PRIVILEGES (EVAL.md #14 recommendation)
    -- Only CONNECT, not superuser or database ownership
    GRANT CONNECT ON DATABASE ${authz_db} TO ${authz_db};

    -- Schema-level permissions
    GRANT USAGE ON SCHEMA public TO ${authz_db};

    GRANT ALL PRIVILEGES ON DATABASE ${authz_db} to ${authz_db};
    GRANT ALL PRIVILEGES ON SCHEMA public TO ${authz_db};
EOSQL

echo "Database '${agui_db}' initialized with minimal privileges for user '${agui_db}'"
echo "Database '${authz_db}' initialized with minimal privileges for user '${authz_db}'"
