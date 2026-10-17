import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from medrax.medical_session import ACTIVE_TURN, MedicalSession, TurnContext, needs_patient_context
from medrax.query_converter import QwenQueryConverter
from medrax.tools.rag_tool import MedicalEvidenceRAGTool
from rag.guidelines import GuidelineMetadataConnector, who_guideline_metadata_registry
from rag.models import QueryPlan
from rag.pubmed import _safe_term


class EvidencePresentationTest(unittest.TestCase):
    def test_general_literature_does_not_require_patient(self):
        self.assertFalse(needs_patient_context("总结新生儿感染性肺炎治疗的最新研究成果"))
        self.assertTrue(needs_patient_context("结合该患者的病历分析用药"))

    def test_concept_groups_reach_pubmed_without_becoming_wordwise_and(self):
        converter = QwenQueryConverter()
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='{"concepts":[["pneumonia"],["neonatal","newborn"],["treatment","therapy"]]}')
        converter.client._get_model = Mock(return_value=model)
        converted = converter.convert("neonatal pneumonia treatment")
        plan = QueryPlan("neonatal pneumonia treatment", (), (), (), {}, (), machine_translations=(converted,))
        self.assertEqual(_safe_term(plan), '("pneumonia") AND ("neonatal" OR "newborn") AND ("treatment" OR "therapy")')

    def test_who_scope_filters_topic_and_population(self):
        connector = GuidelineMetadataConnector("who-guideline-live", who_guideline_metadata_registry())
        for query, expected in (("neonatal pneumonia treatment", 0), ("neonatal hypertension treatment", 0), ("adult hypertension treatment", 1)):
            plan = QueryPlan(query, (query,), (), (), {}, ())
            with self.subTest(query=query):
                self.assertEqual(len(connector.search(plan, limit=8).documents), expected)

    def test_model_view_hides_trace_and_general_query_omits_cached_patient(self):
        from test_rag_tool import StubRAG
        rag = StubRAG()
        package = rag.retrieve_evidence({"question": "pneumonia"})
        package["trace"]["internal_secret"] = "debug-marker"
        package["warnings"] = ["NCBI email debug-marker"]
        rag.retrieve_evidence = Mock(return_value=package)
        turn = TurnContext(MedicalSession(snapshot={"facts": ["private-patient"]}),
                           "总结新生儿肺炎治疗研究", lambda event: None)
        token = ACTIVE_TURN.set(turn)
        try:
            with patch("medrax.tools.rag_tool._get_rag", return_value=rag):
                content, artifact = MedicalEvidenceRAGTool()._run(search_query="neonatal pneumonia treatment")
        finally:
            ACTIVE_TURN.reset(token)
        self.assertNotIn("patient_snapshot", rag.retrieve_evidence.call_args.args[0])
        self.assertNotIn("debug-marker", content)
        self.assertIn("debug-marker", str(artifact["trace"]))
        self.assertIn("retrieval-test:cit-1", content)
        self.assertIn("Retrieved evidence.", content)


if __name__ == "__main__":
    unittest.main()
