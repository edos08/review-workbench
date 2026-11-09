"""Every bundled map (the default and everything in examples/) must parse."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mapparser

FILES = sorted((ROOT / "defaults").glob("*.map")) + sorted((ROOT / "examples").glob("*.map"))


class TestBundledMaps(unittest.TestCase):
    def test_there_are_examples(self):
        self.assertGreaterEqual(len(list((ROOT / "examples").glob("*.map"))), 3)

    def test_every_map_parses_and_has_a_submittable_field(self):
        for f in FILES:
            with self.subTest(map=f.name):
                tree = mapparser.parse(f.read_text(encoding="utf-8"))
                self.assertTrue(tree["attrs"].get("label"), "Sitemap needs a label")
                fields = []

                def walk(n):
                    if n["type"] in mapparser.FIELD_TYPES:
                        fields.append(n)
                    if n["type"] != "Choice":
                        for c in n["children"]:
                            walk(c)
                walk(tree)
                self.assertTrue(any(not f["attrs"].get("private") for f in fields))

    def test_maps_contain_no_local_paths_or_email_addresses(self):
        import re
        for f in FILES:
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\|\S+@\S+\.\S+", text), f.name)


if __name__ == "__main__":
    unittest.main()
