"""Fidelity gate tests: policy mapping, signal application, pipeline integration.

Offline and deterministic. The pipeline-level tests use a translate stub whose
output corrupts a drug entity (mirroring a real opus-mt failure recorded in the
offline translation-quality snapshot from development) so the gate's
downweight/reject paths are exercised end-to-end.
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.fidelity import FidelityLexicon, TermEntry, audit_translation, extract_slots
from rag.gate import FidelityGate, GATE_POLICIES
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.fixtures import SYNTHETIC_PUBMED


def _lexicon() -> FidelityLexicon:
    return FidelityLexicon([
        TermEntry("新生儿呼吸窘迫综合征", ("respiratory distress syndrome",), "disease"),
        TermEntry("氨茶碱", ("aminophylline",), "drug"),
    ])


class TranslateStub:
    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = 0

    def expand(self, plan):
        self.calls += 1
        return (self.mapping.get(plan.original_question, ""),)


def _pipeline(**kwargs):
    registry = ConnectorRegistry()
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry, **kwargs)


QUESTION = "该患儿新生儿呼吸窘迫综合征使用氨茶碱，2月龄，未记录过敏"
BAD_TRANSLATION = "The infant has respiratory distress syndrome and takes dichotomy."
# aminophylline missing -> suspected_drop (drug, L3); 2月龄 missing -> numeric_loss (L2).
GOOD_TRANSLATION = "The infant has respiratory distress syndrome and takes aminophylline, aged 2 months, no recorded allergy."


class GatePolicyTests(unittest.TestCase):
    def test_unknown_policy_rejected(self):
        with self.assertRaises(ValueError):
            FidelityGate(policy="explode")

    def test_penalty_bounds(self):
        with self.assertRaises(ValueError):
            FidelityGate(policy="downweight", penalty=1.5)

    def test_policy_matrix(self):
        slots = extract_slots("新生儿呼吸窘迫综合征使用氨茶碱", lexicon=_lexicon())
        report_translation = audit_translation(slots, ("The infant takes dichotomy.",), _lexicon())
        self.assertEqual(report_translation.level, "L3")

        from rag.fidelity import FidelityReport
        report = FidelityReport(slots=slots, routes=(report_translation,))

        def action(policy):
            decision = FidelityGate(policy=policy).decide(report)[0]
            return decision.action

        self.assertEqual(action("audit"), "record")
        self.assertEqual(action("warn"), "warn")
        self.assertEqual(action("downweight"), "reject")  # L3 under downweight policy -> reject
        self.assertEqual(action("reject"), "reject")

    def test_l2_downweights_under_downweight_policy(self):
        slots = extract_slots("2月龄婴儿发热", lexicon=_lexicon())
        report_l2 = audit_translation(slots, ("Infant with fever.",), _lexicon())
        self.assertEqual(report_l2.level, "L2")
        from rag.fidelity import FidelityReport
        report = FidelityReport(slots=slots, routes=(report_l2,))
        decision = FidelityGate(policy="downweight", penalty=0.5).decide(report)[0]
        self.assertEqual(decision.action, "downweight")
        self.assertEqual(decision.penalty, 0.5)

    def test_gate_policies_constant_matches_models(self):
        self.assertEqual(set(GATE_POLICIES), set(RetrievalRequest.FIDELITY_GATE_POLICIES))

    def test_apply_to_neural_scores_downweight_and_reject(self):
        scores = {"xlingual": {"a": 0.8, "b": 0.4}}
        from rag.fidelity import FidelityReport, RouteAudit
        from rag.gate import GateDecision

        downweight = (GateDecision(route="multilingual_encoding", level="L2", action="downweight", penalty=0.5, reason="t"),)
        adjusted, adjustments = FidelityGate.apply_to_neural_scores(scores, downweight)
        self.assertEqual(adjusted["xlingual"]["a"], 0.4)
        self.assertEqual(adjustments[0]["action"], "downweight")

        reject = (GateDecision(route="multilingual_encoding", level="L3", action="reject", penalty=0.0, reason="t"),)
        adjusted, adjustments = FidelityGate.apply_to_neural_scores(scores, reject)
        self.assertEqual(adjusted["xlingual"], {})
        self.assertEqual(adjustments[0]["action"], "reject")


class PipelineIntegrationTests(unittest.TestCase):
    def test_default_policy_is_off_and_matches_v641_shape(self):
        pipeline = _pipeline(neural_retrievers={"translate": TranslateStub({QUESTION: BAD_TRANSLATION})})
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "retrieval_method": "translate", "top_k": 4,
        }))
        fidelity = package.trace["query_fidelity"]
        self.assertFalse(fidelity["applied"])
        self.assertEqual(fidelity["policy"], "off")
        self.assertIsNone(fidelity["report"])

    def test_audit_policy_records_without_changing_signals(self):
        pipeline = _pipeline(neural_retrievers={"translate": TranslateStub({QUESTION: BAD_TRANSLATION})}, fidelity_lexicon=_lexicon())
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "retrieval_method": "translate", "top_k": 4, "fidelity_gate": "audit",
        }))
        fidelity = package.trace["query_fidelity"]
        self.assertTrue(fidelity["applied"])
        decisions = {item["route"]: item["action"] for item in fidelity["decisions"]}
        self.assertEqual(decisions["translate"], "record")
        report = fidelity["report"]
        self.assertIsNotNone(report)
        translate_route = next(route for route in report["routes"] if route["route"] == "translate")
        self.assertEqual(translate_route["level"], "L3")
        verdicts = {finding["verdict"] for finding in translate_route["findings"] if finding["level"] != "L0"}
        self.assertIn("suspected_drop", verdicts)
        # plan variants still contain the translation (no signal change)
        self.assertTrue(any("dichotomy" in variant for variant in package.query_plan.variants))

    def test_reject_policy_drops_translation_variants_and_keeps_audit_trail(self):
        stub = TranslateStub({QUESTION: BAD_TRANSLATION})
        pipeline = _pipeline(neural_retrievers={"translate": stub}, fidelity_lexicon=_lexicon())
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "retrieval_method": "translate", "top_k": 4, "fidelity_gate": "reject",
        }))
        decisions = {item["route"]: item["action"] for item in package.trace["query_fidelity"]["decisions"]}
        self.assertEqual(decisions["translate"], "reject")
        # scoring variants no longer include the translation
        self.assertFalse(any("dichotomy" in variant for variant in package.query_plan.variants))
        # audit trail keeps the fact: expansion_trace still records the translation, marked rejected
        entry = next(item for item in package.query_plan.expansion_trace if item.get("source") == "machine_translation_zh_en")
        self.assertEqual(entry.get("fidelity_gate"), "rejected")
        # and it is visible in warnings
        self.assertTrue(any("查询保真门控" in warning and "reject" in warning for warning in package.warnings))
        # method trace marks the translate route as gated
        method_entry = next(item for item in package.trace["method_trace"] if item.get("method") == "translate")
        self.assertEqual(method_entry.get("fidelity_gate"), "rejected")

    def test_good_translation_passes_under_reject_policy(self):
        pipeline = _pipeline(neural_retrievers={"translate": TranslateStub({QUESTION: GOOD_TRANSLATION})}, fidelity_lexicon=_lexicon())
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "retrieval_method": "translate", "top_k": 4, "fidelity_gate": "reject",
        }))
        decisions = {item["route"]: item["action"] for item in package.trace["query_fidelity"]["decisions"]}
        self.assertEqual(decisions["translate"], "pass")
        self.assertTrue(any("aminophylline" in variant for variant in package.query_plan.variants))

    def test_reviewed_mapping_action_demoted_to_warn(self):
        """The quality-upper-bound branch is never down-weighted/rejected in v0."""
        pipeline = _pipeline(fidelity_lexicon=_lexicon())
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "english_terms": ["respiratory distress syndrome"],  # aminophylline missing -> L3 drop
            "top_k": 4, "fidelity_gate": "reject",
        }))
        decisions = {item["route"]: item["action"] for item in package.trace["query_fidelity"]["decisions"]}
        self.assertEqual(decisions["reviewed_mapping"], "warn")
        # documented demotion is visible in the decision reason
        reason = next(item["reason"] for item in package.trace["query_fidelity"]["decisions"] if item["route"] == "reviewed_mapping")
        self.assertIn("不可安装", reason)

    def test_invalid_policy_rejected_by_contract(self):
        with self.assertRaises(ValueError):
            RetrievalRequest.from_dict({"question": "x", "fidelity_gate": "explode"})

    def test_multilingual_route_downweights_l2_and_rejects_l3(self):
        """Fabricated explicit terms: drug/disease entity -> L3 reject; other -> L2 downweight."""
        from rag.retrieval_adapters import DeterministicSemanticStub
        stub = DeterministicSemanticStub({doc.identity_key(): 0.9 for doc in SYNTHETIC_PUBMED})
        lexicon = FidelityLexicon([
            TermEntry("新生儿呼吸窘迫综合征", ("respiratory distress syndrome",), "disease"),
            TermEntry("氨茶碱", ("aminophylline",), "drug"),
            TermEntry("新生儿肺炎", ("neonatal pneumonia",), "disease"),
            TermEntry("疫苗", ("vaccine",), "medical"),
        ])
        # L3: fabricated disease entity (neonatal pneumonia) for a question that never mentions it
        pipeline = _pipeline(neural_retrievers={"xlingual": stub}, fidelity_lexicon=lexicon)
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": QUESTION, "retrieval_method": "bm25,xlingual",
            "english_terms": ["respiratory distress syndrome", "aminophylline", "neonatal pneumonia"],
            "top_k": 4, "fidelity_gate": "downweight",
        }))
        decisions = {item["route"]: item["action"] for item in package.trace["query_fidelity"]["decisions"]}
        self.assertEqual(decisions["multilingual_encoding"], "reject")
        adjustments = package.trace["query_fidelity"].get("signal_adjustments", [])
        self.assertTrue(any(item["method"] == "xlingual" and item["action"] == "reject" for item in adjustments))

        # L2: fabricated non-primary entity (vaccine) -> downweight with penalty
        pipeline = _pipeline(neural_retrievers={"xlingual": stub}, fidelity_lexicon=lexicon)
        package = pipeline.retrieve(RetrievalRequest.from_dict({
            "question": "该患儿新生儿呼吸窘迫综合征的用药如何调整",
            "retrieval_method": "bm25,xlingual",
            "english_terms": ["respiratory distress syndrome", "vaccine"],
            "top_k": 4, "fidelity_gate": "downweight",
        }))
        decisions = {item["route"]: item["action"] for item in package.trace["query_fidelity"]["decisions"]}
        self.assertEqual(decisions["multilingual_encoding"], "downweight")
        adjustments = package.trace["query_fidelity"].get("signal_adjustments", [])
        self.assertTrue(any(item["method"] == "xlingual" and item["action"] == "downweight" for item in adjustments))


if __name__ == "__main__":
    unittest.main()
