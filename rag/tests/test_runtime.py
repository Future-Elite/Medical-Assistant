import unittest

from rag.runtime import create_evidence_rag


class RuntimeTest(unittest.TestCase):
    def test_current_runtime_registers_pubmed_once(self):
        rag = create_evidence_rag()
        self.assertEqual(
            rag.pipeline.registry.describe(),
            [{"source_id": "pubmed-live", "kinds": ["pubmed"]}],
        )


if __name__ == "__main__":
    unittest.main()
