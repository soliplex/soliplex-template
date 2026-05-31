---
icon: lucide/shield
---

# Ingester control plane

`haiku-ingester` binds its control plane on `0.0.0.0:8765` so the host port
mapping works. With a non-loopback bind, haiku.rag **requires a bearer token**
(`ingester.api.auth_token`); otherwise anyone who can reach the port could
cancel jobs, retry from the dead-letter queue, and trigger source refreshes.

<%text>## How the token gets in</%text>

1. `haiku.rag/haiku.rag.yaml` ships
   `ingester.api.auth_token: __INGESTER_TOKEN__` as a placeholder.
2. The `haiku-ingester` service runs a small `sh -c "sed ... && exec ..."`
   wrapper that replaces the placeholder with the value of `$INGESTER_TOKEN`
   before haiku-ingester reads the config.
3. `INGESTER_TOKEN` defaults to `secret` — Compose sets
   `<%text>${INGESTER_TOKEN:-secret}</%text>`. Override it in `.env`
   for anything that isn't a single-developer laptop.

<%text>## Calling the API</%text>

Clients send `Authorization: Bearer $INGESTER_TOKEN`:

```bash
curl -fsS -H "Authorization: Bearer $INGESTER_TOKEN" \
  http://localhost:${ingester_port}/stats
```

The browser dashboard at `/` is unauthenticated HTML; its in-page JavaScript
attaches the bearer to its JSON fetches itself.

!!! warning "Watch the startup log"
    The startup log warns if `auth_token` is `None`. If you see that
    warning, the substitution didn't fire and the API is **open**.

<%text>## Token character restrictions</%text>

The token cannot contain `|`, `\`, or `&` — they are the `sed` delimiter and
escape characters used by the substitution wrapper. Use alphanumerics, e.g.:

```bash
openssl rand -hex 32
```
