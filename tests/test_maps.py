"""Every bundled map template (maps/*.map) must parse and carry the catalogue metadata the home page shows."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mapparser
from store import ID_RE, Store

FILES = sorted((ROOT / "maps").glob("*.map"))


def fields(node):
    if node["type"] in mapparser.FIELD_TYPES:
        yield node
    if node["type"] != "Choice":
        for c in node["children"]:
            yield from fields(c)


class TestBundledMaps(unittest.TestCase):
    def test_the_library_is_there_and_has_the_two_special_templates(self):
        names = {f.stem for f in FILES}
        self.assertGreaterEqual(len(names), 20)
        self.assertIn(Store.DEFAULT_TEMPLATE, names)
        self.assertIn(Store.LEGACY_TEMPLATE, names)
        for f in FILES:
            self.assertRegex(f.stem, ID_RE, f.name)
            self.assertEqual(f.stem, f.stem.lower(), f.name)

    def test_every_map_parses_and_has_catalogue_metadata(self):
        for f in FILES:
            with self.subTest(map=f.name):
                a = mapparser.parse(f.read_text(encoding="utf-8"))["attrs"]
                self.assertTrue(a.get("label"), "label")
                self.assertTrue(a.get("venue"), "venue")
                self.assertIn(a.get("kind"), ("conference", "journal", "general"))
                self.assertIn(a.get("fidelity"), ("form", "guidelines", "generic"))
                self.assertTrue(0 < len(a.get("description", "")) <= 300, "description 1-300 chars")
                if a["kind"] != "general":
                    self.assertIsInstance(a.get("year"), int)
                    self.assertTrue(str(a.get("source", "")).startswith("https://"), "source url")

    def test_forms_are_usable(self):
        for f in FILES:
            with self.subTest(map=f.name):
                tree = mapparser.parse(f.read_text(encoding="utf-8"))
                fs = list(fields(tree))
                self.assertTrue(any(not n["attrs"].get("private") for n in fs), "needs a field that is exported")
                self.assertTrue(any(n["attrs"].get("required") for n in fs), "needs at least one required field")
                for n in fs:
                    if n["type"] == "Choice":
                        values = [str(o["attrs"].get("value")) for o in n["children"]]
                        self.assertGreaterEqual(len(values), 2, n["attrs"]["id"])
                        self.assertEqual(len(values), len(set(values)), f"duplicate option values in {n['attrs']['id']}")
                        for o in n["children"]:
                            self.assertTrue(o["attrs"].get("label"), n["attrs"]["id"])

    def test_maps_contain_no_local_paths_or_email_addresses(self):
        for f in FILES:
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\|\S+@\S+\.\S+", text), f.name)

    def test_venue_maps_say_how_far_to_trust_them(self):
        """A map that is not copied from the published form must tell the user to check the real one."""
        for f in FILES:
            tree = mapparser.parse(f.read_text(encoding="utf-8"))
            if tree["attrs"]["fidelity"] != "guidelines":
                continue
            infos = " ".join(str(c["attrs"].get("text", "")) for c in tree["children"] if c["type"] == "Info")
            self.assertRegex(infos, r"(?i)could not be verified|differ|check", f.name)


if __name__ == "__main__":
    unittest.main()
