# Configuration for the `soliplex` backend service

The backend image is built from `backend/Dockerfile` (which installs the pinned
`soliplex` release plus runtime dependencies via `uv`), and its runtime config
lives under `backend/environment/`.

This content now lives in the documentation site:

- **Backend image & dependencies** (building the image, adding third-party or
  local Python dependencies, gotchas):
  <https://soliplex.github.io/soliplex-template/architecture/backend/>
- **Backend configuration** (the `backend/environment/` layout, rooms, skills,
  the sandbox): <https://soliplex.github.io/soliplex-template/architecture/configuration/>

The docs sources are under `docs/` in the repository root.
