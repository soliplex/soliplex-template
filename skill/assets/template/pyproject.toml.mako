[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# Dependencies for running this project's own tooling outside Docker
# (e.g. `soliplex-cli` against the Postgres backing store). The container
# images install their own pinned deps; this file is for host-side use:
#   uv sync     # or: pip install -e .
dependencies = [
    "soliplex ${soliplex_backend_constraint}",
    "psycopg[binary]",
    "asyncpg",
]
