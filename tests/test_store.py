import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mapparser
from store import Store

DEFAULT_MAP = Path(__file__).resolve().parent.parent / "defaults" / "default.map"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
GOOD_MAP = 'Sitemap label="Tiny" {\n  Text id=a label="A"\n}\n'


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.store = Store(self.data, DEFAULT_MAP)


class TestReviews(StoreCase):
    def test_create_copies_default_map_and_lists(self):
        r = self.store.create("Paper X")
        self.assertEqual(r["title"], "Paper X")
        folder = self.data / "reviews" / r["id"]
        self.assertEqual((folder / "map.map").read_text(), DEFAULT_MAP.read_text())
        self.assertTrue((self.data / "default.map").exists())  # seeded
        listed = self.store.list()
        self.assertEqual([x["id"] for x in listed], [r["id"]])
        self.assertEqual(listed[0]["title"], "Paper X")

    def test_save_filters_unknown_keys_and_is_atomic(self):
        r = self.store.create()
        self.store.save(r["id"], {"values": {"a": "1"}, "evil": "x", "id": "other", "annotations": [{"p": 1}]})
        got = self.store.get(r["id"])
        self.assertEqual(got["values"], {"a": "1"})
        self.assertEqual(got["id"], r["id"])
        self.assertNotIn("evil", got)
        self.assertEqual(got["annotations"], [{"p": 1}])
        self.assertEqual([p.name for p in (self.data / "reviews" / r["id"]).iterdir() if p.suffix == ".tmp"], [])

    def test_values_patch_merges_so_stale_writers_do_not_clobber(self):
        r = self.store.create()
        self.store.save(r["id"], {"values": {"a": "1", "b": "2"}})
        # two stale clients each send only the keys they changed
        self.store.save(r["id"], {"valuesPatch": {"a": "A2"}})
        self.store.save(r["id"], {"valuesPatch": {"c": "3", "b": None}})
        self.assertEqual(self.store.get(r["id"])["values"], {"a": "A2", "c": "3"})
        self.store.save(r["id"], {"pdfState": {"page": 4}})  # a scroll-only save leaves answers alone
        self.assertEqual(self.store.get(r["id"])["values"], {"a": "A2", "c": "3"})
        with self.assertRaises(ValueError):
            self.store.save(r["id"], {"valuesPatch": ["x"]})

    def test_ids_cannot_escape_data_dir(self):
        for bad in ("../x", "a/b", "", ".."):
            with self.assertRaises(ValueError):
                self.store.get(bad)
        with self.assertRaises(FileNotFoundError):
            self.store.get("doesnotexist")

    def test_delete_removes_folder(self):
        r = self.store.create()
        self.store.delete(r["id"])
        self.assertEqual(self.store.list(), [])


class TestMap(StoreCase):
    def test_set_map_invalid_keeps_old_map(self):
        r = self.store.create()
        self.store.set_map(r["id"], GOOD_MAP)
        with self.assertRaises(mapparser.MapError):
            self.store.set_map(r["id"], "Sitemap {{{")
        self.assertEqual(self.store.map_text(r["id"]), GOOD_MAP)

    def test_reset_map_restores_default(self):
        r = self.store.create()
        self.store.set_map(r["id"], GOOD_MAP)
        self.store.reset_map(r["id"])
        self.assertEqual(self.store.map_text(r["id"]), DEFAULT_MAP.read_text())

    def test_oversized_map_rejected(self):
        r = self.store.create()
        with self.assertRaises(ValueError):
            self.store.set_map(r["id"], GOOD_MAP + "#" * (1024 * 1024 + 1))


class TestPdf(StoreCase):
    def test_set_get_delete_pdf(self):
        r = self.store.create()
        self.assertIsNone(self.store.pdf_path(r["id"]))
        self.store.set_pdf(r["id"], PDF)
        self.assertEqual(self.store.pdf_path(r["id"]).read_bytes(), PDF)
        self.store.delete_pdf(r["id"])
        self.assertIsNone(self.store.pdf_path(r["id"]))

    def test_written_files_are_readable_by_the_backup_user(self):
        r = self.store.create()
        self.store.set_pdf(r["id"], PDF)
        self.store.save(r["id"], {"title": "x"})
        folder = self.data / "reviews" / r["id"]
        for name in ("paper.pdf", "review.json", "map.map"):
            self.assertEqual((folder / name).stat().st_mode & 0o777, 0o644, name)

    def test_non_pdf_and_oversize_rejected_keep_old(self):
        r = self.store.create()
        self.store.set_pdf(r["id"], PDF)
        with self.assertRaises(ValueError):
            self.store.set_pdf(r["id"], b"<html>nope</html>")
        Store.MAX_PDF, old = 10, Store.MAX_PDF
        try:
            with self.assertRaises(ValueError):
                self.store.set_pdf(r["id"], PDF)
        finally:
            Store.MAX_PDF = old
        self.assertEqual(self.store.pdf_path(r["id"]).read_bytes(), PDF)


class TestZip(StoreCase):
    def _zip(self, members):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, d in members.items():
                z.writestr(n, d)
        return buf.getvalue()

    def test_zip_roundtrip_new_id_on_collision(self):
        r = self.store.create("T")
        self.store.set_pdf(r["id"], PDF)
        self.store.set_map(r["id"], GOOD_MAP)
        self.store.save(r["id"], {"values": {"a": "hello"}})
        blob = self.store.export_zip(r["id"])
        a = self.store.import_zip(blob)
        b = self.store.import_zip(blob)
        self.assertEqual(len({r["id"], a["id"], b["id"]}), 3)
        self.assertEqual(len(list((self.data / "reviews").iterdir())), 3)
        self.assertEqual(self.store.get(a["id"])["values"], {"a": "hello"})
        self.assertEqual(self.store.map_text(a["id"]), GOOD_MAP)
        self.assertEqual(self.store.pdf_path(a["id"]).read_bytes(), PDF)
        self.assertEqual(self.store.get(r["id"])["values"], {"a": "hello"})

    def test_import_zip_rejects_traversal_and_extra_members(self):
        base = json.dumps({"id": "z1", "values": {}})
        for name in ("../evil", "/abs", "x.txt", "sub/review.json"):
            with self.assertRaises(ValueError, msg=name):
                self.store.import_zip(self._zip({"review.json": base, name: "x"}))
        self.assertEqual(self.store.list(), [])
        self.assertEqual([p.name for p in self.root.iterdir()], ["data"])

    def test_import_zip_rejects_bad_review_json_and_bad_map(self):
        with self.assertRaises(ValueError):
            self.store.import_zip(self._zip({"review.json": "[]"}))
        with self.assertRaises(ValueError):
            self.store.import_zip(self._zip({"review.json": json.dumps({"values": {}}), "map.map": "nope"}))
        with self.assertRaises(ValueError):
            self.store.import_zip(b"not a zip")
        self.assertEqual(self.store.list(), [])

    def test_import_zip_rejects_oversized_member(self):
        Store.MAX_PDF, old = 20, Store.MAX_PDF
        try:
            with self.assertRaises(ValueError):
                self.store.import_zip(self._zip({"review.json": json.dumps({"values": {}}), "paper.pdf": PDF + b"x" * 100}))
        finally:
            Store.MAX_PDF = old
        self.assertEqual(self.store.list(), [])

    def test_import_review_json_uses_default_map(self):
        r = self.store.import_review_json({"id": "old1", "title": "Old", "values": {"summary": "s"}, "map": "gone"})
        self.assertEqual(r["id"], "old1")
        self.assertEqual(self.store.map_text("old1"), DEFAULT_MAP.read_text())
        with self.assertRaises(ValueError):
            self.store.import_review_json({"nope": 1})


class TestMigration(StoreCase):
    def test_migrate_legacy_is_idempotent_and_non_destructive(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        a = json.dumps({"id": "leg1", "map": "aamas2027", "title": "A", "values": {"summary": "x"}, "paperFile": "p.pdf"})
        b = json.dumps({"id": "leg2", "map": "no_such_map_any_more", "title": "B", "values": {}})
        (legacy / "leg1.json").write_text(a)
        (legacy / "leg2.json").write_text(b)
        (legacy / "broken.json").write_text("{not json")
        self.assertEqual(sorted(self.store.migrate_legacy(legacy)), ["leg1", "leg2"])
        self.assertEqual(self.store.get("leg1")["values"], {"summary": "x"})
        self.assertEqual(self.store.get("leg1")["paperFile"], "p.pdf")
        self.assertEqual(self.store.map_text("leg2"), DEFAULT_MAP.read_text())
        self.assertEqual((legacy / "leg1.json").read_text(), a)  # untouched
        self.assertEqual(self.store.migrate_legacy(legacy), [])  # idempotent
        self.assertEqual(self.store.migrate_legacy(self.root / "missing"), [])


if __name__ == "__main__":
    unittest.main()
