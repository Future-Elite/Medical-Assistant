import tempfile
import unittest
from pathlib import Path

from medrax.utils import load_prompts_from_file


class PromptLoaderTest(unittest.TestCase):
    def test_only_fixed_sections_are_headers_and_content_is_preserved(self):
        content = (
            "[MEDICAL_ASSISTANT]\n"
            "Medical line\n"
            "\n"
            "[GENERAL_ASSISTANT]\n"
            "  Keep indentation  \n"
            "\n"
            "[MEDICAL_EVIDENCE_ASSISTANT]\n"
            "## Citations\n"
            "\n"
            "[retrieval-test:cit-1]\n"
            "- keep **Markdown**\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompts.txt"
            path.write_text(content, encoding="utf-8")
            prompts = load_prompts_from_file(str(path))

        self.assertEqual(
            list(prompts),
            ["MEDICAL_ASSISTANT", "GENERAL_ASSISTANT", "MEDICAL_EVIDENCE_ASSISTANT"],
        )
        self.assertEqual(prompts["MEDICAL_ASSISTANT"], "Medical line\n\n")
        self.assertEqual(prompts["GENERAL_ASSISTANT"], "  Keep indentation  \n\n")
        self.assertEqual(
            prompts["MEDICAL_EVIDENCE_ASSISTANT"],
            "## Citations\n\n[retrieval-test:cit-1]\n- keep **Markdown**\n",
        )


if __name__ == "__main__":
    unittest.main()
