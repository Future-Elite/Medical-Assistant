"""API naming guard: no internal codenames, lowercase kebab-case paths."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server.py"


class ApiNamingTests(unittest.TestCase):
    def test_public_paths_are_lowercase_kebab_case_and_have_no_codename(self):
        text = SERVER.read_text(encoding="utf-8")
        paths = set(re.findall(r'"(/api/[^"]*)"', text))
        self.assertTrue(paths)
        for path in paths:
            base = path.split("?", 1)[0]
            self.assertNotIn("dreamer", base.lower(), base)
            for segment in base.split("/"):
                if not segment or segment.endswith("...") or segment.startswith("{"):
                    continue
                self.assertRegex(segment, r"^[a-z0-9-]+$", f"non-kebab path segment: {path}")

    def test_no_legacy_project_namespace_is_reachable(self):
        text = SERVER.read_text(encoding="utf-8")
        self.assertNotIn("/api/dreamer", text)
        self.assertNotIn("/api/v2", text)


if __name__ == "__main__":
    unittest.main()
