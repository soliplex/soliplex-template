# Configuration for the `soliplex` backend service

The backend image is built from `backend/Dockerfile`, which builds `FROM` the
published `ghcr.io/soliplex/soliplex-postgres` release image and installs any
extra packages listed in `backend/requirements.txt`. Its runtime configuration
lives under `backend/environment/`.

This content now lives in the documentation site:

- **Backend image & dependencies** (building the image, adding third-party or
  local Python dependencies, gotchas):
  <https://soliplex.github.io/soliplex-template/users/architecture/backend/>
- **Backend configuration** (the `backend/environment/` layout, rooms, skills,
  the sandbox): <https://soliplex.github.io/soliplex-template/users/architecture/configuration/>

The docs sources are under `docs/` in the repository root.
