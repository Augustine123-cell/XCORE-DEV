"""Health endpoint: GET / → JSON status (used by Docker/Railway health checks)."""
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from . import config, db, logger, runtime

_server: Optional[ThreadingHTTPServer] = None


def start(session_count: Callable[[], int]) -> Optional[ThreadingHTTPServer]:
    global _server
    if _server:
        return _server
    cfg = config.get()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(
                {
                    "name": cfg.bot.name,
                    "dev": cfg.owner.name,
                    "status": "online",
                    "uptime": runtime.uptime_seconds(),
                    "mode": db.get_mode(),
                    "sessions": session_count(),
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # silence per-request access logs
            pass

    try:
        _server = ThreadingHTTPServer(("0.0.0.0", cfg.port), Handler)
    except OSError as e:
        logger.warn(f"Health server error: {e}")
        return None
    threading.Thread(target=_server.serve_forever, daemon=True, name="health-server").start()
    logger.ok(f"Health server: http://0.0.0.0:{cfg.port}")
    return _server


def stop() -> None:
    global _server
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
