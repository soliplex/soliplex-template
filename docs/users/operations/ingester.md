---
icon: lucide/shield
---

# Ingester control plane

<!-- site-only -->
!!! note "About this page"
    This documents a stack **generated from `soliplex-template`**. A generated
    project ships its own copy of this page without this note.
<!-- endsite-only -->

`haiku-ingester` binds its control plane on `0.0.0.0:8765` so the host port
mapping works. With a non-loopback bind, haiku.rag **requires a bearer token**
(`ingester.api.auth_token`); otherwise anyone who can reach the port could
cancel jobs, retry from the dead-letter queue, and trigger source refreshes.

## How the token gets in

1. `haiku.rag/haiku.rag.yaml` ships
   `ingester.api.auth_token: ${INGESTER_TOKEN}`. haiku.rag expands `${VAR}`
   references when it loads the file, so the ingester reads the
   bind-mounted config directly.
2. `INGESTER_TOKEN` defaults to `secret` — Compose sets
   `${INGESTER_TOKEN:-secret}`. Override it in `.env`
   for anything that isn't a single-developer laptop.
3. An unset or empty value is a load error, so a missing token stops the
   ingester at startup instead of leaving the control plane unauthenticated.

## Calling the API

Clients send `Authorization: Bearer $INGESTER_TOKEN`:

```bash
curl -fsS -H "Authorization: Bearer $INGESTER_TOKEN" \
  http://localhost:8765/stats
```

The browser dashboard at `/` is unauthenticated HTML; its in-page JavaScript
attaches the bearer to its JSON fetches itself.

## Choosing a token

haiku.rag expands the reference itself, so the token no longer has to dodge
`sed` metacharacters. Prefer alphanumerics anyway, which keeps a literal `$`
out of `.env` where Compose would try to interpolate it:

```bash
openssl rand -hex 32
```
