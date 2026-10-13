import unittest
from unittest.mock import Mock, patch

from medrax.tools.rag_tool import MedicalEvidenceRAGTool


class StubRAG:
    def retrieve_evidence(self, request):
        return {
            "retrieval_id": "retrieval-test",
            "query_plan": {"original_question": request["question"]},
            "ranked_documents": [{
                "score": 0.91,
                "citation_id": "retrieval-test:cit-1",
                "document": {
                    "source_id": "pubmed-live",
                    "document_id": "pmid-12345678",
                    "kind": "pubmed",
                    "title": "Example study",
                    "text": "Retrieved evidence.",
                    "published_at": "2025-01-01",
                    "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                    "pmid": "12345678",
                    "doi": "10.1000/example",
                },
            }],
            "citations": [{
                "citation_id": "retrieval-test:cit-1",
                "document_id": "pmid-12345678",
                "pmid": "12345678",
                "doi": "10.1000/example",
                "locator": "Abstract",
            }],
            "warnings": [],
            "trace": {"connector_trace": [{"source_id": "pubmed-live", "status": "ok"}]},
        }


class MedicalEvidenceRAGToolTest(unittest.TestCase):
    @patch("medrax.tools.rag_tool._get_rag", return_value=StubRAG())
    def test_normalizes_rag_package_for_agent(self, get_rag):
        result = MedicalEvidenceRAGTool().invoke({
            "name": "retrieve_medical_evidence",
            "args": {"question": "hypertension treatment"},
            "id": "rag-call",
            "type": "tool_call",
        })

        self.assertIn("PMID:12345678", result.content)
        self.assertEqual(result.artifact["retrieval_id"], "retrieval-test")
        self.assertEqual(result.artifact["tool_args"]["top_k"], 8)
        evidence = result.artifact["evidence"][0]
        self.assertEqual(evidence["source_type"], "pubmed")
        self.assertEqual(evidence["source_id"], "PMID:12345678")
        self.assertEqual(evidence["metadata"]["citation_id"], "retrieval-test:cit-1")

    @patch("main.create_agent")
    @patch("main._create_qwen_model", return_value=Mock())
    def test_medical_agent_registers_rag_his_and_citation_tools(self, model, create_agent):
        from main import initialize_medical_agent

        create_agent.return_value = Mock()
        _, tools = initialize_medical_agent("medrax/docs/system_prompts.txt")

        self.assertEqual(
            [tool.name for tool in tools.values()],
            ["retrieve_medical_evidence", "get_patient_snapshot", "open_medical_citation"],
        )

    @patch("medrax.tools.rag_tool._get_rag", return_value=StubRAG())
    def test_user_terms_is_not_exposed_and_stale_parameter_cannot_abort(self, get_rag):
        tool = MedicalEvidenceRAGTool()
        self.assertNotIn("user_terms", tool.args_schema.model_json_schema()["properties"])
        result = tool.invoke({
            "name": tool.name, "type": "tool_call", "id": "stale-call",
            "args": {"question": "哮喘治疗", "user_terms": {"未知词": ["model guess"]}},
        })
        self.assertNotIn("未知词", result.artifact["tool_args"]["user_terms"])
        self.assertIn("translate", result.artifact["tool_args"]["retrieval_method"])


if __name__ == "__main__":
    unittest.main()
