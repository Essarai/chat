from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


WEB_DIR = Path(__file__).resolve().parent / "web"
LIST_PAGE = "/static/production-task-list.html"
ALLOWED_FILES = {
    "/static/production-task.html": ("production-task.html", "text/html; charset=utf-8"),
    LIST_PAGE: ("production-task-list.html", "text/html; charset=utf-8"),
    "/static/production-task.css": ("production-task.css", "text/css; charset=utf-8"),
    "/static/production-task.js": ("production-task.js", "text/javascript; charset=utf-8"),
    "/static/production-task-list.css": ("production-task-list.css", "text/css; charset=utf-8"),
    "/static/production-task-list.js": ("production-task-list.js", "text/javascript; charset=utf-8"),
}


class MockPageHandler(BaseHTTPRequestHandler):
    server_version = "ProductionTaskMock/1.0"

    def do_HEAD(self) -> None:
        self._handle(send_body=False)

    def do_GET(self) -> None:
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/":
            self.send_response(302)
            self.send_header("Location", LIST_PAGE)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return

        if request_path == "/health":
            payload = json.dumps(
                {"ok": True, "service": "production-task-mock"},
                ensure_ascii=False,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8", send_body)
            return

        asset = ALLOWED_FILES.get(request_path)
        if asset is None:
            self.send_error(404, "Not Found")
            return

        filename, content_type = asset
        file_path = WEB_DIR / filename
        if not file_path.is_file():
            self.send_error(404, "Asset Not Found")
            return
        self._send_bytes(file_path.read_bytes(), content_type, send_body)

    def _send_bytes(self, payload: bytes, content_type: str, send_body: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            self.wfile.write(payload)


def main() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MockPageHandler)
    print(f"Production task Mock running on http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
