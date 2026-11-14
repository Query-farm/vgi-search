"""Serve exact public DDG recordings or forward only those bounded reads."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen


def request_key(path):
    """Compare the complete path and query independent of argument order."""
    url = urlsplit(path)
    return url.path, tuple(sorted(parse_qsl(url.query)))


class RecordedAPI:
    """Bound a run to two reads per recorded query, without fallback on failure."""

    def __init__(self, directory: Path, mode: str, artifact_dir: Path):
        if mode not in {"replay", "live"}:
            raise ValueError("DATA_MODE must be replay or live")
        manifest = json.loads((directory / "manifest.json").read_text())
        entries = {}
        for entry in manifest["entries"]:
            body = (directory / entry["filename"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                raise ValueError(f"Recording hash mismatch: {entry['filename']}")
            url = urlsplit(entry["url"])
            if url.scheme != "https" or url.netloc != "api.duckduckgo.com":
                raise ValueError("Unexpected source endpoint")
            entries[request_key(entry["url"])] = (entry, body)
        self.requests = []
        self.lock = threading.Lock()
        artifact_dir.mkdir(exist_ok=True)
        audit_file = artifact_dir / "request-audit.json"
        audit_file.write_text("[]\n")
        owner = self

        def save():
            with owner.lock:
                staged = audit_file.with_suffix(".tmp")
                staged.write_text(json.dumps(owner.requests, indent=2))
                staged.replace(audit_file)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                key = request_key(self.path)
                record = {"method": "GET", "path": self.path, "started_utc": datetime.now(UTC).isoformat()}
                with owner.lock:
                    query_id = entries[key][0]["query_id"] if key in entries else None
                    ordinal = sum(r.get("query_id") == query_id for r in owner.requests) + 1
                    record["query_id"] = query_id
                    owner.requests.append(record)
                try:
                    if key not in entries or ordinal > 2 or len(owner.requests) > 4:
                        raise ValueError("Outside the two-query/four-read budget; no fallback")
                    entry, body = entries[key]
                    record.update(query_id=entry["query_id"], source_url=entry["url"], ordinal=ordinal)
                    if mode == "live":
                        request = Request(
                            entry["url"],
                            headers={
                                "User-Agent": "vgi-search-notebooks/1.0 (+https://github.com/Query-farm/vgi-search)",
                                "Accept": "application/json",
                            },
                        )
                        with urlopen(request, timeout=25) as response:
                            body = response.read()
                            record["source_date"] = response.headers.get("Date")
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("Expected a JSON object from DDG")
                    record.update(
                        status=200,
                        bytes=len(body),
                        sha256=hashlib.sha256(body).hexdigest(),
                        received_utc=datetime.now(UTC).isoformat(),
                    )
                    (artifact_dir / f"{entry['query_id']}-{ordinal}.json").write_bytes(body)
                    save()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    record.update(status=400, error=str(exc), received_utc=datetime.now(UTC).isoformat())
                    save()
                    self.send_error(400, str(exc))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        """Stop this run's owned local endpoint."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
