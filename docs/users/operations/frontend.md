---
icon: lucide/app-window
---

# Web frontend and nginx

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

The `nginx` service is the stack's only public entry point. It serves the
Soliplex web client (a Flutter app, built ahead of time) and reverse-proxies
the backend and the other services behind it. This page covers:

- how its image is built
- how the web client is served
- what each part of `nginx/nginx.conf` controls
- how to make sure a rebuild actually delivers the release you expect

## How the image is built

`nginx/Dockerfile` has two stages:

1. **The web client.** It starts `FROM ghcr.io/soliplex/frontend-web:${frontend_version}`.
   soliplex/frontend publishes this image, and it holds nothing but the
   compiled client under `/build/web`.
2. **The server.** It starts `FROM debian:trixie`, then:
    - installs nginx from Debian's packages, together with the
      `headers-more`, `ndk` and `lua` modules
    - generates a self-signed TLS certificate with `openssl`, whose subject
      is the `tls_subject` generation parameter (`CN=${server_name}` by
      default)
    - copies the client from the first stage to `/app/build/web`
    - creates the runtime user from `PUID`/`PGID`, so the bind mounts and
      Docker secrets are readable without a uid mismatch

The image holds no nginx configuration of its own. `docker-compose.yml`
bind-mounts it read-only from the stack:

| Stack path | In the container |
|---|---|
| `nginx/nginx.conf` | `/etc/nginx/nginx.conf` |
| `nginx/mime.types` | `/etc/nginx/mime.types` |
| `nginx/conf.d/` | `/etc/nginx/conf.d/` |
| `nginx/error-pages/` | `/app/error-pages/` |

So a configuration change needs only a restart, not a rebuild (see
[Applying a change](#applying-a-change)).

## The web client

The client in `/app/build/web` is laid out like this:

- `index.html`: the page the browser loads first; it points at the release's
  directory
- `v<release>/`: everything else the client loads (`main.dart.js`,
  `flutter_bootstrap.js`, splash images, `version.json`), under a directory
  named for the release tag with its build number (e.g. `v0.117.0+106/`)
- `favicon.png`, `icons/`, `manifest.json`: the root-level assets a browser
  asks for by fixed name
- `.release-hash`: the release tag, as one line (e.g. `v0.117.0+106`)

The client is served at `/` (its `<base href>`) and calls the backend on the
same origin, under `/api/`, which nginx proxies to `backend`. It has no runtime
configuration file. Its release, branding and behavior come from the image tag
alone.

### Picking the release

The `FROM` tag in `nginx/Dockerfile` picks the release:

| Tag form | Example | Behavior |
|---|---|---|
| `<major>.<minor>` | `0.117` | Moving: follows the series' patch releases on `docker compose build --pull nginx` |
| `<major>.<minor>.<patch>` | `0.117.0` | Exact: pins one release |
| `<major>.<minor>.<patch>-<build>` | `0.117.0-106` | Exact: pins one build of a release |

Client and backend releases are not independent. A newer client can need
backend features (the 0.117 client's thread labels and thread search need
soliplex 0.92 or later). When you change the tag, check the client's changelog
against the soliplex release in `backend/Dockerfile`, and move the two
together when it asks for that.

### Caching in the browser

`nginx.conf` sets the caching so that a new release reaches browsers on their
next page load:

| Path | Cache policy | Why |
|---|---|---|
| `/index.html` | never cached (`no-cache, no-store, must-revalidate`) | It names the release's directory, so the browser must always fetch it fresh |
| `/v<release>/…` | one year, `immutable` | A new release lives at new URLs, so cached files can never be stale |
| `/favicon.png`, `/manifest.json`, `/icons/…` | one day | Fixed names, rarely changed |

The client's service worker unregisters itself and caches nothing, so a reload
is all it takes: no cache-clearing. A tab left open keeps running the release
it loaded until it is reloaded.

## How nginx starts

The `nginx` service's `command` in `docker-compose.yml` does not run nginx on
the mounted file directly. nginx can read neither environment variables nor
secret files inside its configuration, so the command first writes a copy to
`/tmp/nginx.conf`, substituting two placeholders:

| Placeholder | Replaced with |
|---|---|
| `@@RESOLVER@@` | `NGINX_RESOLVER` (default `127.0.0.11`, Docker's embedded DNS) |
| `@@INGESTER_BEARER@@` | the contents of the `ingester_bearer` Docker secret |

It then runs `nginx -c /tmp/nginx.conf`. The command exits with an error if
the bearer secret is missing or empty, or if `NGINX_RESOLVER` is empty.

Under podman, set `NGINX_RESOLVER` in `.env` to the network's DNS address (the
`nameserver` line of the container's `/etc/resolv.conf`, e.g. `10.89.0.1`).

## Configuration reference

`nginx/nginx.conf` holds two `server` blocks, which mirror each other:

- **HTTP**: port 9000 in the container, published on host port
  ${nginx_http}
- **HTTPS**: port 9443 in the container, published on host port
  ${nginx_https}, with the self-signed certificate

Both answer to `server_name ${server_name}`. A change to one usually belongs in
the other too.

### Routes

| Path | Goes to |
|---|---|
| `/` | the web client; paths that match no file fall back to `index.html` |
| `/api/`, `/mcp/` | `backend:8000` |
| `/ingester/` | `haiku-ingester:8765`, gated to admins (see [Ingester control plane](ingester.md)) |
| `/error-pages/` | internal only: the pages nginx serves for its own errors |

Other routes depend on what the stack includes:

<!-- if:tui -->
- `/tui/` goes to the terminal UI (`tui:8002`), websockets included.
<!-- endif -->
<!-- if:gitea -->
- `/gitea/` goes to Gitea (`gitea:3000`), on the HTTPS server only, with
  a `512M` request body limit for pushes and uploads.
<!-- endif -->
- `/authelia/` goes to the in-stack Authelia provider once it is installed;
  its route comes from a `conf.d/` fragment (see [Fragments](#fragments)).

Every upstream is named through a variable (`set $backend_soliplex "backend";`
and so on), which makes nginx look the name up through the `resolver` at
request time, cached for 10 seconds. A restarted container with a new IP
address is found again without restarting nginx.

### Protocols and TLS

- Both servers speak HTTP/1.1, and nginx talks HTTP/1.1 to every upstream.
  HTTP/2 is not enabled: neither `listen` line carries `http2`, and there is
  no `http2 on;` directive. A fragment holding `http2 on;` would turn it on in
  both servers (see [Fragments](#fragments)), so leave it out unless you need
  it.
- The HTTPS server accepts TLS 1.2 and 1.3 only, with ECDHE/AES-GCM ciphers
  and session tickets off.
- The certificate is the self-signed one generated at build time. An uncached
  rebuild generates a new one, so browsers ask to trust it again.

### Running behind another proxy

When a proxy in front of the stack terminates TLS, nginx reads the
`X-Forwarded-Proto` and `X-Forwarded-Port` headers it sets:

- They pass through to the backend as `X-Forwarded-*`. When they are missing,
  nginx uses its own scheme and port, so the backend builds correct absolute
  URLs either way.
- On the HTTP server, a request carrying `X-Forwarded-Proto: http` is
  redirected (301) to `https://` on the same host and port.
- A request straight to the HTTP port, without the header, is not redirected.
  Redirecting plain HTTP is left to the proxy in front.

### Limits

| Setting | Value | Scope |
|---|---|---|
| `client_max_body_size` | `10m` | each request body (uploads included) |
| `limit_req zone=api` | 50 requests/s per client IP, bursts to 100 | `/api/`, `/mcp/`, `/ingester/` |
| `limit_conn conn` | 10 concurrent connections per client IP | each server |
| `limit_rate` | `1024k` per connection | responses |
| `proxy_read_timeout` | `600` seconds | `/api/`, `/mcp/`, `/ingester/` (long agent runs and streams) |

A request over a rate or connection limit gets a 429 response.

`proxy_buffering` is off on the proxied routes, so the backend's streamed
responses reach the browser as they are produced.

### Cookies

On the HTTPS server, cookies set by proxied responses are rewritten to
`HttpOnly; Secure; SameSite=None`, which federated sign-in flows that POST
across sites need. Soliplex's own session cookie, `soliplex_authn`, is
exempt: it keeps the `SameSite` the backend gives it, so other sites cannot
send it.

### Errors and headers

- nginx's own errors (not the backend's) are served from
  `nginx/error-pages/`: `400.html`, `403.html` (also used for 429),
  `404.html` and `50x.html`. Edit them in place.
- When the backend or ingester is unreachable, `/api/`, `/mcp/` and
  `/ingester/` answer with a JSON 503, and other paths get `50x.html`. Error
  responses the backend sends itself pass through untouched.
- The `Server` header is removed, and so are the upstreams' `X-Powered-By`,
  `Server`, `X-AspNet-Version` and `X-Runtime`.
- Requests for a list of file types the client never uses (executables,
  archives, media and the like) get a 403.

### Fragments

Both servers `include /etc/nginx/conf.d/*.conf`, the stack's `nginx/conf.d/`
directory, so a fragment can hold `location` blocks and server-level
directives, though not `http`-level ones such as `map`. The skill's scripts
manage fragments as parts of the stack come and go:

- `signin-provider.conf` names the OIDC provider the `/ingester/` gate signs
  in with (written by `oidc_providers.py`)
- `authelia.conf` routes `/authelia/` (written by `install_authelia.py`)

A fragment of your own is fine too. Name it for what it does, and remember
that it applies to both servers.

## Applying a change

What a change needs depends on where it lives:

| You changed | Then run |
|---|---|
| `nginx/nginx.conf`, `nginx/mime.types`, a `conf.d/` fragment | `docker compose restart nginx` |
| `nginx/error-pages/` | nothing: they are read on each request |
| `NGINX_RESOLVER` in `.env` | `docker compose up -d nginx` (recreates the container) |
| the `FROM` tag in `nginx/Dockerfile`, `PUID`/`PGID` | a rebuild: see [Making a rebuild reach the expected version](#making-a-rebuild-reach-the-expected-version) |

Before restarting, check the configuration in the running container. The
config is rendered at start, so render a test copy the same way the service
does:

```bash
docker compose exec nginx sh -c \
  'sed -e "s|@@INGESTER_BEARER@@|x|g" -e "s|@@RESOLVER@@|$NGINX_RESOLVER|g" \
     /etc/nginx/nginx.conf > /tmp/nginx-test.conf &&
   nginx -t -c /tmp/nginx-test.conf'
```

A failed check leaves the running nginx alone. A restart with a broken
configuration takes the whole stack offline until it is fixed.

## Making a rebuild reach the expected version

The image combines three things, and each one is refreshed differently:

| Part | Comes from | Refreshed by |
|---|---|---|
| The web client | the `frontend-web` image tag | `--pull`, which fetches the tag's current image |
| nginx itself | Debian's package archive, at build time | an uncached build, `--no-cache` |
| The configuration | the stack's files, bind-mounted | a restart (see above) |

The catch is Docker's build cache. `--pull` refreshes the two base images, but
the `apt-get install` step is cached on its own command line. It reruns only
when the `debian:trixie` base it builds on has changed. Otherwise the image
keeps whatever nginx package the first build installed, however long ago that
was, even when Debian has published a fix since.

To be sure a rebuild picks up both the newest client in the tag's series and
Debian's newest nginx package, skip the cache, then recreate the container:

```bash
docker compose build --pull --no-cache nginx
docker compose up -d nginx
```

`up -d` sees the new image and replaces the running container. A plain
`restart` would keep the old one.

### Checking what is running

The client release, from the container and as browsers see it:

```bash
docker compose exec nginx cat /app/build/web/.release-hash
curl -s http://localhost:${nginx_http}/ | grep -o 'v[^"]*/flutter_bootstrap.js'
```

Both name the same release (`v0.117.0+106`, for instance). The `frontend-web`
image also records it, in its `org.opencontainers.image.version` label:

```bash
docker image inspect ghcr.io/soliplex/frontend-web:${frontend_version} \
  --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'
```

The nginx package installed in the image:

```bash
docker compose exec nginx dpkg-query -W nginx
```

And the newest one Debian offers right now, to compare against:

```bash
docker run --rm debian:trixie sh -c \
  'apt-get update -qq && apt-cache policy nginx' | grep Candidate
```

If the installed version is older than the candidate, the build reused a
cached layer: rebuild with `--no-cache` as above. The candidate comes from
the `debian:trixie` image's own package sources, which include Debian's
security archive, so it already reflects security updates.
