"""A tiny canned-response HTTP server for deterministic provider E2E tests.

Serves each provider's fixture JSON at the path that provider hits, so a real
``Provider.search(...)`` call (full HTTP round-trip through httpx) runs with no
keys, no cost, and no real network egress. Also supports a ``/flaky`` route that
returns 503 a configurable number of times before succeeding (to exercise the
bounded retry/backoff path) and a ``/boom`` route that always 500s.

Used both by the pytest mock-server E2E (:mod:`tests.test_mock_e2e`) and by the
haybarn SQL E2E launcher (:mod:`tests.mock_server` run as ``python -m``).
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


# Map (method, path) -> fixture name. Providers hit distinct paths.
_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/web/search"): "brave",  # brave
    ("POST", "/search"): "tavily",  # tavily AND exa AND searxng share /search;
    ("GET", "/search"): "searxng",  #   disambiguated by method below
    ("GET", "/search.json"): "serpapi",  # serpapi
    ("GET", "/"): "ddg",  # ddg instant answer
}


class _Handler(BaseHTTPRequestHandler):
    # Per-server flaky counter (set on the server instance).
    def log_message(self, *args: Any) -> None:  # silence the default stderr spam
        pass

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/paging/web/search":
            # A brave-shaped payload that honors the ?count & ?offset query params
            # exactly as the real API pages -- so a 10-result page spans several
            # 5-row emit chunks (exercising the scan-state batch-boundary path).
            qs = parse_qs(urlsplit(self.path).query)
            count = int(qs.get("count", ["10"])[0])
            offset = int(qs.get("offset", ["0"])[0])
            start = offset * count
            results = [
                {
                    "title": f"Result {i}",
                    "url": f"https://example.com/{i}",
                    "description": f"snippet {i}",
                }
                for i in range(start, start + count)
            ]
            self._send(200, json.dumps({"web": {"results": results}}).encode())
            return

        if path == "/boom":
            self._send(500, b'{"error": "boom"}')
            return

        if path == "/flaky":
            server: Any = self.server
            with server.flaky_lock:
                if server.flaky_remaining > 0:
                    server.flaky_remaining -= 1
                    self._send(503, b'{"error": "try later"}')
                    return
            self._send(200, _fixture("brave"))
            return

        # Exa posts to /search too; route POST /search to exa when the body asks
        # for it, else tavily. We keep it simple: exa uses a distinct path here.
        if path == "/exa/search" and method == "POST":
            self._send(200, _fixture("exa"))
            return
        if path == "/serper/search" and method == "POST":
            self._send(200, _fixture("serper"))
            return

        name = _ROUTES.get((method, path))
        if name is None:
            self._send(404, b'{"error": "no route"}')
            return
        self._send(200, _fixture(name))

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        # Drain the request body so the client isn't left hanging.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        self._route("POST")


class MockServer:
    """A threaded canned-response HTTP server with a stable ``base`` URL."""

    def __init__(self, flaky: int = 0) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.flaky_lock = threading.Lock()  # type: ignore[attr-defined]
        self._httpd.flaky_remaining = flaky  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> MockServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


def main() -> None:
    """Run the mock server in the foreground, printing ``PORT:<n>`` once bound.

    Mirrors the vgi serve harness convention so the haybarn launcher can read the
    chosen port. Stays up until killed.
    """
    flaky = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv = MockServer(flaky=flaky)
    srv._thread.start()
    port = srv._httpd.server_address[1]
    print(f"PORT:{port}", flush=True)
    try:
        srv._thread.join()
    except KeyboardInterrupt:
        srv._httpd.shutdown()


if __name__ == "__main__":
    main()


# Re-export for tests that just want the JSON shapes.
def fixture_json(name: str) -> dict[str, Any]:
    return json.loads(_fixture(name))
