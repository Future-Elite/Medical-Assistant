"""Server boundary: credential redaction and bounded retrieval-tool cache."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from rag import server as server_module


class SafeErrorMessageTests(unittest.TestCase):
    def test_redacts_all_configured_keys_and_api_key_parameters(self):
        env = {
            "OPENAI_API_KEY": "openai-secret",
            "RAG_NCBI_API_KEY": "ncbi-secret",
            "OPENFDA_API_KEY": "openfda-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            message = server_module._safe_error_message(RuntimeError(
                "openai-secret ncbi-secret openfda-secret "
                "https://example.test/?api_key=openfda-secret&x=1"
            ))
        self.assertNotIn("openai-secret", message)
        self.assertNotIn("ncbi-secret", message)
        self.assertNotIn("openfda-secret", message)
        self.assertIn("[REDACTED]", message)


class RetrievalToolCacheTests(unittest.TestCase):
    def test_cache_is_bounded_and_evicts_oldest(self):
        handler = server_module.RAGRequestHandler
        original_tools = handler.retrieval_tools
        original_limit = server_module.MAX_RETRIEVAL_TOOLS
        handler.retrieval_tools = {}
        server_module.MAX_RETRIEVAL_TOOLS = 2
        try:
            for index in range(3):
                handler._remember_retrieval_tool(f"retrieval-{index}", object())
            self.assertEqual(list(handler.retrieval_tools), ["retrieval-1", "retrieval-2"])
        finally:
            handler.retrieval_tools = original_tools
            server_module.MAX_RETRIEVAL_TOOLS = original_limit


if __name__ == "__main__":
    unittest.main()
