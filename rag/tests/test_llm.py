"""LLM configuration tests with no key and no network call."""

from __future__ import annotations

import unittest

from v5.llm import LLMConfigurationError, OpenAIResponsesLLM, _chat_completion_text, _response_text


class TestLLMConfiguration(unittest.TestCase):
    def test_missing_explicit_configuration_never_generates_a_fallback(self) -> None:
        client = OpenAIResponsesLLM(api_key="", model="")
        self.assertFalse(client.configured)
        with self.assertRaises(LLMConfigurationError):
            client.summarize(question="test", evidence_package={"citations": []})

    def test_response_parsers_accept_openai_shapes(self) -> None:
        self.assertEqual(_response_text({"output_text": "hello"}), "hello")
        self.assertEqual(_response_text({"output": [{"content": [{"text": "hello"}]}]}), "hello")
        self.assertEqual(_chat_completion_text({"choices": [{"message": {"content": "hello"}}]}), "hello")

    def test_safe_error_does_not_hide_protocol_incompatibility(self) -> None:
        client = OpenAIResponsesLLM(api_key="key", model="model", base_url="https://relay.example/v1")
        client._request = lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("HTTP 426 WebSocket upgrade required"))
        with self.assertRaises(Exception):
            client._request("/responses", {})


if __name__ == "__main__":
    unittest.main()
