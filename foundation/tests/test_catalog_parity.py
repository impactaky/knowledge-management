from pathlib import Path
import tempfile
import unittest

import indexer
import server
from federation_core import parse_catalog


class CatalogParityTests(unittest.TestCase):
    def test_search_indexer_and_ui_resolve_the_same_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "folder with spaces"
            package.mkdir()
            (package / "INDEX.md").write_text("# Knowledge\n")
            for target in [
                "folder%20with%20spaces/INDEX.md#knowledge",
                "folder%20with%20spaces/",
                str(package / "INDEX.md"),
                (package / "INDEX.md").as_uri(),
            ]:
                with self.subTest(target=target):
                    catalog = root / "CATALOG.md"
                    catalog.write_text(
                        "# CATALOG\n## パッケージ\n"
                        f"  - [renamed]({target}) — Scope\n"
                        "## Other\n- [not-a-package](missing/INDEX.md) — Ignore\n"
                    )
                    expected = [
                        {"name": p.name, "description": p.description,
                         "entry_path": p.entry_path, "root": p.root, "publication_root": root}
                        for p in parse_catalog(catalog)
                    ]
                    self.assertEqual(len(expected), 1)
                    self.assertEqual(expected[0]["root"], package)
                    self.assertEqual(expected[0]["entry_path"],
                                     package if target.endswith("/") else package / "INDEX.md")
                    self.assertEqual(indexer.parse_catalog(catalog), expected)
                    self.assertEqual(server.parse_catalog_packages(catalog), expected)


if __name__ == "__main__":
    unittest.main()
