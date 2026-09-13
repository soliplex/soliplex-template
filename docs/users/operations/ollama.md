---
icon: lucide/cpu
---

# Local Ollama

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

Every model this stack names — the chat models, the title model, the RAG
question-answering model, and the embedding model — resolves through the
Ollama server at `OLLAMA_BASE_URL` in `.env`. When that server runs on the
Docker host rather than another machine, it usually is not reachable from the
containers, and the fix is less obvious than it looks.

## Why the default install is unreachable

A local Ollama install binds to `127.0.0.1:11434`. Each container has its own
network namespace, so `localhost` inside `backend` is *the backend container*,
not the host — and the host's loopback interface is not routable from any
container at all. `OLLAMA_BASE_URL=http://localhost:11434` therefore fails from
`backend` and `haiku-ingester` no matter what else is configured.

Confirm what your server is bound to:

```bash
ss -ltn | grep 11434
#  LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:*   <- loopback only, unreachable
#  LISTEN 0 4096   0.0.0.0:11434 0.0.0.0:*   <- all interfaces, incl. the LAN
```

Binding to `0.0.0.0` does make Ollama reachable, but it also publishes an
unauthenticated model server to every network the host is attached to. The
options below keep it off the network while still reaching the containers.

## Address Ollama by IP, never by hostname

!!! warning "A friendly hostname will return 403"
    Ollama has a DNS-rebinding guard that rejects any request whose `Host`
    header is neither `localhost` nor an IP literal. Give `OLLAMA_BASE_URL` a
    name — say by adding an `extra_hosts` alias so services can use
    `http://ollama:11434` — and every request is answered **403 with an empty
    body**.

This is worth internalising before trying any option below, because the
failure does not look like a networking problem. It surfaces in the backend
log as a model error:

```text
Stream error: status_code: 403, model_name: <model>, body:
```

Reproduce the rule directly against the server:

```bash
curl -o /dev/null -w '%{http_code}\n' -H 'Host: ollama:11434' \
  http://127.0.0.1:11434/api/tags      # 403
curl -o /dev/null -w '%{http_code}\n' -H 'Host: 172.17.0.1:11434' \
  http://127.0.0.1:11434/api/tags      # 200
```

A TCP relay cannot paper over this: it forwards bytes and never sees the HTTP
header. So `OLLAMA_BASE_URL` must carry a bare IP address.

!!! note "`OLLAMA_ORIGINS` is a different setting"
    `OLLAMA_ORIGINS` controls CORS — the browser's `Origin` header. It has no
    effect on the `Host` check described here.

## Option 1 — relay the host's Ollama (recommended)

Leave Ollama bound to `127.0.0.1` and add a small proxy service which
republishes it on the Docker bridge gateway, `172.17.0.1`. Containers can
route to that address; no other machine can.

Add the service to `docker-compose.yml`:

```yaml
  ollama-relay:

    image: alpine/socat:latest

    # Required: only a container sharing the host's network namespace can
    # connect to the host's 127.0.0.1. Incompatible with 'ports:'/'networks:'.
    network_mode: host

    command:
      - "TCP-LISTEN:11434,bind=${OLLAMA_RELAY_BIND:-172.17.0.1},fork,reuseaddr"
      - "TCP:127.0.0.1:11434"

    restart: unless-stopped
```

Point the stack at it in `.env`:

```bash
OLLAMA_BASE_URL=http://172.17.0.1:11434
OLLAMA_RELAY_BIND=172.17.0.1
```

Optionally add `ollama-relay` to the `depends_on` of `backend` and
`haiku-ingester` so it starts first.

!!! note "Confirm your bridge gateway"
    `172.17.0.1` is the conventional `docker0` address, but it is a daemon-level
    detail. Check with `ip -4 addr show docker0` and adjust both values if your
    host differs.

This option changes nothing about how Ollama itself is installed or
supervised, which makes it the least invasive choice for a systemd-managed
install.

## Option 2 — rebind Ollama to the bridge gateway

The same reachability without a proxy service: tell Ollama to listen on
the bridge gateway instead of loopback.

```bash
sudo systemctl edit ollama
```

```ini
[Service]
Environment="OLLAMA_HOST=172.17.0.1:11434"
```

```bash
sudo systemctl restart ollama
```

Then set `OLLAMA_BASE_URL=http://172.17.0.1:11434` in `.env`.

The trade-off is on the host side: the `ollama` CLI defaults to
`127.0.0.1:11434` and will no longer find the server, so you must export
`OLLAMA_HOST=172.17.0.1:11434` in your shell to use `ollama list`, `ollama
pull`, and friends.

## Option 3 — run Ollama inside the stack

The strongest isolation. Ollama joins the Compose network as an ordinary
service with **no `ports:` mapping**, so it is reachable from the stack and
from nowhere else — not from the host's LAN interfaces, and not from
containers outside this project.

Stop the host service first, so the two do not compete for the same model
store and GPU:

```bash
sudo systemctl disable --now ollama
```

```yaml
  ollama:

    image: ollama/ollama:latest

    # Deliberately no 'ports:' -- reachable only on the Compose network.

    volumes:
      - ollama_models:/root/.ollama

    restart: unless-stopped
```

Add `ollama_models:` to the top-level `volumes:` block, and set
`OLLAMA_BASE_URL=http://ollama:11434` in `.env`.

!!! note "The hostname works here — and only here"
    This is the one option where a name is correct rather than a 403. The
    official image sets `OLLAMA_HOST=0.0.0.0`, which lifts the `Host` check
    described above. Inside the container that is not an exposure, because the
    port is never published.

Two things to plan for:

- **Existing models.** A fresh volume starts empty and every model re-downloads.
  To reuse what the host already pulled, find the store with
  `systemctl show ollama -p Environment | tr ' ' '\n' | grep OLLAMA_MODELS`
  (commonly `/usr/share/ollama/.ollama/models`) and bind-mount it instead of
  using a named volume. Run the container as the owning uid:gid so new pulls do
  not leave root-owned blobs behind.
- **GPU access.** A containerized Ollama does not inherit the host's GPU. NVIDIA
  hosts need the Container Toolkit plus a device reservation; AMD/ROCm hosts
  need the `ollama/ollama:rocm` image with `/dev/kfd` and `/dev/dri` passed
  through and the `video`/`render` groups added. On a CPU-only host the plain
  image is sufficient and no extra wiring is needed. Check which case you are
  in with `journalctl -u ollama | grep -i 'inference compute'` before you
  migrate.

## What not to do: `network_mode: host` on the stack's services

It looks like the obvious fix — put `backend` in the host's namespace and let
it reach `127.0.0.1` — and it does technically work, but it is the wrong lever:

- `network_mode: host` is **mutually exclusive with `ports:`**, which every
  published service in this stack uses.
- A host-mode service leaves the Compose network and loses its DNS entry. nginx
  resolves the backend by name (`proxy_pass http://$backend_soliplex:8000`), and
  Gitea reaches Postgres at `postgres:5432`; both break as soon as their target
  moves.

Fixing the cascade means moving nginx, `backend`, and `haiku-ingester` all into
host mode, rewriting every service name to `127.0.0.1`, deleting the `ports:`
blocks, and managing port collisions directly on the host. Host mode belongs on
the one-line proxy service (Option 1), not on the application services.

## Verify

The authoritative check runs against the *resolved* installation, so it covers
every model named in every referenced YAML file — not just the ones set during
generation:

```bash
docker compose run --rm backend \
    /app/.venv/bin/soliplex-cli audit ollama --check-responsive /environment
```

It reports `reachable` / `MISSING` / `OK` per configured URL and exits non-zero
on failure.

!!! warning "`--check-responsive` flags the embedding model"
    The responsiveness probe sends a chat-completion request to every
    configured model, including `rag_embed_model`. An embedding model
    correctly rejects that with `400 Bad Request`, so it is reported
    `UNRESPONSIVE` and the command exits non-zero even when the model is
    healthy. Read the chat-model results as valid, and check an embedding
    model by hand against `/v1/embeddings` instead. Tracked in
    [soliplex#1356](https://github.com/soliplex/soliplex/issues/1356).

Pull anything it reports missing:

```bash
docker compose run --rm backend \
    /app/.venv/bin/soliplex-cli ollama pull /environment   # -n to preview
```

To test the transport by itself, bypassing the installation config:

```bash
docker compose exec backend \
    curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://172.17.0.1:11434/api/tags
```

`HTTP 200` means the address and the `Host` header are both acceptable. `403`
means the URL uses a hostname — see
[Address Ollama by IP](#address-ollama-by-ip-never-by-hostname). A connection
failure means the relay or bind address is wrong.

!!! note "Restart after editing `.env`"
    `OLLAMA_BASE_URL` is read into the container environment at creation, so
    `docker compose up -d backend haiku-ingester` is needed to pick up a change.
    Confirm with `docker compose exec backend printenv OLLAMA_BASE_URL`.

## A note on model sizes

Reachability is not the only thing that makes a local Ollama feel broken. A
large chat model on a CPU-only host answers at low single-digit tokens per
second, and embedding a corpus for RAG slows to a crawl. If the stack responds
but does so very slowly, the model choice matters more than the networking —
see [Configuration](../architecture/configuration.md) for where the model names
are set.
