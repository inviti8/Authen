"""gunicorn config for the Authen node."""

bind = "127.0.0.1:8402"

# Sync workers are correct here: the x402 middleware is the sync variant
# (x402ResourceServerSync + HTTPFacilitatorClientSync), and settlement happens inline
# inside the request. Threads give headroom for the per-issue render in Phase 2
# without pulling in an async stack.
workers = 2
threads = 4
worker_class = "gthread"

# Must exceed the route's max_timeout_seconds (120) and nginx's proxy_read_timeout
# margin. The whole-issue render lives inside the request window once Phase 2 lands
# (IMPLEMENTATION_PLAN.md §4.6); a worker killed mid-settle is the bad case.
timeout = 180
graceful_timeout = 30
keepalive = 5

# systemd Type=notify
import os  # noqa: E402

if os.environ.get("NOTIFY_SOCKET"):
    pass  # gunicorn detects this itself; kept explicit so the unit file makes sense

accesslog = "-"
errorlog = "-"
loglevel = "info"
# Log the settlement receipt's presence, not its contents.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'

preload_app = False  # each worker builds its own facilitator client
