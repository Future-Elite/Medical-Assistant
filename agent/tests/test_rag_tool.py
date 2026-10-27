import unittest
from unittest.mock import Mock, patch

from medrax.tools.rag_tool import MedicalEvidenceRAGTool
from medrax.medical_session import ACTIVE_TURN, MedicalSession, TurnContext


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
            "trace": {
                "candidate_count": 12,
                "selected_count": 1,
                "connector_trace": [{
                    "source_id": "pubmed-live", "kind": "pubmed", "status": "ok",
                    "query": '("hypertension")', "returned": 12,
                }],
            },
        }


class MedicalEvidenceRAGToolTest(unittest.TestCase):
    def test_turn_summary_separates_current_answer_from_history(self):
        historical = {
            "retrieval_id": "retrieval-old",
            "evidence": [{
                "source_type": "pubmed", "source_id": "PMID:999", "title": "Old",
                "content": "Old evidence", "date": "2020", "url": "",
                "metadata": {"citation_id": "retrieval-old:cit-1", "document_id": "old"},
            }],
        }
        session = MedicalSession(retrievals={"retrieval-old": historical})
        turn = TurnContext(session, "Find current evidence", lambda event: None)
        token = ACTIVE_TURN.set(turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=StubRAG()):
                content, artifact = MedicalEvidenceRAGTool()._run(search_query="hypertension")
        finally:
            ACTIVE_TURN.reset(token)

        summary = artifact["turn_retrieval_summary"]
        self.assertEqual(summary["answer_evidence_scope"], "current_turn")
        self.assertEqual(summary["current_answer_evidence_count"], 1)
        self.assertEqual(summary["current_answer_usable_evidence_count"], 1)
        self.assertEqual(summary["historical_candidate_count"], 1)
        self.assertEqual(summary["current_answer_citation_ids"], ["retrieval-test:cit-1"])
        self.assertNotIn("retrieval-old:cit-1", content)

    def test_turn_summary_keeps_earlier_results_after_an_empty_retry(self):
        rag = StubRAG()
        first = rag.retrieve_evidence({"question": "hypertension"})
        empty = {
            **first,
            "retrieval_id": "retrieval-empty",
            "ranked_documents": [],
            "citations": [],
            "trace": {
                "candidate_count": 0,
                "selected_count": 0,
                "connector_trace": [{
                    "source_id": "pubmed-live", "kind": "pubmed", "status": "ok",
                    "query": '("nifedipine")', "returned": 0,
                }],
            },
        }
        rag.retrieve_evidence = Mock(side_effect=[first, empty])
        turn = TurnContext(MedicalSession(), "Find hypertension evidence", lambda event: None)
        token = ACTIVE_TURN.set(turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
                tool = MedicalEvidenceRAGTool()
                tool._run(search_query="hypertension")
                content, artifact = tool._run(
                    search_query="nifedipine", evidence_gap="Need drug-specific evidence",
                    adjustment_reason="Target nifedipine studies",
                )
        finally:
            ACTIVE_TURN.reset(token)

        summary = artifact["turn_retrieval_summary"]
        self.assertEqual([row["connectors"][0]["returned"] for row in summary["attempts"]], [12, 0])
        self.assertTrue(summary["any_connector_results_returned"])
        self.assertEqual(summary["returned_across_attempts_by_source"]["pubmed-live"], 12)
        self.assertEqual(summary["unique_selected_by_source"]["pubmed"], 1)
        self.assertIn('"any_connector_results_returned": true', content)

    def test_publication_range_is_structured_logged_and_validated(self):
        rag = StubRAG()
        rag.retrieve_evidence = Mock(wraps=rag.retrieve_evidence)
        turn = TurnContext(MedicalSession(), "recent hypertension evidence", lambda event: None)
        token = ACTIVE_TURN.set(turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
                _, artifact = MedicalEvidenceRAGTool()._run(
                    search_query="primary hypertension",
                    publication_from="2024-10-13",
                    publication_to="2026-10-13",
                )
        finally:
            ACTIVE_TURN.reset(token)
        request = rag.retrieve_evidence.call_args.args[0]
        self.assertEqual(request["question"], "primary hypertension")
        self.assertEqual(request["filters"], {
            "publication_from": "2024-10-13", "publication_to": "2026-10-13",
        })
        self.assertEqual(artifact["tool_args"]["publication_from"], "2024-10-13")
        self.assertEqual(artifact["turn_retrieval_summary"]["attempts"][0]["publication_to"], "2026-10-13")
        with self.assertRaisesRegex(ValueError, "must not be later"):
            MedicalEvidenceRAGTool()._run(
                search_query="primary hypertension",
                publication_from="2026-10-13",
                publication_to="2024-10-13",
            )

    def test_query_separation_retry_reasons_and_turn_scoped_reuse(self):
        rag = StubRAG()
        rag.retrieve_evidence = Mock(wraps=rag.retrieve_evidence)
        events = []
        original = "读取患者用药和血钾，并检索高血压研究"
        turn = TurnContext(MedicalSession(as_of="2025-12-31"), original, events.append)
        token = ACTIVE_TURN.set(turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
                tool = MedicalEvidenceRAGTool()
                _, first = tool._run(search_query="hypertension treatment")
                self.assertEqual(rag.retrieve_evidence.call_args.args[0]["question"], "hypertension treatment")
                self.assertEqual(first["original_question"], original)
                self.assertIn("translate", first["tool_args"]["retrieval_method"])
                _, reused = tool._run(search_query=" hypertension   treatment ")
                self.assertTrue(reused["cache_hit"])
                self.assertEqual(first["retrieval_id"], reused["retrieval_id"])
                self.assertEqual(rag.retrieve_evidence.call_count, 1)
                _, rejected = tool._run(search_query="hypertension")
                self.assertEqual(rejected["error"], "retrieval_adjustment_required")
                self.assertEqual(rag.retrieve_evidence.call_count, 1)
                self.assertEqual([e["event"] for e in events], ["evidence_search", "evidence_reused", "retrieval_rejected"])
        finally:
            ACTIVE_TURN.reset(token)
        next_turn = TurnContext(turn.session, original, events.append)
        token = ACTIVE_TURN.set(next_turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
                tool._run(search_query="hypertension treatment")
                _, broader = tool._run(search_query="hypertension", evidence_gap="No applicable treatment studies",
                                       adjustment_reason="Remove treatment restriction to broaden recall")
                self.assertEqual(rag.retrieve_evidence.call_count, 3)
                self.assertEqual(broader["search_query"], "hypertension")
                self.assertEqual(broader["tool_args"]["original_question"], original)
                summary = broader["turn_retrieval_summary"]
                self.assertEqual(summary["attempt_count"], 2)
                self.assertTrue(summary["any_connector_results_returned"])
                self.assertEqual(summary["returned_across_attempts_by_source"]["pubmed-live"], 24)
                self.assertEqual(summary["attempts"][0]["connectors"][0]["query"], '("hypertension")')
        finally:
            ACTIVE_TURN.reset(token)

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

    def test_preserves_same_paper_fragments_and_emits_compact_applicability_cards(self):
        rag = StubRAG()
        package = rag.retrieve_evidence({"question": "neonatal pneumonia"})
        first = package["ranked_documents"][0]
        first["document"]["population"] = "newborn infants"
        first["document"]["metadata"] = {"content_mode": "abstract"}
        package["applicability"] = [{
            "document_id": "pmid-12345678", "status": "possible_match",
            "reason": "Population field matches the patient context.",
        }]
        package["ranked_documents"].append({
            "score": 0.88, "citation_id": "retrieval-test:cit-2",
            "document": {**first["document"], "document_id": "pmid-12345678-results",
                         "text": "A distinct results passage."},
        })
        package["citations"].append({
            "citation_id": "retrieval-test:cit-2", "document_id": "pmid-12345678-results",
            "pmid": "12345678", "locator": "Results",
        })
        rag.retrieve_evidence = Mock(return_value=package)
        with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
            content, artifact = MedicalEvidenceRAGTool()._run(search_query="neonatal pneumonia")

        self.assertEqual(len(artifact["evidence"]), 2)
        self.assertEqual([row["source_id"] for row in artifact["evidence"]],
                         ["PMID:12345678", "PMID:12345678"])
        metadata = artifact["evidence"][0]["metadata"]
        self.assertEqual(metadata["question_relevance"], "selected_for_query")
        self.assertEqual(metadata["population_applicability"], "possible_match")
        self.assertIn("Evidence card 1", content)
        self.assertIn("Population: newborn infants", content)
        self.assertIn("Population applicability: possible_match", content)
        self.assertNotIn('"score"', content)

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
