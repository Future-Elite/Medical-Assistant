"""Page-marker alignment for MinerU Markdown (offline, supplied page texts)."""
from __future__ import annotations

import unittest
from pathlib import Path

from guideline_parsing.page_markers import add_page_markers


class PageMarkerTests(unittest.TestCase):
    def test_existing_markers_are_preserved(self):
        text = "<!-- page: 1 -->\n\n# Title\n\nBody text."
        self.assertEqual(add_page_markers(text, Path("missing.pdf"), pages=["Title\n\nBody text."]), text)

    def test_blocks_get_pages_from_supplied_text(self):
        markdown = "# Title\n\nAlpha block.\n\nBeta block.\n\nGamma block.\n"
        pages = [
            "Title\n\nAlpha block.",
            "Beta block.\n\nGamma block.",
        ]
        output = add_page_markers(markdown, Path("guide.pdf"), pages=pages)
        self.assertIn("<!-- page: 1 -->\n\n# Title", output)
        self.assertIn("<!-- page: 2 -->\n\nBeta block.", output)
        self.assertEqual(output.count("<!-- page: 2 -->"), 1)

    def test_unmatched_block_keeps_previous_page(self):
        markdown = "# Title\n\nAlpha block.\n\nUnmatched text.\n"
        output = add_page_markers(
            markdown,
            Path("guide.pdf"),
            pages=["Title\n\nAlpha block."],
        )
        self.assertNotIn("<!-- page: 2 -->", output)
        self.assertEqual(output.count("<!-- page: 1 -->"), 1)


if __name__ == "__main__":
    unittest.main()
