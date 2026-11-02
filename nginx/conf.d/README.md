# nginx fragments

Both nginx servers (HTTP and HTTPS) include every `*.conf` file in this
directory, which is mounted read-only at `/etc/nginx/conf.d/`. Other files,
such as this README, are ignored.

The skill's scripts add and remove fragments here as parts of the stack come
and go, e.g. the route for an added service. Each fragment is included
inside both `server` blocks, so it may hold `location` blocks and
server-level directives, but not `http`-level ones such as `map`.

The sign-in provider for the `/ingester/` gate defaults to the value set
in `nginx.conf`. A fragment can override it:

```nginx
set $soliplex_signin_provider "<auth_systems id>";
```

nginx reads its configuration only at start. After adding, changing or
removing a fragment, restart it:

```bash
docker compose restart nginx
```
