import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server
from auth import Users
from store import Store

PDF = b"%PDF-1.4\n%%EOF\n"
GOOD_MAP = 'Sitemap label="Tiny" {\n  Text id=a label="A"\n}\n'


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "data", ROOT / "maps")
        self.users = Users(Path(self._tmp.name) / "data")
        self.httpd = server.make_server("127.0.0.1", 0, self.store, ROOT / "static", self.users)
        self.port = self.httpd.server_address[1]
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close()))

        # every test acts as a signed-in user unless it passes cookie=None
        st, hdrs, _ = self._raw("POST", "/api/auth/register", {"username": "tester", "password": "correct horse"})
        self.cookie = hdrs["Set-Cookie"].split(";")[0]

    def _raw(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def req(self, method, path, body=None, headers=None, cookie="default"):
        headers = dict(headers or {})
        ck = self.cookie if cookie == "default" else cookie
        if ck:
            headers["Cookie"] = ck
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheader("Content-Type") or "", data

    def jreq(self, *a, **k):
        st, _, data = self.req(*a, **k)
        return st, json.loads(data or b"{}")

    def new_review(self):
        st, r = self.jreq("POST", "/api/reviews", {})
        self.assertEqual(st, 201)
        return r["id"]


class TestApi(ServerCase):
    def test_create_then_map_and_pdf_endpoints(self):
        rid = self.new_review()
        st, tree = self.jreq("GET", f"/api/reviews/{rid}/map")
        self.assertEqual((st, tree["type"]), (200, "Sitemap"))
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}/pdf")[0], 404)
        st, _, _ = self.req("PUT", f"/api/reviews/{rid}/pdf", PDF, {"Content-Type": "application/pdf"})
        self.assertEqual(st, 200)
        st, ctype, data = self.req("GET", f"/api/reviews/{rid}/pdf")
        self.assertEqual((st, ctype, data), (200, "application/pdf", PDF))
        self.assertEqual(self.req("DELETE", f"/api/reviews/{rid}/pdf")[0], 200)
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}/pdf")[0], 404)

    def test_review_crud_and_unknown_id(self):
        rid = self.new_review()
        st, _ = self.jreq("PUT", f"/api/reviews/{rid}", {"values": {"a": "x"}, "title": "T"})
        self.assertEqual(st, 200)
        st, r = self.jreq("GET", f"/api/reviews/{rid}")
        self.assertEqual((r["values"], r["title"]), ({"a": "x"}, "T"))
        st, lst = self.jreq("GET", "/api/reviews")
        self.assertEqual([x["id"] for x in lst], [rid])
        self.assertEqual(self.req("GET", "/api/reviews/nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/reviews/..%2f..%2fetc")[0], 404)
        self.assertEqual(self.req("DELETE", f"/api/reviews/{rid}")[0], 200)
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}")[0], 404)

    def test_put_invalid_map_returns_422_and_keeps_old(self):
        rid = self.new_review()
        self.assertEqual(self.req("PUT", f"/api/reviews/{rid}/map", GOOD_MAP.encode())[0], 200)
        st, body = self.jreq("PUT", f"/api/reviews/{rid}/map", b"Sitemap {{{")
        self.assertEqual(st, 422)
        self.assertIn("map error", body["error"])
        st, tree = self.jreq("GET", f"/api/reviews/{rid}/map")
        self.assertEqual(tree["attrs"]["label"], "Tiny")
        st, _, raw = self.req("GET", f"/api/reviews/{rid}/map.map")
        self.assertEqual(raw.decode(), GOOD_MAP)
        self.assertEqual(self.req("DELETE", f"/api/reviews/{rid}/map")[0], 200)
        st, tree = self.jreq("GET", f"/api/reviews/{rid}/map")
        self.assertNotEqual(tree["attrs"]["label"], "Tiny")

    def test_put_non_pdf_returns_400_and_keeps_old(self):
        rid = self.new_review()
        self.req("PUT", f"/api/reviews/{rid}/pdf", PDF)
        self.assertEqual(self.req("PUT", f"/api/reviews/{rid}/pdf", b"<html>")[0], 400)
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}/pdf")[2], PDF)

    def test_oversized_upload_returns_413_without_changing_pdf(self):
        rid = self.new_review()
        self.req("PUT", f"/api/reviews/{rid}/pdf", PDF)
        Store.MAX_PDF, old = 10, Store.MAX_PDF
        try:
            self.assertEqual(self.req("PUT", f"/api/reviews/{rid}/pdf", PDF + b"x" * 50)[0], 413)
        finally:
            Store.MAX_PDF = old
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}/pdf")[2], PDF)

    def test_zip_backup_download_and_import_roundtrip(self):
        rid = self.new_review()
        self.jreq("PUT", f"/api/reviews/{rid}", {"values": {"a": "hi"}})
        self.req("PUT", f"/api/reviews/{rid}/pdf", PDF)
        st, ctype, blob = self.req("GET", f"/api/reviews/{rid}/backup.zip")
        self.assertEqual((st, ctype), (200, "application/zip"))
        st, r2 = self.jreq("POST", "/api/reviews/import", blob, {"Content-Type": "application/zip"})
        self.assertEqual(st, 201)
        self.assertNotEqual(r2["id"], rid)
        self.assertEqual(self.req("GET", f"/api/reviews/{r2['id']}/pdf")[2], PDF)
        st, got = self.jreq("GET", f"/api/reviews/{r2['id']}")
        self.assertEqual(got["values"], {"a": "hi"})
        self.assertEqual(self.req("POST", "/api/reviews/import", b"garbage", {"Content-Type": "application/zip"})[0], 400)

    def test_import_old_json_format(self):
        st, r = self.jreq("POST", "/api/reviews/import", {"id": "old1", "title": "Old", "values": {"summary": "s"}, "map": "aamas2027"})
        self.assertEqual((st, r["id"]), (201, "old1"))
        self.assertEqual(self.req("POST", "/api/reviews/import", {"nope": 1})[0], 400)

    def test_templates_listing_and_create_from_template(self):
        st, ts = self.jreq("GET", "/api/templates")
        self.assertEqual(st, 200)
        by_id = {x["id"]: x for x in ts}
        self.assertGreaterEqual(len(by_id), 20)
        self.assertEqual((by_id["icml-2025"]["kind"], by_id["icml-2025"]["fidelity"]), ("conference", "form"))
        st, r = self.jreq("POST", "/api/reviews", {"template": "iclr-2025", "title": "My paper"})
        self.assertEqual((st, r["template"], r["title"]), (201, "iclr-2025", "My paper"))
        st, tree = self.jreq("GET", f"/api/reviews/{r['id']}/map")
        self.assertEqual(tree["attrs"]["label"], "ICLR 2025 - Review")
        self.assertEqual(self.jreq("POST", "/api/reviews", {"template": "nope"})[0], 404)
        self.assertEqual(self.jreq("POST", "/api/reviews", {"template": "../x"})[0], 400)
        st, lst = self.jreq("GET", "/api/reviews")
        self.assertEqual((lst[0]["template"], lst[0]["mapLabel"], lst[0]["progress"]["filled"]), ("iclr-2025", "ICLR 2025 - Review", 0))

    def test_switch_template_keeps_answers(self):
        rid = self.new_review()
        self.jreq("PUT", f"/api/reviews/{rid}", {"valuesPatch": {"summary": "kept"}})
        st, _ = self.jreq("POST", f"/api/reviews/{rid}/template", {"template": "tmlr"})
        self.assertEqual(st, 200)
        self.assertEqual(self.jreq("GET", f"/api/reviews/{rid}/map")[1]["attrs"]["venue"], "TMLR")
        got = self.jreq("GET", f"/api/reviews/{rid}")[1]
        self.assertEqual((got["template"], got["values"]), ("tmlr", {"summary": "kept"}))
        self.assertEqual(self.jreq("POST", f"/api/reviews/{rid}/template", {"template": "nope"})[0], 404)
        self.assertEqual(self.jreq("POST", f"/api/reviews/{rid}/template", {})[0], 400)

    def test_custom_template_upload_list_use_and_delete(self):
        st, _ = self.jreq("PUT", "/api/templates/my-venue", GOOD_MAP.encode(), {"Content-Type": "text/plain"})
        self.assertEqual(st, 200)
        by_id = {x["id"]: x for x in self.jreq("GET", "/api/templates")[1]}
        self.assertTrue(by_id["my-venue"]["custom"])
        st, r = self.jreq("POST", "/api/reviews", {"template": "my-venue"})
        self.assertEqual(self.jreq("GET", f"/api/reviews/{r['id']}/map")[1]["attrs"]["label"], "Tiny")
        st, err = self.jreq("PUT", "/api/templates/broken", b"Sitemap {{{")
        self.assertEqual(st, 422)
        self.assertIn("map error", err["error"])
        self.assertNotIn("broken", {x["id"] for x in self.jreq("GET", "/api/templates")[1]})
        self.assertEqual(self.jreq("DELETE", "/api/templates/my-venue")[0], 200)
        self.assertEqual(self.jreq("DELETE", "/api/templates/my-venue")[0], 404)
        self.assertEqual(self.jreq("DELETE", "/api/templates/blank")[0], 404)  # bundled ones are read-only
        self.assertEqual(self.jreq("GET", f"/api/reviews/{r['id']}/map")[1]["attrs"]["label"], "Tiny")  # existing review keeps its copy

    def test_health_and_no_auth(self):
        st, _, body = self.req("GET", "/healthz")
        self.assertEqual((st, body), (200, b"ok"))
        self.assertEqual(self.req("GET", "/api/reviews")[0], 200)


class TestConfig(unittest.TestCase):
    def test_pdf_limit_from_env(self):
        self.assertEqual(server.pdf_limit_from_env({}), 100 * 1024 * 1024)
        self.assertEqual(server.pdf_limit_from_env({"MAX_PDF_MB": "5"}), 5 * 1024 * 1024)
        for bad in ("abc", "0", "-3", "1.5", ""):
            if bad == "":
                self.assertEqual(server.pdf_limit_from_env({"MAX_PDF_MB": bad}), 100 * 1024 * 1024)
                continue
            with self.assertRaises(ValueError, msg=bad):
                server.pdf_limit_from_env({"MAX_PDF_MB": bad})


class TestHttpHardening(ServerCase):
    def raw(self, request: bytes):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        s.close()
        return data

    def test_negative_content_length_is_rejected_and_nothing_is_saved(self):
        rid = self.new_review()
        resp = self.raw(f"PUT /api/reviews/{rid}/pdf HTTP/1.0\r\nCookie: {self.cookie}\r\nContent-Length: -1\r\n\r\n".encode() + PDF)
        self.assertTrue(resp.startswith(b"HTTP/1.0 400"), resp[:60])
        self.assertEqual(self.req("GET", f"/api/reviews/{rid}/pdf")[0], 404)

    def test_values_patch_via_api(self):
        rid = self.new_review()
        self.jreq("PUT", f"/api/reviews/{rid}", {"values": {"a": "1", "b": "2"}})
        self.jreq("PUT", f"/api/reviews/{rid}", {"valuesPatch": {"b": "B"}})
        self.assertEqual(self.jreq("GET", f"/api/reviews/{rid}")[1]["values"], {"a": "1", "b": "B"})


class TestStatic(ServerCase):
    def test_tab_icons_are_served_and_linked(self):
        st, ctype, body = self.req("GET", "/")
        for href in (b'rel="icon" type="image/svg+xml" href="/favicon.svg"', b'rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png"',
                     b'rel="apple-touch-icon" href="/apple-touch-icon.png"'):
            self.assertIn(href, body)
        for path, want in (("/favicon.svg", "image/svg+xml"), ("/favicon-32.png", "image/png"),
                           ("/apple-touch-icon.png", "image/png"), ("/favicon.ico", "image/")):
            st, ctype, data = self.req("GET", path)
            self.assertEqual(st, 200, path)
            self.assertTrue(ctype.startswith(want), (path, ctype))
            self.assertGreater(len(data), 100, path)
        # Safari keeps tab icons in its own cache and may skip responses marked no-store: icons must be cacheable
        for path in ("/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png", "/favicon.ico"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", path)
            r = c.getresponse(); r.read(); c.close()
            cc = r.getheader("Cache-Control") or ""
            self.assertIn("max-age=", cc, path)
            self.assertNotIn("no-store", cc, path)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/app.js"); r = c.getresponse(); r.read(); c.close()
        self.assertIn("no-store", r.getheader("Cache-Control") or "")  # the app itself is never served stale
        self.assertIn(b'rel="shortcut icon" href="/favicon.ico"', self.req("GET", "/")[2])
        self.assertTrue(self.req("GET", "/favicon-32.png")[2].startswith(b"\x89PNG"))
        self.assertTrue(self.req("GET", "/favicon.ico")[2].startswith(b"\x00\x00\x01\x00"))

    def test_index_served_and_traversal_blocked(self):
        st, ctype, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ctype)
        st, ctype, _ = self.req("GET", "/vendor/pdfjs/pdf.min.mjs")
        self.assertEqual((st, ctype.split(";")[0]), (200, "text/javascript"))
        for p in ("/../server.py", "/..%2fserver.py", "/%2e%2e/store.py", "/static/../../etc/passwd"):
            self.assertEqual(self.req("GET", p)[0], 404, p)


if __name__ == "__main__":
    unittest.main()


class TestAccounts(ServerCase):
    def signup(self, name, pw="another password", **kw):
        st, hdrs, data = self._raw("POST", "/api/auth/register", {"username": name, "password": pw})
        return st, (hdrs.get("Set-Cookie") or "").split(";")[0], json.loads(data)

    def test_everything_but_auth_needs_a_session(self):
        for method, path in (("GET", "/api/reviews"), ("GET", "/api/templates"), ("POST", "/api/reviews"),
                             ("GET", "/api/reviews/abc/pdf")):
            self.assertEqual(self.req(method, path, cookie=None)[0], 401, path)
        self.assertEqual(self.req("GET", "/healthz", cookie=None)[0], 200)
        self.assertEqual(self.req("GET", "/", cookie=None)[0], 200)  # the sign-in page itself must load

    def test_forged_or_garbage_cookie_is_rejected(self):
        self.assertEqual(self.req("GET", "/api/reviews", cookie="rw_session=abc.123.0.sig")[0], 401)
        uid, exp, gen, sig = self.cookie.split("=", 1)[1].split(".")
        forged = f"rw_session={uid}.{int(exp) + 999}.{gen}.{sig}"
        self.assertEqual(self.req("GET", "/api/reviews", cookie=forged)[0], 401)

    def test_users_only_see_and_touch_their_own_reviews(self):
        mine = self.new_review()
        self.jreq("PUT", f"/api/reviews/{mine}", {"values": {"a": "secret"}})
        st, bob, _ = self.signup("bob")
        self.assertEqual(st, 200)
        self.assertEqual(self.jreq("GET", "/api/reviews", cookie=bob)[1], [])
        for method, suffix in (("GET", ""), ("PUT", ""), ("DELETE", ""), ("GET", "/map"), ("GET", "/pdf"),
                               ("GET", "/backup.zip"), ("PUT", "/pdf"), ("POST", "/template"), ("GET", "/map.map")):
            body = {"values": {}} if method in ("PUT", "POST") and suffix != "/pdf" else (PDF if suffix == "/pdf" else None)
            self.assertEqual(self.req(method, f"/api/reviews/{mine}{suffix}", body, cookie=bob)[0], 404, (method, suffix))
        self.assertEqual(self.jreq("GET", f"/api/reviews/{mine}")[1]["values"], {"a": "secret"})  # untouched
        theirs = self.jreq("POST", "/api/reviews", {}, cookie=bob)[1]["id"]
        self.assertEqual([r["id"] for r in self.jreq("GET", "/api/reviews")[1]], [mine])
        self.assertEqual(self.req("GET", f"/api/reviews/{theirs}")[0], 404)

    def test_owner_cannot_be_changed_or_imported(self):
        rid = self.new_review()
        _, bob, _ = self.signup("bob")
        self.jreq("PUT", f"/api/reviews/{rid}", {"owner": "someone-else"})
        self.assertEqual(self.jreq("GET", "/api/reviews")[1][0]["id"], rid)  # still mine
        st, _, zdata = self.req("GET", f"/api/reviews/{rid}/backup.zip")
        got = self.jreq("POST", "/api/reviews/import", zdata, {"Content-Type": "application/zip"}, cookie=bob)[1]
        self.assertEqual([r["id"] for r in self.jreq("GET", "/api/reviews", cookie=bob)[1]], [got["id"]])
        self.assertNotEqual(got["id"], rid)

    def test_login_logout_and_wrong_password(self):
        self.signup("carol", "carol-password")
        st, _, _ = self._raw("POST", "/api/auth/login", {"username": "carol", "password": "nope-nope"})
        self.assertEqual(st, 401)
        st, hdrs, _ = self._raw("POST", "/api/auth/login", {"username": "CAROL", "password": "carol-password"})
        self.assertEqual(st, 200)
        cookie = hdrs["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        me = self.jreq("GET", "/api/auth/me", cookie=cookie.split(";")[0])[1]
        self.assertEqual(me["user"]["username"], "carol")
        self.assertNotIn("hash", json.dumps(me))
        self.assertEqual(self.jreq("GET", "/api/auth/me", cookie=None)[1]["user"], None)
        st, hdrs, _ = self._raw("POST", "/api/auth/logout", {})
        self.assertIn("Max-Age=0", hdrs["Set-Cookie"])

    def test_registration_rules(self):
        self.assertEqual(self.signup("tester")[0], 409)       # taken (case-insensitive too)
        self.assertEqual(self.signup("TESTER")[0], 409)
        self.assertEqual(self.signup("ab")[0], 400)
        self.assertEqual(self.signup("has space")[0], 400)
        self.assertEqual(self.signup("dave", "short")[0], 400)
        self.assertEqual(self.signup("dave", "x" * 500)[0], 400)

    def test_registration_can_be_closed_but_first_account_always_works(self):
        self.users.allow_registration = False
        self.assertEqual(self.signup("eve")[0], 403)
        self.assertFalse(self.jreq("GET", "/api/auth/me", cookie=None)[1]["registration"])
        with tempfile.TemporaryDirectory() as d:
            fresh = Users(d, allow_registration=False)
            self.assertTrue(fresh.can_register())
            fresh.register("first", "first-password")
            self.assertFalse(fresh.can_register())

    def test_password_change_signs_out_other_sessions(self):
        st, hdrs, _ = self._raw("POST", "/api/auth/login", {"username": "tester", "password": "correct horse"})
        other = hdrs["Set-Cookie"].split(";")[0]
        self.assertEqual(self.req("GET", "/api/reviews", cookie=other)[0], 200)
        self.assertEqual(self.jreq("POST", "/api/auth/password", {"old": "bad", "new": "brand new pw"})[0], 401)
        st, _, _ = self.req("POST", "/api/auth/password", {"old": "correct horse", "new": "brand new pw"})
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", "/api/reviews", cookie=other)[0], 401)
        st, _, _ = self._raw("POST", "/api/auth/login", {"username": "tester", "password": "brand new pw"})
        self.assertEqual(st, 200)

    def test_repeated_failures_are_throttled(self):
        for _ in range(Users.MAX_FAILURES):
            self.assertEqual(self._raw("POST", "/api/auth/login", {"username": "tester", "password": "wrong-one"})[0], 401)
        self.assertEqual(self._raw("POST", "/api/auth/login", {"username": "tester", "password": "correct horse"})[0], 429)

    def test_first_account_inherits_reviews_from_before_accounts(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "data", ROOT / "maps")
            old = store.create("old one")                      # no owner, as if made by the previous version
            users = Users(Path(d) / "data")
            uid = users.register("owner", "owner-password")[0]["id"]
            self.assertEqual(store.list(uid), [])
            store.claim_unowned(uid)
            self.assertEqual([r["id"] for r in store.list(uid)], [old["id"]])
            self.assertEqual(store.list(None), [])

    def test_admin_reset_changes_password_and_signs_out(self):
        self.users.reset_password("TESTER", "reset-by-admin")
        self.assertEqual(self.req("GET", "/api/reviews")[0], 401)  # old cookie is void
        self.assertEqual(self._raw("POST", "/api/auth/login", {"username": "tester", "password": "correct horse"})[0], 401)
        self.assertEqual(self._raw("POST", "/api/auth/login", {"username": "tester", "password": "reset-by-admin"})[0], 200)
        with self.assertRaises(Exception):
            self.users.reset_password("nobody", "whatever-pass")

    def test_secret_and_users_files_are_private(self):
        data = Path(self._tmp.name) / "data"
        for name in (".secret", "users.json"):
            self.assertEqual((data / name).stat().st_mode & 0o077, 0, name)
