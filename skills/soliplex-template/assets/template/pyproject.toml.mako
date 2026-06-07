[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "${project_name}"
version = "0.1.0"
requires-python = ">=3.13"
# Host-side dependencies for this project's own tooling and the custom code
# under src/${package_name}/ (e.g. running `soliplex-cli` against the Postgres
# backing store, or this project's tests). The container images install their
# own pinned deps; this file is for host-side use:
#   uv sync                 # create/refresh the dev environment
#   uv pip install -e .     # or a plain editable install
dependencies = [
    "soliplex ${soliplex_backend_constraint}",
    "psycopg[binary]",
    "asyncpg",
]

[dependency-groups]
dev = [
    "pytest",
    # Builds the documentation site under docs/ (`uv run zensical build`).
    "zensical",
]

# src/ layout: the importable package lives at src/${package_name}/. The
# Soliplex backend puts src/ on PYTHONPATH (see docker-compose.yml); the
# build + test config below points at the same layout for host-side use.
[tool.hatch.build.targets.wheel]
packages = ["src/${package_name}"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
pythonpath = ["src"]
