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
if [ -f "$INGESTION_DB_PASS_FILE" ]; then
    INGESTION_DB_PASS=$(cat "$INGESTION_DB_PASS_FILE")
elif [ -z "$INGESTION_DB_PASS" ]; then
    echo "ERROR: Neither INGESTION_DB_PASS_FILE nor INGESTION_DB_PASS is set"
    exit 1
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
    -- Create ingetser application user with password
    CREATE USER soliplex_ingester WITH PASSWORD '$INGESTION_DB_PASS';

    -- Create ragserver AGUI application user with password
    CREATE USER soliplex_agui WITH PASSWORD '$AGUI_DB_PASS';

    -- Create ragserver authz application user with password
    CREATE USER soliplex_authz WITH PASSWORD '$AUTHZ_DB_PASS';

    -- Create database owned by postgres (not application user)
    CREATE DATABASE soliplex_ingester;
    ALTER DATABASE soliplex_ingester OWNER TO postgres;

    -- Create database owned by postgres (not application user)
    CREATE DATABASE soliplex_agui;
    ALTER DATABASE soliplex_agui OWNER TO postgres;

    -- Create database owned by postgres (not application user)
    CREATE DATABASE soliplex_authz;
    ALTER DATABASE soliplex_authz OWNER TO postgres;

    -- Connect to the soliplex_ingester database to set up schema permissions
    \c soliplex_ingester

    -- Grant minimal required privileges (EVAL.md #14 recommendation)
    -- Only CONNECT, not superuser or database ownership
    GRANT CONNECT ON DATABASE soliplex_ingester TO soliplex_ingester;

    -- Schema-level permissions
    GRANT USAGE ON SCHEMA public TO soliplex_ingester;

    GRANT ALL PRIVILEGES ON DATABASE soliplex_ingester to soliplex_ingester;
    GRANT ALL PRIVILEGES ON SCHEMA public TO soliplex_ingester;

    -- Connect to the soliplex_agui database to set up schema permissions
    \c soliplex_agui

    -- Grant minimal required PRIVILEGES (EVAL.md #14 recommendation)
    -- Only CONNECT, not superuser or database ownership
    GRANT CONNECT ON DATABASE soliplex_agui TO soliplex_agui;

    -- Schema-level permissions
    GRANT USAGE ON SCHEMA public TO soliplex_agui;

    GRANT ALL PRIVILEGES ON DATABASE soliplex_agui to soliplex_agui;
    GRANT ALL PRIVILEGES ON SCHEMA public TO soliplex_agui;

    -- Connect to the soliplex_authz database to set up schema permissions
    \c soliplex_authz

    -- Grant minimal required PRIVILEGES (EVAL.md #14 recommendation)
    -- Only CONNECT, not superuser or database ownership
    GRANT CONNECT ON DATABASE soliplex_authz TO soliplex_authz;

    -- Schema-level permissions
    GRANT USAGE ON SCHEMA public TO soliplex_authz;

    GRANT ALL PRIVILEGES ON DATABASE soliplex_authz to soliplex_authz;
    GRANT ALL PRIVILEGES ON SCHEMA public TO soliplex_authz;
EOSQL

echo "Database 'soliplex_ingester' initialized with minimal privileges for user 'soliplex_ingester'"
echo "Database 'soliplex_agui' initialized with minimal privileges for user 'soliplex_agui'"
echo "Database 'soliplex_authz' initialized with minimal privileges for user 'soliplex_authz'"
