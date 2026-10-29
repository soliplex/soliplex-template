# Docker secrets
.secrets/

#Python
*.pyc
.coverage
*.egg-info

# Virtualenvs (uv). Unanchored, so it also covers the per-project
# .venv under each project directory, not just the root one.
.venv/

# Dotenv
.env

# direnv
.direnv

# misc
tmp/

# RAG vector store (LanceDB), created under rag/db at runtime.
# Ignore the data, but keep the directory itself tracked (via its README):
# the haiku-ingester bind-mounts rag/db at /data, and a missing bind-mount
# source is recreated root-owned by the Docker daemon, after which the
# uid-1000 ingester can't write the LanceDB under /data.
rag/db/*
!rag/db/README.md

# Zensical docs build output (`zensical build`)
/site/

#logs
info.log

# android
local.properties
.android/

# osx
.DS_Store
._*

# data and conf directories (for testing)
data/
conf/

# Data uploaded to rooms / threads, created at runtime. Keep the dirs
# tracked (their bind-mount source must exist on a fresh clone, else the
# Docker daemon recreates it root-owned and the uid-1000 backend can't
# write to it) but ignore the uploaded files themselves.
uploads/rooms/*
uploads/threads/*
!uploads/rooms/.gitkeep
!uploads/threads/.gitkeep

# Per-run sandbox working dirs, created by agents at runtime. Keep the
# directory tracked (same root-owned-bind-mount reason as above) but
# ignore its contents.
backend/sandbox/workdirs/*
!backend/sandbox/workdirs/README.md

# The embedded skill template tree is a BUILD ARTIFACT, generated from the repo
# exemplars by scripts/refresh_skill_template.py. It is regenerated at skill
# build time (scripts/build_skill.py) and before the functional tests, so it is
# not committed. See issue #135.
skills/soliplex-template/assets/template/

# Every entry under src/ is a project directory. This project's own
# package is tracked; anything else cloned there (the soliplex
# checkout, a third-party repo you want to hack on) belongs to its
# own repo, so it is ignored without needing a line per clone.
/src/*
!/src/${package_name}/
