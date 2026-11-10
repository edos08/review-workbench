#!/usr/bin/env python3
"""Review Workbench HTTP server. Standard library only.

Config (environment): DATA_DIR (default ./data), HOST (default 127.0.0.1), PORT (default 8000),
MAX_PDF_MB (default 100), LEGACY_REVIEWS_DIR (optional: folder of old single-file reviews to import at start).
"""
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import mapparser
from store import Store

ROOT = Path(__file__).resolve().parent
mimetypes.add_type("text/javascript", ".mjs")


class HttpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def pdf_limit_from_env(env) -> int:
    """MAX_PDF_MB (megabytes, default 100) -> bytes. Empty means default; junk raises ValueError."""
    raw = (env.get("MAX_PDF_MB") or "").strip()
    if not raw:
        return 100 * 1024 * 1024
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"MAX_PDF_MB must be a whole number of megabytes >= 1, got {raw!r}")
    return int(raw) * 1024 * 1024


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "review").lower()).strip("-")[:50] or "review"


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewWorkbench/2.0"
    timeout = 120  # a stalled client cannot hold a thread forever

    def log_message(self, fmt, *args):
        pass

    # ---------- plumbing ----------
    @property
    def store(self):
        return self.server.store

    def _send(self, code, body, ctype="application/json", extra=None, cache="no-store"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0:
            self.close_connection = True
            raise HttpError(400, "invalid Content-Length")
        if n > limit:
            self.close_connection = True
            raise HttpError(413, f"upload too large (limit {limit // (1024 * 1024) or 1} MB)")
        return self.rfile.read(n)

    def _json_body(self):
        return json.loads(self._raw(self.store.MAX_JSON) or b"{}")

    def _route(self, method):
        parts = [p for p in unquote(urlparse(self.path).path).split("/") if p]
        try:
            if parts == ["healthz"]:
                return self._send(200, b"ok", "text/plain")
            if parts[:1] == ["api"]:
                return self._api(method, parts[1:])
            if method != "GET":
                raise HttpError(405, "method not allowed")
            return self._static(parts)
        except HttpError as e:
            self._send(e.code, {"error": str(e)})
        except FileNotFoundError:
            self._send(404, {"error": "not found"})
        except mapparser.MapError as e:
            self._send(422, {"error": f"map error: {e}"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # pragma: no cover
            self._send(500, {"error": repr(e)})

    def _static(self, parts):
        static = self.server.static_dir.resolve()
        f = (static / ("/".join(parts) or "index.html")).resolve()
        if static not in f.parents or not f.is_file():
            raise HttpError(404, "not found")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        # Tab icons must be cacheable (Safari keeps them in its own cache); everything else is never served stale.
        is_icon = f.name.startswith(("favicon", "apple-touch-icon"))
        self._send(200, f.read_bytes(), ctype, cache="public, max-age=86400" if is_icon else "no-store")

    # ---------- API ----------
    def _api(self, method, p):
        s = self.store
        if p == ["templates"] and method == "GET":
            return self._send(200, s.templates())
        if len(p) == 2 and p[0] == "templates":
            if method == "PUT":
                s.set_custom_template(p[1], self._raw(s.MAX_MAP).decode("utf-8", "replace"))
                return self._send(200, {"ok": True})
            if method == "DELETE":
                s.delete_custom_template(p[1])
                return self._send(200, {"ok": True})
        if p == ["reviews"] and method == "GET":
            return self._send(200, s.list())
        if p == ["reviews"] and method == "POST":
            body = self._json_body()
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            title = body.get("title", "")
            return self._send(201, s.create(title if isinstance(title, str) else "", body.get("template") or None))
        if p == ["reviews", "import"] and method == "POST":
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/zip":
                r = s.import_zip(self._raw(s.MAX_PDF + s.MAX_JSON + s.MAX_MAP))
            else:
                r = s.import_review_json(self._json_body())
            return self._send(201, r)
        if len(p) >= 2 and p[0] == "reviews":
            rid, rest = p[1], p[2:]
            if not rest:
                if method == "GET":
                    return self._send(200, s.get(rid))
                if method == "PUT":
                    body = self._json_body()
                    if not isinstance(body, dict):
                        raise ValueError("expected a JSON object")
                    return self._send(200, {"updated": s.save(rid, body)["updated"]})
                if method == "DELETE":
                    s.delete(rid)
                    return self._send(200, {"ok": True})
            elif rest == ["map"]:
                if method == "GET":
                    tree = mapparser.parse(s.map_text(rid))
                    return self._send(200, tree)
                if method == "PUT":
                    s.set_map(rid, self._raw(s.MAX_MAP).decode("utf-8", "replace"))
                    return self._send(200, {"ok": True})
                if method == "DELETE":
                    s.reset_map(rid)
                    return self._send(200, {"ok": True})
            elif rest == ["template"] and method == "POST":
                body = self._json_body()
                tid = body.get("template") if isinstance(body, dict) else None
                if not tid:
                    raise ValueError("missing 'template'")
                s.set_template(rid, tid)
                return self._send(200, {"ok": True})
            elif rest == ["map.map"] and method == "GET":
                return self._send(200, s.map_text(rid).encode(), "text/plain; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="review.map"'})
            elif rest == ["pdf"]:
                if method == "GET":
                    path = s.pdf_path(rid)
                    if path is None:
                        raise HttpError(404, "no PDF for this review")
                    return self._send(200, path.read_bytes(), "application/pdf")
                if method == "PUT":
                    s.set_pdf(rid, self._raw(s.MAX_PDF))
                    return self._send(200, {"ok": True})
                if method == "DELETE":
                    s.delete_pdf(rid)
                    return self._send(200, {"ok": True})
            elif rest == ["backup.zip"] and method == "GET":
                name = f"{slug(s.get(rid).get('title'))}-{rid}.zip"
                return self._send(200, s.export_zip(rid), "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
        raise HttpError(404, "not found")

    def do_GET(self): self._route("GET")
    def do_POST(self): self._route("POST")
    def do_PUT(self): self._route("PUT")
    def do_DELETE(self): self._route("DELETE")


def make_server(host, port, store, static_dir):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.store = store
    srv.static_dir = Path(static_dir)
    return srv


def main():
    data_dir = Path(os.environ.get("DATA_DIR") or ROOT / "data")
    try:
        Store.MAX_PDF = pdf_limit_from_env(os.environ)
    except ValueError as e:
        sys.exit(f"Configuration error: {e}")
    store = Store(data_dir, ROOT / "maps")
    legacy = os.environ.get("LEGACY_REVIEWS_DIR")
    if legacy:
        moved = store.migrate_legacy(legacy)
        if moved:
            print(f"Imported {len(moved)} review(s) from {legacy}: {', '.join(moved)}")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8000))
    srv = make_server(host, port, store, ROOT / "static")
    print(f"Review Workbench on http://{host}:{port}  (data: {data_dir})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
