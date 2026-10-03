"""A stand-in dev server used by ``portglance --demo``.

It is started under names such as ``node .../vite`` or ``uvicorn`` so it looks
like the real thing to PortGlance, and is configured through environment
variables so its command line can mimic the real tool. It must not import
anything from PortGlance: it runs as a plain script.
"""

import html
import os
import signal
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ["PORTGLANCE_DEMO_PORT"])
HOST = os.environ.get("PORTGLANCE_DEMO_HOST", "127.0.0.1")
KIND = os.environ.get("PORTGLANCE_DEMO_KIND", "http")
TITLE = os.environ.get("PORTGLANCE_DEMO_TITLE", "Dev server")
SUBTITLE = os.environ.get("PORTGLANCE_DEMO_SUBTITLE", "")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font: 16px/1.5 system-ui, sans-serif;
    background: radial-gradient(circle at 30% 20%, #3584e4 0, #1a5fb4 45%, #241f31 100%);
  }}
  main {{
    background: Canvas; color: CanvasText; border-radius: 18px;
    padding: 36px 44px; box-shadow: 0 20px 60px #0006; max-width: 30rem;
  }}
  .port {{ font: 800 3.2rem/1 ui-monospace, monospace; color: #3584e4; }}
  h1 {{ margin: 12px 0 4px; font-size: 1.4rem; }}
  p {{ margin: 0; opacity: .7; }}
</style>
</head>
<body>
<main>
  <div class="port">:{port}</div>
  <h1>{title}</h1>
  <p>{subtitle}</p>
  <p>A fake server started by <strong>PortGlance demo mode</strong>.</p>
</main>
</body>
</html>
"""


def _terminate(_signum, _frame):
    sys.exit(0)


def _ignore(_signum, _frame):
    sys.stderr.write(f"{TITLE}: ignoring SIGTERM\n")


signal.signal(signal.SIGTERM, _ignore if os.environ.get("PORTGLANCE_DEMO_STUBBORN") else _terminate)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.format(
            title=html.escape(TITLE), subtitle=html.escape(SUBTITLE), port=PORT
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def serve_http():
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in HOST else socket.AF_INET
        allow_reuse_address = True
        daemon_threads = True

    Server((HOST, PORT), Handler).serve_forever()


def serve_tcp():
    family = socket.AF_INET6 if ":" in HOST else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))
        sock.listen()
        while True:
            conn, _ = sock.accept()
            conn.close()


if __name__ == "__main__":
    try:
        serve_http() if KIND == "http" else serve_tcp()
    except KeyboardInterrupt:
        pass
