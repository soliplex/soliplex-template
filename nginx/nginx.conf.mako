load_module /usr/lib/nginx/modules/ngx_http_headers_more_filter_module.so;

worker_processes auto;
error_log stderr notice;
pid /tmp/nginx.pid;

events {
    worker_connections 1024;
}

http {
    client_body_temp_path /tmp/nginx_body_temp;
    proxy_temp_path /tmp/nginx_proxy_temp;
    fastcgi_temp_path /tmp/nginx_fastcgi_temp;
    uwsgi_temp_path /tmp/nginx_uwsgi_temp;
    scgi_temp_path /tmp/nginx_scgi_temp;

    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    dav_methods off;
    server_tokens off;
    more_clear_headers 'Server';

    # Hide backend technology headers from proxied responses
    proxy_hide_header X-Powered-By;
    proxy_hide_header Server;
    proxy_hide_header X-AspNet-Version;
    proxy_hide_header X-Runtime;


    # Use Docker's internal DNS resolver with short cache TTL
    # This prevents stale IP caching when containers restart
    resolver 127.0.0.11 valid=10s;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /dev/stdout main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    gzip on;
    gzip_proxied off;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    charset utf-8;
    charset_types text/plain text/css application/json application/javascript text/xml application/xml;

    # Rate limiting for API and MCP endpoints (50 req/s per client IP, burst to 100)
    limit_req_zone $binary_remote_addr zone=api:10m rate=50r/s;
    limit_req_status 429;

    # Concurrent connection limit (10 per client IP)
    limit_conn_zone $binary_remote_addr zone=conn:10m;
    limit_conn_status 429;

    map $http_x_forwarded_port $backend_port {

      # If x-forwarded-port is missing, use this server's port (we're contacting
      # this server directly, not through an upstream proxy)

      default $http_x_forwarded_port;
      ""      $server_port;
    }

    # If x-forwarded-proto is missing, use http (we're contacting this server
    # directly, not through an upstream proxy)

    map $http_x_forwarded_proto $backend_proto {
      # If x-forwarded-proto is missing, use http
      default $http_x_forwarded_proto;
      ""      "http";
    }

    # nginx only sends the host without the port as a header if you use
    # $host.  We need to tack on the port if it's not an https or http
    # request on the standard ports.

    map "$backend_proto:$backend_port" $host_port {
      "http:80"   "";
      "https:9443" "";
      default     :$backend_port;
    }

    # Map Upgrade header for websocket proxying. Sends 'Connection: upgrade'
    # for WS handshakes and 'Connection: close' for regular requests sharing
    # the same location (e.g. the textual-serve index and /static/ assets).
    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

% if not backend_auth_flag:
    # '/ingester/' gate: a 401 redirects the browser's top-level navigations
    # to sign-in, while the dashboard's own polling fetch()es must get a
    # plain 401. Any 'Sec-Fetch-Mode: navigate' request is a browser
    # navigation.
    map $http_sec_fetch_mode $soliplex_navigate {
        default 0;
        navigate 1;
    }
% endif

    server {
        listen 9000;
        server_name ${server_name};

        root /app/build/web;
        index index.html;
        client_max_body_size 10m;
        limit_conn conn 10;
        limit_rate 1024k;
        proxy_cookie_path / /;

        # Custom error pages for nginx-generated errors
        error_page 400 401 405 408 414 431 497 /error-pages/400.html;
        error_page 403 /error-pages/403.html;
        error_page 404 /error-pages/404.html;
        error_page 429 /error-pages/403.html;
        error_page 500 501 502 503 504 /error-pages/50x.html;

        # Internal location to serve error pages — not directly accessible by clients
        location /error-pages/ {
            internal;
            alias /app/error-pages/;
        }

        # Named location for backend-down errors on proxied requests.
        # Returns JSON for API/MCP clients and HTML for browser clients.
        location @backend_down {
            default_type application/json;
            if ($uri ~ ^/(api|mcp|ingester)/) {
                return 503 '{"error": "service_unavailable", "message": "Backend is temporarily unavailable"}';
            }
            root /app/error-pages;
            try_files /50x.html =503;
        }
% if not backend_auth_flag:
        # Named location for the '/ingester/' gate's 401: navigations to
        # sign-in, everything else a plain JSON 401 (see the map above).
        # '$request_uri' is still the original request's URI, so the
        # sign-in flow returns the browser to the page it wanted.
        location @ingester_signin {
            if ($soliplex_navigate) {
                # delivery=cookie makes the login set the authn cookie the
                # check (and its callers) read; the provider id matches the
                # auth_systems entry in 'oidc/config.yaml' -- keep the two
                # in step when editing that config.
                return 302 "/api/login/keycloak?delivery=cookie&return_to=$request_uri";
            }
            return 401 '{"error": "signin_required", "message": "Sign in with an admin account to use the ingester control plane"}';
        }
% endif

% if not backend_auth_flag:
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
% endif

        # Redirect HTTP to HTTPS when behind a proxy that sets
        # X-Forwarded-Proto.  Uses $http_host (not $host) to preserve the
        # original port if non-standard.
        # Note: direct access to port 9000 without X-Forwarded-Proto is not
        # redirected; the upstream proxy is expected to handle that case.
        if ($http_x_forwarded_proto = "http") {
            return 301 https://$http_host$request_uri;
        }

        # index.html should never be cached - it bootstraps the versioned assets
        location = /index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            add_header Pragma "no-cache";
            add_header Expires "0";
        }

        # Versioned assets in the release's hash directory - cache aggressively
        # (1 year, immutable).
        # The directory is named for the frontend release tag,
        # build metadata included (e.g., /v0.114.0+101/...), so each release's
        # assets live at new URLs and browsers fetch them afresh.
        location ~ "^/v[0-9]+\.[0-9]+(\.[0-9]+)?(\+[0-9A-Za-z.-]+)?/(.*)" {
            expires 1y;
            add_header Cache-Control "public, max-age=31536000, immutable";
        }

        # Root-level static assets (favicon, icons, manifest) - moderate caching
        location ~* ^/(favicon\.png|manifest\.json|icons/.*)$ {
            expires 1d;
            add_header Cache-Control "public, max-age=86400";
        }

        # Deny access to file types not required by the application
        location ~* \.(wmlc|cco|jardiff|jnlp|run|pl|pm|prc|pdb|rar|rpm|sea|swf|sit|tcl|tk|der|pem|crt|xpi|xspf|bin|exe|dll|deb|dmg|iso|img|msi|msp|msm|mid|midi|kar|mp3|ogg|m4a|ra|3gpp|3gp|ts|mp4|mpeg|mpg|ogv|mov|webm|flv|m4v|mkv|mng|asx|asf|wmv|avi)$ {
            return 403;
        }

        location / {
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            # Do not intercept backend error responses — let JSON pass through.
            # The @backend_down handler only fires for nginx-generated 502/503/504
            # (i.e. when the backend is unreachable).
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
            set $backend_soliplex "backend";
            proxy_pass http://$backend_soliplex:8000;
            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto $backend_proto;
            proxy_set_header X-Debug-Backend-Port $backend_port;
            proxy_set_header X-Debug-Backend-Proto $backend_proto;
            proxy_set_header X-Debug-Backend-Host-Port $host_port;
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        location /mcp/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
            set $backend_soliplex "backend";
            proxy_pass http://$backend_soliplex:8000;
            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto $backend_proto;
            proxy_set_header X-Debug-Backend-Port $backend_port;
            proxy_set_header X-Debug-Backend-Proto $backend_proto;
            proxy_set_header X-Debug-Backend-Host-Port $host_port;
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        # '@@INGESTER_BEARER@@' is rendered by the 'nginx' service command in
        # docker-compose.yml at container start.
        location = /ingester {
            return 301 /ingester/;
        }

        location /ingester/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            error_page 403 /error-pages/403.html;
% if not backend_auth_flag:
            # The gate (auth-mode stacks): an admin's authn cookie passes
            # the check; anything else 401s into @ingester_signin (401) or
            # the stack's 403 page. In no-auth stacks the backend check
            # would answer 204 for everyone, so the gate is omitted.
            auth_request /_authz_check;
            auth_request_set $soliplex_user $upstream_http_x_soliplex_user;
            error_page 401 = @ingester_signin;
% endif
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
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
% if not backend_auth_flag:
            # Set by the auth_request above ('X-Soliplex-User'); names the
            # caller for the control plane's logs.
            proxy_set_header X-Soliplex-User $soliplex_user;
% endif
            proxy_set_header Authorization "Bearer @@INGESTER_BEARER@@";
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        # Redirect bare /tui to /tui/ — textual-serve serves its index at '/'
        # and the public URL mapping requires the trailing slash.
        location = /tui {
            return 301 /tui/;
        }

        # Proxy textual-serve (soliplex TUI). Matches both HTTP index/static
        # requests and the websocket at /tui/ws.  Upstream is the 'tui' service
        # on port 8002; strip the /tui prefix so aiohttp's routes match.
        # NOTE: 'set' must precede 'rewrite ... break' — 'break' terminates the
        # rewrite module, so any 'set' after it would leave the variable unset.
        location /tui/ {
            set $backend_tui "tui";
            rewrite ^/tui/?(.*)$ /$1 break;

            proxy_pass http://$backend_tui:8002;

            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto $backend_proto;

            # Websocket sessions are long-lived and mostly idle.
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

    }

    # HTTPS server with self-signed certificate
    server {
        listen 9443 ssl;
        server_name ${server_name};

        ssl_certificate /etc/nginx/ssl/server.crt;
        ssl_certificate_key /etc/nginx/ssl/server.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_prefer_server_ciphers on;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_session_tickets off;

        root /app/build/web;
        index index.html;
        client_max_body_size 10m;
        limit_conn conn 10;
        limit_rate 1024k;
        # Cookie security flags for proxied responses.
        # SameSite=None is required for the CAC/ADFS federated login flow,
        # which involves cross-site POSTs that would block cookies under
        # Strict or Lax, but must not apply to the Soliplex authn cookie:
        # rewriting 'soliplex_authn' to SameSite=None would let other
        # origins carry the session cookie, defeating the authn gate for
        # proxied admin surfaces (issue #221).  nginx requires at least one
        # flag per directive and applies the first matching directive, so
        # the exemption lists a flag the backend already sets on the authn
        # cookie: the directive is a no-op for it, and the catch-all below
        # never runs for it.
        proxy_cookie_flags soliplex_authn httponly;
        proxy_cookie_flags ~ HttpOnly Secure SameSite=None;
        proxy_cookie_path / /;

        # Custom error pages for nginx-generated errors
        error_page 400 401 405 408 414 431 497 /error-pages/400.html;
        error_page 403 /error-pages/403.html;
        error_page 404 /error-pages/404.html;
        error_page 429 /error-pages/403.html;
        error_page 500 501 502 503 504 /error-pages/50x.html;

        # Internal location to serve error pages — not directly accessible by clients
        location /error-pages/ {
            internal;
            alias /app/error-pages/;
        }

% if not backend_auth_flag:
        location = /_authz_check {
            internal;
            set $backend_authz "backend";
            proxy_pass http://$backend_authz:8000/api/v1/authz/check/ingester;
            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
        }
% endif

        # Named location for backend-down errors on proxied requests.
        # Returns JSON for API/MCP clients and HTML for browser clients.
        location @backend_down {
            default_type application/json;
            if ($uri ~ ^/(api|mcp|ingester)/) {
                return 503 '{"error": "service_unavailable", "message": "Backend is temporarily unavailable"}';
            }
            root /app/error-pages;
            try_files /50x.html =503;
        }
% if not backend_auth_flag:
        location @ingester_signin {
            if ($soliplex_navigate) {
                # delivery=cookie makes the login set the authn cookie the
                # check (and its callers) read; the provider id matches the
                # auth_systems entry in 'oidc/config.yaml' -- keep the two
                # in step when editing that config.
                return 302 "/api/login/keycloak?delivery=cookie&return_to=$request_uri";
            }
            return 401 '{"error": "signin_required", "message": "Sign in with an admin account to use the ingester control plane"}';
        }
% endif

        # index.html should never be cached - it bootstraps the versioned assets
        location = /index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            add_header Pragma "no-cache";
            add_header Expires "0";
        }

        # Versioned assets in the release's hash directory - cache aggressively
        # (1 year, immutable). The directory is named for the frontend release tag,
        # build metadata included (e.g., /v0.114.0+101/...), so each release's assets
        # live at new URLs and browsers fetch them afresh.
        location ~ "^/v[0-9]+\.[0-9]+(\.[0-9]+)?(\+[0-9A-Za-z.-]+)?/(.*)" {
            expires 1y;
            add_header Cache-Control "public, max-age=31536000, immutable";
        }

        # Root-level static assets (favicon, icons, manifest) - moderate caching
        location ~* ^/(favicon\.png|manifest\.json|icons/.*)$ {
            expires 1d;
            add_header Cache-Control "public, max-age=86400";
        }

        # Deny access to file types not required by the application
        location ~* \.(wmlc|cco|jardiff|jnlp|run|pl|pm|prc|pdb|rar|rpm|sea|swf|sit|tcl|tk|der|pem|crt|xpi|xspf|bin|exe|dll|deb|dmg|iso|img|msi|msp|msm|mid|midi|kar|mp3|ogg|m4a|ra|3gpp|3gp|ts|mp4|mpeg|mpg|ogv|mov|webm|flv|m4v|mkv|mng|asx|asf|wmv|avi)$ {
            return 403;
        }

        location / {
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
            set $backend_soliplex "backend";
            proxy_pass http://$backend_soliplex:8000;
            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Debug-Backend-Port $backend_port;
            proxy_set_header X-Debug-Backend-Proto https;
            proxy_set_header X-Debug-Backend-Host-Port $host_port;
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        location /mcp/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
            set $backend_soliplex "backend";
            proxy_pass http://$backend_soliplex:8000;
            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Debug-Backend-Port $backend_port;
            proxy_set_header X-Debug-Backend-Proto https;
            proxy_set_header X-Debug-Backend-Host-Port $host_port;
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        # Bare /ingester → the dashboard and <base href> live under the
        # trailing slash.
        location = /ingester {
            return 301 /ingester/;
        }

        location /ingester/ {
            proxy_intercept_errors off;
            error_page 502 503 504 = @backend_down;
            error_page 403 /error-pages/403.html;
% if not backend_auth_flag:
            # The gate (auth-mode stacks): an admin's authn cookie passes
            # the check; anything else 401s into @ingester_signin (401) or
            # the stack's 403 page. In no-auth stacks the backend check
            # would answer 204 for everyone, so the gate is omitted.
            auth_request /_authz_check;
            auth_request_set $soliplex_user $upstream_http_x_soliplex_user;
            error_page 401 = @ingester_signin;
% endif
            limit_req zone=api burst=100 nodelay;

            # Use variable to force DNS re-resolution via the resolver directive
            set $backend_ingester "haiku-ingester";
            rewrite ^/ingester/?(.*)$ /$1 break;

            proxy_pass http://$backend_ingester:8765;

            proxy_no_cache 1;
            proxy_cache_bypass 1;
            proxy_cache off;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto https;
% if not backend_auth_flag:
            # Set by the auth_request above ('X-Soliplex-User'); names the
            # caller for the control plane's logs.
            proxy_set_header X-Soliplex-User $soliplex_user;
% endif
            proxy_set_header Authorization "Bearer @@INGESTER_BEARER@@";
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            chunked_transfer_encoding off;
        }

        location = /tui {
            return 301 /tui/;
        }

        location /tui/ {
            set $backend_tui "tui";
            rewrite ^/tui/?(.*)$ /$1 break;

            proxy_pass http://$backend_tui:8002;

            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto https;

            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

% if include_gitea:
        # Gitea under /gitea/.  Only mounted on the HTTPS server -- Gitea's
        # ROOT_URL points at https://.../gitea/, so browsers hitting
        # http://.../gitea would get mixed absolute links.
        # '^~' makes this prefix trump the regex locations above.  Gitea
        # itself does not know the sub-path; we strip /gitea before proxying
        # and let ROOT_URL prepend it on Gitea's generated links.  See:
        # https://docs.gitea.com/administration/reverse-proxies
        location ^~ /gitea {
            client_max_body_size 512M;

            # 'set' MUST precede the 'rewrite ... break' below: 'break' halts
            # the ngx_http_rewrite_module, so a 'set' placed after it never
            # runs and $backend_gitea reads back empty ("no host in upstream
            # :3000").
            set $backend_gitea "gitea";

            # Preserve encoded chars (e.g. %2F in branch names) through the
            # rewrite; two-step trick from the Gitea docs.
            rewrite ^ $request_uri;
            rewrite ^/gitea/?(.*) /$1 break;

            proxy_pass http://$backend_gitea:3000$uri;
            proxy_http_version 1.1;
            proxy_set_header Connection $http_connection;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Host $host$host_port;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Port $backend_port;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 10;
            proxy_send_timeout 60;
            proxy_read_timeout 600;
            proxy_buffering off;
        }
% endif

    }
}
