# Gunicorn settings for the VPS. Started by deploy/mythras-site.service.
bind = "127.0.0.1:8000"          # only Caddy can reach it; never expose this port
workers = 3                      # ~2 x CPU cores + 1; 3 is fine for a small VPS
timeout = 120                    # room for jar downloads/uploads on slow connections
graceful_timeout = 20
keepalive = 5
preload_app = True               # create the database once, before forking workers
forwarded_allow_ips = "127.0.0.1"
control_socket_disable = True    # gunicorn >= 25.1: no gunicornc socket (the service user has no home dir)
accesslog = "-"                  # goes to journald
errorlog = "-"
loglevel = "info"
