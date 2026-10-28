---
icon: lucide/shield
---

# Ingester control plane

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

`haiku-ingester` binds its control plane on `0.0.0.0:8765`, reachable
only from the stack network: nginx is its public face at `/ingester/`,
and the control plane's host port is unpublished unless
`ingester_port` was set at generation. With a non-loopback bind,
haiku.rag **requires a bearer token** (`ingester.api.auth_token`);
otherwise anyone who can reach the port could cancel jobs, retry from
the dead-letter queue, and trigger source refreshes.

## How the token gets in

1. The service interpolates the `${INGESTER_TOKEN}` value
   into `haiku.rag/haiku.rag.yaml` **from the environment, not files**.
   Each service exports the value from the secret file into the environment
   before the config loads.

2. `nginx` does not read the token from the environment at all.
   Its command reads the value from the secret file and renders it via `sed`
   into a writable copy of `/etc/nginx.conf` at `/tmp` at container start.

3. The proxy also gates `/ingester/` on the backend's admin check
   via an `auth_request` against `/_authz_check`:
   signed-in admins get through, anonymous requests are redirected to
   sign-in, and non-admins get the stack's error page.

## Reaching it

**Via nginx** — the normal route. In a no-auth stack open
<http://localhost:${nginx_http}/ingester/> and you're done (the token is
injected at the proxy). In an auth-mode stack open
<https://${server_name}:${nginx_https}/ingester/>:
not signed in → sign-in page; signed in as an admin → the dashboard; signed
in as a non-admin → the stack's "access denied" page.

<!-- ifp:ingester_port -->
**Direct** (the stack's `${ingester_port}` mapping) — for scripts and
debugging; pass the token yourself:

```bash
curl -fsS -H "Authorization: Bearer $(cat .secrets/ingester_bearer.gen)" \
  http://localhost:${ingester_port}/stats
```

<!-- endif -->

## Choosing a token

Tokens are generated as 32 alphanumeric characters by
`scripts/generate_secrets.py` when `ingester_bearer.gen` is missing -- the
script never rewrites an existing file. Rotate the token with
`uv run scripts/generate_secrets.py --rotate ingester_bearer`, then
restart `haiku-ingester` and `nginx` (the token is rendered into nginx's
running config).
