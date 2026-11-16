# Deploying to a DigitalOcean droplet, with DO serverless inference

Loads when: Load this reference when the user wants to run a generated stack on a DigitalOcean droplet, or to use DigitalOcean serverless inference (its models or inference routers) for a stack's chat and embeddings.

## What this deployment looks like

One droplet runs the whole Compose stack; inference runs at DigitalOcean,
so the droplet needs no GPU and manages no models. DO serverless inference
is a hosted OpenAI-compatible endpoint, so the generation inputs and the
post-generation wiring are those in `INFERENCE_PROVIDERS.md`. This page
supplies the DO-specific values, sizes the droplet, and covers exposing
the stack.

## Sizing the droplet

These figures come from one deployment (soliplex 0.89, haiku.rag 0.104.0,
docling-serve `cpu` v1.38.0, frontend 0.114) on a 4 GB / 2 vCPU droplet
that also ran Apache. Treat the measured rows as a floor, not a benchmark.

Resident memory per service, measured idle and while ingesting markdown:

| Service | Memory |
|---|---|
| docling-serve | 1.25–1.4 GiB |
| backend | ~610 MiB |
| haiku-ingester | 350–440 MiB |
| soliplex-provisioner | ~270 MiB |
| postgres | 50–75 MiB |
| nginx | ~5 MiB |

That is about 2.6 GiB for the stack. With the host's own processes, about
500 MB stayed available while ingesting.

Choose by corpus:

- **Markdown and plain text only: 4 GB / 2 vCPU works (measured)**, with
  little headroom. The ingester processed one file at a time, using a full
  core, at 9–20 s per Sphinx-sized markdown page: about 10–15 minutes for
  a 60-file corpus. docling-serve stayed near idle, since markdown takes
  its light path (no layout, table-structure, or OCR models).
- **Recommended: 8 GB / 4 vCPU (estimate, not measured).** It leaves
  room for concurrent users and for the occasional PDF.
- **PDFs, Office files with images, or OCR: 16 GB (estimate).**
  docling-serve's CPU image loads its layout and OCR models for these and
  spikes by several GB per conversion; on 4 GB a PDF is likely to be
  OOM-killed.

For a corpus with PDFs, bound conversion memory before buying a bigger
droplet:

- **Split large PDFs.** In the ingester's `haiku.rag/haiku.rag.yaml`, set
  `split_pages` in the `processing:` block. The template leaves it unset,
  which disables splitting.

  ```yaml
  processing:
    split_pages: 10
  ```

  Each 10-page slice is then converted as its own docling-serve task, so
  peak memory is one slice's working set rather than the whole document's.
  `10` is haiku.rag's suggested starting point, and it is what fixed an
  ingestion run that a single huge PDF had blown up. The cost is small: a
  paragraph or caption that straddles a slice boundary is split into two
  items, and cross-page links are dropped. Restart `haiku-ingester` after
  changing it.
- **Cap file size.** Set `max_file_size` on the ingester's sources, so a
  file too large even for slicing goes to the dead-letter queue instead of
  taking docling-serve down.

Disk matters as much as memory. The images alone total about 12 GB:

| Image | Size |
|---|---|
| `docling-serve-cpu` | 7.5 GB |
| backend | 1.9 GB |
| `haiku.rag-slim` | 1.6 GB |
| postgres | 0.6 GB |

The provisioner and nginx images, and the build cache, come on top of
that. Docker keeps all of this under `/var/lib/docker` on the root disk,
not on an attached volume. A 25 GB root disk was left with 5 GB free,
too little for an image upgrade. Either choose a droplet whose root disk
is 50 GB or more (the 8 GB plans have 160 GB), or move Docker's
`data-root` to a volume before the first build. The stack directory
itself (databases, uploads, `rag/`) can live on a volume.

## Preparing the droplet

On Ubuntu (24.04 or later):

- **Docker:** either `docker-ce` and the compose plugin from Docker's apt
  repository, or Ubuntu's `docker.io` with `docker-compose-v2`. With the
  Ubuntu packages, every build prints `Docker Compose is configured to
  build using Bake, but buildx isn't installed`; it is harmless. Install
  `docker-buildx` to silence it. Docker 29 also needs the `/tmp`
  workaround in GENERATION.md (#345).
- **The docker group:** add the operator's user to it (`sudo usermod
  -aG docker $USER`), then open a new SSH session.
- **uv:** install it, and a Python of 3.13 or later, for the skill's
  scripts and the generator.
- **The skill:** run a published build of this skill *on the droplet*.
  Download a release asset (`soliplex-template-skill.tar.gz` from a `v…`
  release) and run its `scripts/generate_soliplex_project.py` there, so
  that the stack and its `.secrets/` are created on the machine that runs
  them.

## DO serverless inference

- **Endpoint (`$BASE`):** `https://inference.do-ai.run/v1`.
- **Key (`$K`):** a *model access key* from the DO control panel's Model
  Studio. A DigitalOcean API token does not work here.
- **Secret names:** `do_inference_key` for the Docker secret and
  `DO_INFERENCE_KEY` for the Soliplex secret, in place of the generic
  names in `INFERENCE_PROVIDERS.md`.

Open-weight models run on DO's own infrastructure. The `anthropic-*` and
`openai-*` models pass through to those vendors, which matters to some
users.

### Chat models and inference routers

A *router* picks a model for each request, so one id can serve all four
chat-style parameters. The routers a key can call are listed by
`/v1/models` as `router:<name>`. DO's presets were:

- `router:general`
- `router:knowledge-base-document`
- `router:software-engineering`
- `router:writing`

`router:knowledge-base-document` suits a RAG stack. In testing it chose
`deepseek-v4-flash-0731` for most prompts and `glm-5.3-flash` for a
retrieval-shaped one. Each response's `model` field names the model that
answered, which is the only way to see what a router chose; the API does
not list a router's candidate pool. The routed models reason, so judge
probes by HTTP status (see `INFERENCE_PROVIDERS.md`).

A router created in the control panel may not be callable by a given key.
A custom router (named `inference-router-balanced`, with the "budget"
routing preference) answered `404 model router not found` both as
`router:<name>` and by its bare name, and did not appear in `/v1/models`
for that key. The cause was not determined. Only use a router id that
`/v1/models` lists for the key the stack will use. If the user's router is
missing, tell them, and offer a preset router or a fixed model.

### Embedding model

Take `qwen3-embedding-0.6b`, with `rag_embed_dim: 1024` (verified). It
also matches the template's default chunking tokenizer
(`Qwen/Qwen3-Embedding-0.6B`). `/v1/models` also listed `bge-m3`,
`gte-large-en-v1.5`, `e5-large-v2`, and `all-mini-lm-l6-v2`, whose
dimensions were not probed. Probe before using any of them.

## Exposure

A generated stack has no OIDC provider, so anyone who reaches nginx can
chat, upload, and spend the user's inference credit. Ask before exposing
it. The choices, safest first:

- **SSH tunnel (the default for a trial).** In `docker-compose.yml`, bind
  nginx's published ports to loopback:

  ```yaml
      ports:
        - "127.0.0.1:9000:9000"
        - "127.0.0.1:9443:9443"
  ```

  The user then runs `ssh -L 9443:localhost:9443 <droplet>` and browses to
  `https://localhost:9443`; the certificate is self-signed. In the web
  client's connect screen they enter `https://localhost:9443`, not the
  `http://localhost:8000` it may offer.
- **Public, behind sign-in.** Add a provider (OIDC_PROVIDERS.md or
  AUTHELIA.md) before `stack-init`.
- **Public, filtered at the network edge.** Use a DO Cloud Firewall.
  `ufw` does not help: Docker's iptables rules bypass it for published
  ports.

Only nginx publishes host ports by default. Leave the other port
parameters unset on a droplet.

## Getting documents in

- **`rag/docs/`** is the ingester's watched directory for the stack's
  `haiku.rag` database. An `rsync` or `scp` there as the droplet user is
  ingested within seconds, and the user's uid matches the containers'
  `PUID` when the stack was generated as that user.
- **Uploads in the web client** are ingested only for provisioned rooms
  (see ROOMS.md). For users who will upload their own documents, provision
  a room rather than adding one with `add_room.py`.
- **Progress** is in `docker compose logs -f haiku-ingester`, and in the
  ingester dashboard at `https://localhost:9443/ingester/`. The dashboard
  shows the current job under *claimed*; its lists go briefly empty
  between refreshes and after an ingester restart.
