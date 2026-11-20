"""MinerU executable resolution and timeout handling."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from guideline_parsing import pdf_to_markdown


class MineruExecutableTests(unittest.TestCase):
    def test_explicit_executable_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "mineru-kit"
            with self.assertRaises(RuntimeError):
                pdf_to_markdown._mineru_executable(missing)
            executable = Path(directory) / "mineru-kit"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            self.assertEqual(pdf_to_markdown._mineru_executable(executable), str(executable))


class ConvertPdfTimeoutTests(unittest.TestCase):
    def test_timeout_is_reported_as_runtime_error(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "guide.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            markdown_path = Path(directory) / "guide.md"
            with mock.patch.object(pdf_to_markdown, "_mineru_executable", return_value="mineru-kit"):
                with mock.patch.object(
                    pdf_to_markdown.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("mineru-kit", 1),
                ):
                    with self.assertRaises(RuntimeError) as context:
                        pdf_to_markdown.convert_pdf(pdf_path, markdown_path)
            self.assertIn("超时", str(context.exception))


if __name__ == "__main__":
    unittest.main()
