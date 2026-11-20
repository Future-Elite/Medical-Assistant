"""Configuration loader: .env parsing, precedence and typed validation."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from rag.core import config as config_module


class EnvFileParsingTests(unittest.TestCase):
    def test_export_inline_comments_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "export RAG_SERVER_HOST=0.0.0.0\n"
                "RAG_SERVER_PORT=8798  # inline comment\n"
                "OPENAI_MODEL=\"model # part of value\"\n"
                "RAG_NCBI_EMAIL='a@b.c'\n",
                encoding="utf-8",
            )
            values = config_module._load_env_file(path)
        self.assertEqual(values["RAG_SERVER_HOST"], "0.0.0.0")
        self.assertEqual(values["RAG_SERVER_PORT"], "8798")
        self.assertEqual(values["OPENAI_MODEL"], "model # part of value")
        self.assertEqual(values["RAG_NCBI_EMAIL"], "a@b.c")

    def test_missing_file_returns_empty(self):
        self.assertEqual(config_module._load_env_file(Path("/nonexistent/.env")), {})


class TypedValidationTests(unittest.TestCase):
    def test_guideline_selection_k_default_and_override(self):
        with mock.patch.dict(os.environ, {"RAG_GUIDELINE_TOP_K": ""}, clear=False):
            self.assertEqual(config_module.get_config().guideline_selection_k, 5)
        with mock.patch.dict(os.environ, {"RAG_GUIDELINE_TOP_K": "7"}, clear=False):
            self.assertEqual(config_module.get_config().guideline_selection_k, 7)

    def test_environment_overrides_dotenv(self):
        with mock.patch.dict(os.environ, {"RAG_SERVER_PORT": "9000"}, clear=False):
            self.assertEqual(config_module.get_config().server_port, 9000)

    def test_invalid_integer_raises(self):
        with mock.patch.dict(os.environ, {"RAG_SERVER_PORT": "not-a-port"}, clear=False):
            with self.assertRaises(ValueError) as context:
                config_module.get_config()
        self.assertIn("RAG_SERVER_PORT", str(context.exception))

    def test_invalid_float_raises(self):
        with mock.patch.dict(os.environ, {"RAG_REQUEST_TIMEOUT_SECONDS": "slow"}, clear=False):
            with self.assertRaises(ValueError) as context:
                config_module.get_config()
        self.assertIn("RAG_REQUEST_TIMEOUT_SECONDS", str(context.exception))

    def test_invalid_bool_raises(self):
        with mock.patch.dict(os.environ, {"RAG_GUIDELINE_AUTO_PARSE": "maybe"}, clear=False):
            with self.assertRaises(ValueError) as context:
                config_module.get_config()
        self.assertIn("RAG_GUIDELINE_AUTO_PARSE", str(context.exception))


if __name__ == "__main__":
    unittest.main()
