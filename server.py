#!/usr/bin/env python3
"""
Proflex Position Monitor — local server for the Proflex Terminal.

Serves the existing static dashboard (index.html + data/status.json + closed
positions) on PORT and adds a live-refresh endpoint so the REFRESH button
actually re-pulls prices instead of just re-reading a stale file:

  GET  /                 -> index.html
  GET  /<any static>     -> file from this directory (data/status.json, etc.)
  POST /api/refresh      -> runs monitor.py (live yfinance pull), rewrites
                            data/status.json, returns {ok, updated_at}

Backend logic (breakeven framework, alerts, option-chain fetch) is untouched —
this only wraps monitor.py. Local refresh never pushes to git.

Run:  python3 server.py     (auto-started by proflexterminal on port 5053)
"""
import json
import os
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
PORT = int(os.getenv("PORT") or 5053)
STATUS_FILE = HERE / "data" / "status.json"


def run_monitor():
    """Run monitor.py to pull live prices and rewrite status.json.

    GITHUB_ACTIONS=true makes monitor.py skip the git commit/push — a refresh
    click should update the local view only, not deploy.
    """
    env = dict(os.environ, GITHUB_ACTIONS="true")
    proc = subprocess.run(
        [sys.executable, "monitor.py"],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=180,
    )
    return proc


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # never cache dashboard data — always show the freshest status.json
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/refresh":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            proc = run_monitor()
            updated_at = None
            if STATUS_FILE.exists():
                updated_at = json.loads(STATUS_FILE.read_text()).get("updated_at")
            self._json(200, {
                "ok": proc.returncode == 0,
                "updated_at": updated_at,
                "returncode": proc.returncode,
                "log": (proc.stdout or "")[-1500:],
                "err": (proc.stderr or "")[-500:],
            })
        except subprocess.TimeoutExpired:
            self._json(504, {"ok": False, "error": "monitor.py timed out"})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})


def main():
    print(f"Proflex Position Monitor server on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
