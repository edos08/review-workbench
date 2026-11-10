import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mapparser
from store import Store

BUNDLED = ROOT / "maps"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
GOOD_MAP = 'Sitemap label="Tiny" venue="Mine" kind=journal {\n  Text id=a label="A" required\n  Text id=b label="B"\n}\n'


def text_of(name):
    return (BUNDLED / f"{name}.map").read_text(encoding="utf-8")


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.store = Store(self.data, BUNDLED)


class TestTemplates(StoreCase):
    def test_bundled_templates_listed_with_metadata(self):
        ts = {t["id"]: t for t in self.store.templates()}
        self.assertGreaterEqual(len(ts), 20)
        for tid in ("aamas-2027", "neurips-2025", "icml-2025", "iclr-2025", "tmlr", "blank"):
            self.assertIn(tid, ts)
        for t in ts.values():
            self.assertFalse(t["custom"], t["id"])
            self.assertIn(t["kind"], ("conference", "journal", "general"), t["id"])
            self.assertTrue(t["label"] and t["venue"], t["id"])
        self.assertEqual(ts["aamas-2027"]["label"], "AAMAS 2027 - Official Review")
        self.assertEqual(ts["aamas-2027"]["kind"], "conference")
        self.assertGreater(ts["neurips-2025"]["fields"], 10)
        self.assertGreater(ts["neurips-2025"]["required"], 5)

    def test_custom_template_lifecycle_and_override(self):
        self.store.set_custom_template("my-venue", GOOD_MAP)
        ts = {t["id"]: t for t in self.store.templates()}
        self.assertTrue(ts["my-venue"]["custom"])
        self.assertEqual((ts["my-venue"]["fields"], ts["my-venue"]["required"], ts["my-venue"]["kind"]), (2, 1, "journal"))
        self.assertEqual(self.store.template_text("my-venue"), GOOD_MAP)
        # same id as a bundled one: the custom file wins until it is deleted
        self.store.set_custom_template("blank", GOOD_MAP)
        ts = {t["id"]: t for t in self.store.templates()}
        self.assertTrue(ts["blank"]["custom"] and ts["blank"]["overrides"])
        self.assertEqual(self.store.template_text("blank"), GOOD_MAP)
        self.store.delete_custom_template("blank")
        self.assertEqual(self.store.template_text("blank"), text_of("blank"))
        self.assertFalse({t["id"]: t for t in self.store.templates()}["blank"]["custom"])
        with self.assertRaises(FileNotFoundError):
            self.store.delete_custom_template("blank")  # bundled ones cannot be deleted

    def test_invalid_custom_template_is_rejected_and_nothing_written(self):
        with self.assertRaises(mapparser.MapError):
            self.store.set_custom_template("broken", "Sitemap {{{")
        with self.assertRaises(ValueError):
            self.store.set_custom_template("../evil", GOOD_MAP)
        with self.assertRaises(ValueError):
            self.store.set_custom_template("big", GOOD_MAP + "#" * (1024 * 1024 + 1))
        self.assertEqual(list((self.data / "maps").iterdir()), [])

    def test_hand_dropped_bad_file_is_listed_with_an_error_not_hidden(self):
        (self.data / "maps" / "oops.map").write_text("Sitemap {{{")
        ts = {t["id"]: t for t in self.store.templates()}
        self.assertIn("error", ts["oops"])
        self.assertTrue(ts["oops"]["custom"])
        with self.assertRaises(mapparser.MapError):
            self.store.create("x", template="oops")


class TestReviews(StoreCase):
    def test_create_copies_chosen_template_and_records_it(self):
        r = self.store.create("Paper X", template="icml-2025")
        self.assertEqual((r["title"], r["template"]), ("Paper X", "icml-2025"))
        folder = self.data / "reviews" / r["id"]
        self.assertEqual((folder / "map.map").read_text(), text_of("icml-2025"))
        self.assertEqual(self.store.get(r["id"])["template"], "icml-2025")
        self.assertEqual([x["id"] for x in self.store.list()], [r["id"]])

    def test_create_defaults_to_blank_and_unknown_template_leaves_nothing(self):
        r = self.store.create()
        self.assertEqual(r["template"], "blank")
        with self.assertRaises(FileNotFoundError):
            self.store.create("x", template="no-such-venue")
        with self.assertRaises(ValueError):
            self.store.create("x", template="../x")
        self.assertEqual(len(list((self.data / "reviews").iterdir())), 1)

    def test_set_template_keeps_answers_and_reset_returns_to_it(self):
        r = self.store.create(template="blank")
        self.store.save(r["id"], {"values": {"summary": "kept"}})
        self.store.set_template(r["id"], "neurips-2025")
        self.assertEqual(self.store.map_text(r["id"]), text_of("neurips-2025"))
        got = self.store.get(r["id"])
        self.assertEqual((got["template"], got["values"]), ("neurips-2025", {"summary": "kept"}))
        self.store.set_map(r["id"], GOOD_MAP)
        self.store.reset_map(r["id"])
        self.assertEqual(self.store.map_text(r["id"]), text_of("neurips-2025"))
        with self.assertRaises(FileNotFoundError):
            self.store.set_template(r["id"], "nope")
        self.assertEqual(self.store.map_text(r["id"]), text_of("neurips-2025"))

    def test_reset_map_of_an_old_review_without_a_template(self):
        r = self.store.create()
        p = self.data / "reviews" / r["id"] / "review.json"
        d = json.loads(p.read_text())
        d.pop("template")
        p.write_text(json.dumps(d))
        self.store.set_map(r["id"], GOOD_MAP)
        self.store.reset_map(r["id"])
        self.assertEqual(self.store.map_text(r["id"]), text_of("aamas-2027"))

    def test_list_reports_progress_template_label_and_pdf(self):
        r = self.store.create("P", template="blank")
        item = self.store.list()[0]
        self.assertEqual((item["progress"], item["hasPdf"], item["template"]), ({"filled": 0, "required": 1}, False, "blank"))
        self.assertEqual(item["mapLabel"], "Blank review (free text)")
        self.store.save(r["id"], {"values": {"review": "text", "summary": ""}})
        self.store.set_pdf(r["id"], PDF)
        item = self.store.list()[0]
        self.assertEqual((item["progress"], item["hasPdf"]), ({"filled": 1, "required": 1}, True))

    def test_progress_counts_a_toggled_checkbox_and_ignores_blank_text(self):
        r = self.store.create()
        self.store.set_map(r["id"], 'Sitemap label="T" {\n Text id=a label="A" required\n Checkbox id=c label="C" required\n Text id=z label="Z" required\n}\n')
        self.store.save(r["id"], {"values": {"a": "  ", "c": False}})
        self.assertEqual(self.store.list()[0]["progress"], {"filled": 1, "required": 3})

    def test_a_review_with_a_broken_map_still_lists(self):
        r = self.store.create()
        (self.data / "reviews" / r["id"] / "map.map").write_text("Sitemap {{{")
        item = self.store.list()[0]
        self.assertEqual(item["id"], r["id"])
        self.assertIsNone(item["progress"])

    def test_save_filters_unknown_keys_and_is_atomic(self):
        r = self.store.create()
        self.store.save(r["id"], {"values": {"a": "1"}, "evil": "x", "id": "other", "template": "hack", "annotations": [{"p": 1}]})
        got = self.store.get(r["id"])
        self.assertEqual(got["values"], {"a": "1"})
        self.assertEqual((got["id"], got["template"]), (r["id"], "blank"))
        self.assertNotIn("evil", got)
        self.assertEqual(got["annotations"], [{"p": 1}])
        self.assertEqual([p.name for p in (self.data / "reviews" / r["id"]).iterdir() if p.suffix == ".tmp"], [])

    def test_values_patch_merges_so_stale_writers_do_not_clobber(self):
        r = self.store.create()
        self.store.save(r["id"], {"values": {"a": "1", "b": "2"}})
        self.store.save(r["id"], {"valuesPatch": {"a": "A2"}})
        self.store.save(r["id"], {"valuesPatch": {"c": "3", "b": None}})
        self.assertEqual(self.store.get(r["id"])["values"], {"a": "A2", "c": "3"})
        self.store.save(r["id"], {"pdfState": {"page": 4}})
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
        self.store.set_custom_template("mine", GOOD_MAP)
        folder = self.data / "reviews" / r["id"]
        for p in (folder / "paper.pdf", folder / "review.json", folder / "map.map", self.data / "maps" / "mine.map"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o644, p.name)

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

    def test_zip_roundtrip_new_id_on_collision_and_keeps_template(self):
        r = self.store.create("T", template="tmlr")
        self.store.set_pdf(r["id"], PDF)
        self.store.set_map(r["id"], GOOD_MAP)
        self.store.save(r["id"], {"values": {"a": "hello"}})
        blob = self.store.export_zip(r["id"])
        a = self.store.import_zip(blob)
        b = self.store.import_zip(blob)
        self.assertEqual(len({r["id"], a["id"], b["id"]}), 3)
        self.assertEqual(len(list((self.data / "reviews").iterdir())), 3)
        self.assertEqual(self.store.get(a["id"])["values"], {"a": "hello"})
        self.assertEqual(self.store.get(a["id"])["template"], "tmlr")
        self.assertEqual(self.store.map_text(a["id"]), GOOD_MAP)
        self.assertEqual(self.store.pdf_path(a["id"]).read_bytes(), PDF)
        self.assertEqual(self.store.get(r["id"])["values"], {"a": "hello"})

    def test_import_zip_rejects_traversal_and_extra_members(self):
        base = json.dumps({"id": "z1", "values": {}})
        for name in ("../evil", "/abs", "x.txt", "sub/review.json"):
            with self.assertRaises(ValueError, msg=name):
                self.store.import_zip(self._zip({"review.json": base, name: "x"}))
        self.assertEqual(self.store.list(), [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["data"])

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

    def test_import_review_json_uses_the_legacy_template(self):
        r = self.store.import_review_json({"id": "old1", "title": "Old", "values": {"summary": "s"}, "map": "gone"})
        self.assertEqual((r["id"], r["template"]), ("old1", "aamas-2027"))
        self.assertEqual(self.store.map_text("old1"), text_of("aamas-2027"))
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
        self.assertEqual(self.store.map_text("leg2"), text_of("aamas-2027"))
        self.assertEqual((legacy / "leg1.json").read_text(), a)
        self.assertEqual(self.store.migrate_legacy(legacy), [])
        self.assertEqual(self.store.migrate_legacy(self.root / "missing"), [])


if __name__ == "__main__":
    unittest.main()
