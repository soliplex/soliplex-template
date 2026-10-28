# Serve through nginx

Part of [Upgrading an existing stack](index.md).

**Walk this rung with the `template-skill-2026.10.28-aadeb80` skill release** —
its generator produces the rung's end state (only nginx published by default;
the ingester control plane served behind nginx at `/ingester/`; its bearer
token a file-backed Docker secret). The stack being upgraded names the
release that generated it in its own `[tool.soliplex-template]` manifest — use
that one when climbing *from* an older shape.

```bash
uv run <skill>/scripts/skill_versions.py downgrade template-skill-2026.10.28-aadeb80
```

This rung bumps no releases — soliplex, `haiku.rag`-slim, docling-serve and
the frontend all stay as the stack has them, and it touches no databases or
RAG stores, so none of the migration machinery applies. What changes:

- the edge: by default only nginx is published to the host; every other
  service's host port becomes opt-in via a `*_port` parameter;
- the ingester control plane: always served behind nginx at `/ingester/`,
  its bearer token injected by the proxy from the new file-backed Docker
  secret `ingester_bearer` — never an environment variable — and, in
  auth-mode stacks, gated on soliplex admin auth;
- nginx keeps the `soliplex_authn` cookie's `SameSite=Lax` under proxied
  rewrites of other cookies;
- the `.secrets`/`.env` provisioning scripts gain rotation and recovery
  features.

Skip the steps that do not apply.

1. **Stop the stack** with `docker compose down`. This rung migrates
   nothing, so backing up first is optional; the stack stays down until the
   last step.

2. **Fetch the updated stack scripts.** `generate_secrets.py` (required:
   it provisions the new `ingester_bearer` secret, see step 10) and
   `generate_env.py` (optional: recreates a fresh clone's missing `.env`
   from the manifest, see [Installation](../users/installation.md)). Fetch
   them into the stack's `scripts/` directory, the way
   [Before you start](index.md#before-you-start) describes:

    ```bash
    for s in generate_secrets generate_env; do
        curl -fsSL -o "scripts/$s.py" \
            "https://raw.githubusercontent.com/soliplex/soliplex-template/main/scripts/$s.py"
    done
    ```

    Both are PEP 723 shims over the `soliplex-template` package, which
    `uv run` provisions on first use: they need soliplex-template **0.19**
    (or later) published on PyPI, which is the release this rung ships with.

3. **Exempt the authn cookie from the cookie-flags rewrite**, in
   `nginx/nginx.conf` — above the catch-all

    ```nginx
        proxy_cookie_flags ~ HttpOnly Secure SameSite=None;
    ```

    (there is normally one, in the TLS server block; check both blocks if
    the stack customized them), add the no-op flag line for the soliplex
    authn cookie:

    ```nginx
        # SameSite=None is required for federation flows, but must not apply
        # to the soliplex authn cookie: rewriting it to SameSite=None would
        # let other origins carry the session cookie. nginx applies the
        # first matching directive, and this one lists a flag the backend
        # already sets, so the catch-all below never runs for it.
        proxy_cookie_flags soliplex_authn httponly;
        proxy_cookie_flags ~ HttpOnly Secure SameSite=None;
    ```

4. **Give the nginx service the ingester bearer and a rendered config.**
   In `docker-compose.yml`, in the `nginx` service:

    add `haiku-ingester` to `depends_on` (nginx now routes to it, and
    must not start ahead of it):

    ```yaml
        depends_on:
          - backend
          - haiku-ingester
    ```

    (keep the stack's existing `tui` / `gitea` lines below it), and
    mount the new secret onto the service and swap the plain
    `command` for the wrapper that fails loudly on a missing token and
    renders it into a copy of the config (nginx cannot read a secret
    file inside `location` blocks, nor expand environment references in
    its configuration). Add both below `volumes:`, as the template has
    them:

    ```yaml
        secrets:
          - ingester_bearer

        #
        # '$$' is Compose's escape for a literal '$': the container's shell
        # sees '$(cat ...)'.
        #
        # Tokens are generated as [A-Za-z0-9]{32} (scripts/generate_secrets.py),
        # so the sed below cannot trip on metacharacters in its replacement.
        command:
          - sh
          - -ec
          - |
            # Fail loudly on a missing or empty token.
            test -s /run/secrets/ingester_bearer || {
                echo "error: /run/secrets/ingester_bearer missing or empty; run 'uv run scripts/generate_secrets.py'" >&2
                exit 1
            }
            # nginx cannot read a secret file inside 'location' blocks, nor
            # environment references in its configuration: substitute the
            # ingester bearer into a copy of the config instead.
            sed "s|@@INGESTER_BEARER@@|$$(cat /run/secrets/ingester_bearer)|g" /etc/nginx/nginx.conf > /tmp/nginx.conf
            exec nginx -c /tmp/nginx.conf -g 'daemon off;'
    ```

5. **Point the ingester service at the shared bearer.** In the
   `haiku-ingester` service:

    replace its `command:` with the block below — it adds the
    `INGESTER_TOKEN` export (the shared `haiku.rag.yaml` interpolates
    the token, so the ingester needs it in the environment) and the
    `--root-path /ingester` serve flag that mounts the API and the
    dashboard's `<base href>` under the public prefix, so nginx's
    `/ingester/` locations (step 8) can strip it before proxying:

    ```yaml
        command:
          - sh
          - -ec
          - |
            # haiku.rag interpolates environment references in its own config
            # (0.57+), so the ingester reads the bind-mounted yaml directly -- no
            # rendered copy. Pending a possible direct read (ggozad/haiku-rag#749),
            # read secrets files into the environment variables expected by its
            # interpolation.
            # - queue password:
            export INGESTER_DB_PASSWORD="$$(cat /run/secrets/ingester_db_password)"
            # - control plane bearer token:
            export INGESTER_TOKEN="$$(cat /run/secrets/ingester_bearer)"
            # The queue role owns its schema; prepare it before serving.
            haiku-ingester --config /app/haiku.rag.yaml queue migrate
            # '--root-path /ingester' mounts the API (and the dashboard's
            # <base href>) under the public prefix so nginx's '/ingester/'
            # locations strip it before proxying.
            exec haiku-ingester --config /app/haiku.rag.yaml serve --root-path /ingester
    ```

    delete the `INGESTER_TOKEN` entry from its `environment:` block
    (and its three comment lines) — the token is a secret file now, not
    an environment variable:

    ```yaml
        # delete these three comment lines and the entry:
        #  # Bearer token clients must send to /jobs, /sources, /dlq, etc.
        #  # Override in '.env'; the default 'secret' is intentionally weak
        #  # so unhardened deployments fail an obvious security review.
        #  INGESTER_TOKEN: ${INGESTER_TOKEN:-secret}
    ```

    add the secret to its `secrets:` list:

    ```yaml
        secrets:
          - ingester_db_password
          - ingester_bearer
    ```

6. **Give the one-shot CLI the bearer too.** In the `haiku-rag` service,
   the shared `haiku.rag.yaml` interpolates `INGESTER_TOKEN`, so its
   entrypoint must export it as well — and it takes the second secret on
   its `secrets:` list:

    ```yaml
        entrypoint:
          - sh
          - -c
          - |
            export INGESTER_DB_PASSWORD="$$(cat /run/secrets/ingester_db_password)"
            # The shared haiku.rag.yaml interpolates INGESTER_TOKEN as well (it
            # describes the 'haiku-ingester' service's API), so the CLI needs it
            # in the environment even though it never calls the API itself.
            export INGESTER_TOKEN="$$(cat /run/secrets/ingester_bearer)"
            exec haiku-rag --config /app/haiku.rag.yaml "$$@"
          - haiku-rag

        secrets:
          - ingester_db_password
          - ingester_bearer
    ```

7. **Declare the secret** at the top level of `docker-compose.yml`, beside
   the other `*.gen` files:

    ```yaml
        secrets:
            ingester_db_password:
                file: ./.secrets/ingester_db_password.gen
            ingester_bearer:
                file: ./.secrets/ingester_bearer.gen
            postgres_password:
                file: ./.secrets/postgres_password.gen
            # ... the stack's remaining secrets unchanged
    ```

8. **Route `/ingester/` through nginx**, in `nginx/nginx.conf`. In **each**
   `server {` block (both `listen 9000` and `listen 9443`):

    teach the backend-down handler the new prefix, so control-plane
    outages get the JSON error for API clients — add `|ingester` to
    the handler's `if` regex:

    ```nginx
        location @backend_down {
            default_type application/json;
            if ($uri ~ ^/(api|mcp|ingester)/) {
                return 503 '{"error": "service_unavailable", "message": "Backend is temporarily unavailable"}';
            }
            # ...
        }
    ```

    Then add the two routing locations (between the versioned assets
    location and the `/tui` redirect, as the template has them):

    ```nginx
        # Bare /ingester -> the dashboard and <base href> live under the
        # trailing slash.
        location = /ingester {
            return 301 /ingester/;
        }

        location /ingester/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            error_page 403 /error-pages/403.html;
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver
            # directive.
            set $backend_ingester "haiku-ingester";
            rewrite ^/ingester/?(.*)$ /$1 break;

            proxy_pass http://$backend_ingester:8765;

            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto $backend_proto;
            proxy_set_header Authorization "Bearer @@INGESTER_BEARER@@";
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }
    ```

    (`X-Forwarded-Proto` spells `$backend_proto` in the HTTP server and
    the literal `https` in the TLS one, matching how the block already
    proxies `/api/`.)

    Stacks whose backend runs **with** auth — no `--no-auth-mode` in the
    `backend` service's `command:` — also gate the surface on soliplex
    admin auth, in both server blocks and once at `http` scope:

    ```nginx
    # at http scope, above the first server block:
    # '/ingester/' gate: a 401 redirects the browser's top-level navigations
    # to sign-in, while the dashboard's own polling fetch()es must get a
    # plain 401. Any 'Sec-Fetch-Mode: navigate' request is a browser
    # navigation.
    map $http_sec_fetch_mode $soliplex_navigate {
        default 0;
        navigate 1;
    }
    ```

    ```nginx
    # inside each server block, beside the other named locations:
    # Named location for the '/ingester/' gate's 401: navigations to
    # sign-in, everything else a plain JSON 401 (see the map above).
    # '$request_uri' is still the original request's URI, so the
    # sign-in flow returns the browser to the page it wanted.
    location @ingester_signin {
        if ($soliplex_navigate) {
            # delivery=cookie makes the login set the authn cookie the
            # check (and its callers) read; the provider id matches the
            # auth_systems entry in 'oidc/config.yaml' -- keep the two in
            # step when editing that config.
            return 302 "/api/login/keycloak?delivery=cookie&return_to=$request_uri";
        }
        return 401 '{"error": "signin_required", "message": "Sign in with an admin account to use the ingester control plane"}';
    }

    # Internal auth_request target for the '/ingester/' gate:
    # - 204 lets the request proceed
    # - 401 lands in @ingester_signin
    # - 403 gets the stack's error
    location = /_authz_check {
        internal;
        set $backend_authz "backend";
        proxy_pass http://$backend_authz:8000/api/v1/authz/check/ingester;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
    }
    ```

    With those in place, the gate itself goes into the `location
    /ingester/` routing block added above, in two spots, in both
    server blocks. Above the `limit_req zone=api burst=100 nodelay;`
    line (directly after the existing `error_page 403 ...` line):

    ```nginx
            # The gate (auth-mode stacks): an admin's authn cookie passes
            # the check; anything else 401s into @ingester_signin (401) or
            # the stack's 403 page. In no-auth stacks the backend check
            # would answer 204 for everyone, so the gate is omitted.
            auth_request /_authz_check;
            auth_request_set $soliplex_user $upstream_http_x_soliplex_user;
            error_page 401 = @ingester_signin;
    ```

    and directly above the `proxy_set_header Authorization "Bearer ..."
    line:

    ```nginx
            # Set by the auth_request above ('X-Soliplex-User'); names the
            # caller for the control plane's logs.
            proxy_set_header X-Soliplex-User $soliplex_user;
    ```

    (the provider id `keycloak` in the sign-in redirect matches the
    `auth_systems` entry in the stack's
    `backend/environment/oidc/config.yaml` — use the stack's own id there.)

9. **Decide which host ports stay published.** The current template
   publishes only nginx (`9000`, `9443`): every other service is opt-in via
   a `*_port` parameter, recorded in the manifest's
   `[tool.soliplex-template.params]` table and baked into the compose file
   as a literal host port at generation time. Nothing ports-related lives
   in `.env`. For each service with a published port today, either:

    - **Adopt the default and unpublish it** — delete its `ports:` block
      from `docker-compose.yml` (`8000:8000` on `backend`, `8765:8765` on
      `haiku-ingester`, `5001:5001` on `docling-serve`, `5432:5432` on
      `postgres`, `3000:3000` / `2222:2222` on `gitea`), and delete the
      matching keys from the manifest (`ingester_port`, `docling_port`,
      `postgres_port`, plus `backend_port`, `gitea_http_port`,
      `gitea_ssh_port` where present).

    - **Keep it published** — the compose mapping is already a literal
      host port and needs no change; just make the manifest agree: keep
      `ingester_port`, `docling_port`, `postgres_port`; add
      `backend_port = 8000` if the backend stays directly reachable, and
      `gitea_http_port = 3000` / `gitea_ssh_port = 2222` in gitea stacks.
      (`gitea_ssh_port` is the only way to keep git-over-SSH clones from
      the host working; web use needs nothing — nginx serves the forge at
      `/gitea/`.)

    Per-service choices are fine. A stack which keeps the ingester or
    backend direct still gets the new nginx route: the control plane is
    served either way, and the `/ingester/` route is the
    browser-dashboard path.

10. **Provision the bearer, then bring the stack up.** With the updated
    `generate_secrets.py` from step 2:

    ```bash
    uv run scripts/generate_secrets.py
    ```

    It provisions only the secrets whose `.gen` files are missing — this
    rung's only new one is `.secrets/ingester_bearer.gen` (32
    alphanumeric characters, the spelling the nginx wrapper's `sed`
    expects) — leaving every existing secret untouched. If `.env` carries
    an `INGESTER_TOKEN` line (older stacks could set it as an override),
    delete it: the token is a secret file now and the compose no longer
    reads it from the environment.

    Then rebuild nothing (the nginx config is bind-mounted and the scripts
    are not baked into any image) and start the stack:

    ```bash
    docker compose up -d
    docker compose logs nginx | head      # started clean: no 'sed' or bearer errors
    ```

    Open `http://localhost:9000/ingester/` — the dashboard loads, and in
    auth-mode stacks the browser first runs the admin sign-in. If the
    stack kept a direct published port, its bearer-authed API answers
    without nginx:

    ```bash
    curl -fsS -H "Authorization: Bearer $(cat .secrets/ingester_bearer.gen)" \
        http://localhost:8765/stats
    ```

    Then [**Check it**](index.md#check-it), and commit.

    Afterwards, refresh the stack's agent-facing docs from a reference
    stack as usual — this rung rewrites the ports paragraph, the
    "Ingester control plane auth" section and the `haiku-ingester` /
    `haiku-rag` service descriptions in `AGENTS.md`, and the corresponding
    pages under `docs/`.
