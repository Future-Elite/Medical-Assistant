"""Offline end-to-end orchestration tests using real tools/RAG and a scripted model."""

import json
import re
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from medrax.medical_session import ACTIVE_TURN, MedicalSession, TurnContext
from medrax.orchestrator import MedicalAgentOrchestrator
from medrax.tools.rag_tool import MedicalEvidenceRAGTool
from medrax.tools.his_tool import PatientSnapshotTool
from medrax.tools.citation_tool import OpenMedicalCitationTool
from medrax.answer_audit import QwenClaimTranslator
from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.fixtures import SYNTHETIC_PUBMED
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ToolCallingModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-tool-calling-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            cid = re.search(r'retrieval-[A-Za-z0-9._-]+:cit-\d+', str(messages[-1].content)).group(0)
            message = AIMessage(content=f"Supported claim[{cid}]")
        else:
            message = AIMessage(content="", tool_calls=[{
                "name": "retrieve_medical_evidence", "args": {"question": "hypertension treatment"}, "id": "offline-call",
            }])
        return ChatResult(generations=[ChatGeneration(message=message)])


class Verifier:
    name = "deterministic-test-verifier"
    available = True

    def verify(self, pairs):
        return [(not claim.startswith("Unsupported"), 0.99) for claim, doc in pairs]


class ScriptedWorkflow:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def stream(self, request, config, **kwargs):
        self.calls.append((request, config))
        action = self.steps.pop(0)
        draft = action() if callable(action) else action
        yield {"model": {"messages": [SimpleNamespace(content=draft, tool_calls=[])]}}


class MedicalAgentIntegrationTest(unittest.TestCase):
    def setUp(self):
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("test-pubmed", ("pubmed",), SYNTHETIC_PUBMED))
        self.rag = EvidenceRAG(EvidenceRetrievalPipeline(registry), auditor=GroundedAnswerAuditor(Verifier()))
        self.tool = MedicalEvidenceRAGTool()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch("medrax.tools.rag_tool._get_rag", return_value=self.rag).start()
        patch("medrax.tools.citation_tool._get_rag", return_value=self.rag).start()
        translator = QwenClaimTranslator()
        translator._translate_batch = Mock(side_effect=AssertionError("English must not call translation API"))
        patch("medrax.tools.rag_tool._get_claim_translator", return_value=translator).start()

    def controller(self, workflow):
        controller = MedicalAgentOrchestrator(workflow, self.tool)
        controller.audit_path = Path(self.tmp.name) / "audit.jsonl"
        return controller

    def retrieve(self, text="Supported claim"):
        content, artifact = self.tool._run("hypertension treatment")
        evidence = artifact["evidence"]
        if not evidence:
            return "No evidence."
        return f"{text}[{evidence[0]['metadata']['citation_id']}]"

    def test_retrieve_open_audit_and_reuse_prior_turn(self):
        def initial():
            draft = self.retrieve("Supported claim; P < 0.001.")
            turn = ACTIVE_TURN.get()
            citation_id = next(iter(turn.session.citation_map))
            opened = OpenMedicalCitationTool()._run(citation_id.rsplit(":", 1)[0], citation_id)
            self.assertEqual(opened[1]["citation"]["citation_id"], citation_id)
            return draft
        def followup():
            citation_id = next(iter(ACTIVE_TURN.get().session.citation_map))
            return f"Supported follow-up[{citation_id}]"
        controller = self.controller(ScriptedWorkflow([initial, followup]))
        first = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        second = controller.run_turn("hypertension follow-up", "thread", as_of="2025-12-31")
        self.assertEqual((first["status"], second["status"]), ("passed", "passed"))
        self.assertIn("PMID:", second["answer"])
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        audits = [row for row in rows if row["event"] == "answer_audit"]
        self.assertEqual(len(audits[0]["claims"]), 1)
        self.assertIn(";", audits[0]["claims"][0]["verifier_claim"])

    def test_two_retrievals_audited_in_their_own_packages(self):
        def draft():
            first = self.retrieve()
            second = self.retrieve()
            return first + "\n" + second
        controller = self.controller(ScriptedWorkflow([draft]))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "passed")
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        audit = next(row for row in rows if row["event"] == "answer_audit")
        self.assertEqual(len(audit["retrieval_ids"]), 2)
        for claim in audit["claims"]:
            self.assertEqual(len(claim["citation_checks"]), 1)

    def test_failed_draft_can_retrieve_new_support(self):
        controller = self.controller(ScriptedWorkflow([
            lambda: self.retrieve("Unsupported claim"),
            lambda: self.retrieve("Supported replacement"),
        ]))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(controller.sessions["thread"].retrievals), 2)

    def test_patient_switch_clears_memory_and_rejects_old_citation(self):
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve(), "Unused?"]))
        controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        old = controller.sessions["thread"]
        controller.run_turn("What patient facts are available?", "thread", patient_ref="2001", as_of="2025-12-31")
        new = controller.sessions["thread"]
        self.assertNotEqual(old.generation, new.generation)
        self.assertFalse(new.retrievals)
        self.assertNotEqual(controller.workflow.calls[0][1], controller.workflow.calls[1][1])
        turn = TurnContext(new, "question", lambda event: None)
        token = ACTIVE_TURN.set(turn)
        try:
            cid = next(iter(old.citation_map))
            with self.assertRaises(ValueError):
                OpenMedicalCitationTool()._run(cid.rsplit(":", 1)[0], cid)
        finally:
            ACTIVE_TURN.reset(token)

    def test_his_snapshot_passed_without_model_rewriting_and_future_hidden(self):
        payload = {
            "patient_ref": "2001", "as_of": "2025-12-31", "source": "synthetic",
            "facts": [
                {"collection": "diagnosis", "event_type": "DIAGNOSIS", "record_path": "his:old", "field_path": "his:old", "time": "2025-01-01", "fields": {"diagnosisName": "hypertension"}},
                {"collection": "diagnosis", "event_type": "DIAGNOSIS", "record_path": "his:future", "field_path": "his:future", "time": "2026-01-01", "fields": {"diagnosisName": "future condition"}},
            ],
        }
        patch("medrax.tools.his_tool.AgentHisClient.snapshot_payload", return_value=payload).start()
        def draft():
            content, artifact = PatientSnapshotTool()._run()
            self.assertNotIn("future condition", content)
            self.assertEqual(artifact["suppressed_future"], 1)
            self.assertIs(ACTIVE_TURN.get().session.snapshot, payload)
            return self.retrieve()
        controller = self.controller(ScriptedWorkflow([draft]))
        result = controller.run_turn("hypertension treatment", "thread", patient_ref="2001", as_of="2025-12-31")
        self.assertEqual(result["status"], "passed")
        self.assertIsNotNone(next(iter(controller.sessions["thread"].retrievals.values()))["patient_context"])

    def test_empty_retrieval_stops_after_three_queries(self):
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()] * 3))
        result = controller.run_turn("unmatchedzzzzzz", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(len(controller.sessions["thread"].retrievals), 3)

    def test_two_revisions_fail_closed(self):
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve("Unsupported claim")] * 3))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("两次修改", result["answer"])

    def test_budget_exhaustion_stops_agent(self):
        def over_budget():
            for _ in range(4):
                self.retrieve()
            return "never emitted"
        controller = self.controller(ScriptedWorkflow([over_budget]))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "budget_exhausted")

    def test_real_create_agent_tool_workers_keep_turn_context(self):
        from main import initialize_medical_agent
        with patch("main._create_qwen_model", return_value=ToolCallingModel()):
            controller, tools = initialize_medical_agent(str(ROOT / "agent/medrax/docs/system_prompts.txt"))
        controller.audit_path = Path(self.tmp.name) / "graph-audit.jsonl"
        result = controller.run_turn("hypertension treatment", "graph-thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(controller.sessions["graph-thread"].retrievals), 1)
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        final = rows[-1]
        self.assertEqual(final["tool_call_counts"]["model"], 2)
        self.assertEqual(final["tool_call_counts"]["retrieve_medical_evidence"], 1)

    def test_gradio_entry_runs_the_same_orchestration(self):
        from interface import ChatInterface
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()]))
        interface = ChatInterface(controller, {"MedicalEvidenceRAGTool": self.tool})
        interface.audit_path = controller.audit_path
        async def consume():
            final = None
            async for item in interface.process_message(
                "hypertension treatment", None, [], "ui-thread", "", "2025-12-31"
            ):
                final = item
            return final
        history, _, _ = asyncio.run(consume())
        self.assertIn("PMID:", history[-1].content)
        self.assertIn("ui-thread", controller.sessions)

    def test_unretrieved_public_pmid_is_blocked(self):
        controller = self.controller(ScriptedWorkflow(["Answer PMID:99999999"]))
        result = controller.run_turn("general question", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "invalid_citation")

    def test_cross_thread_citation_is_rejected(self):
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()]))
        controller.run_turn("hypertension treatment", "first", as_of="2025-12-31")
        cid = next(iter(controller.sessions["first"].citation_map))
        controller.workflow = ScriptedWorkflow([f"Supported claim[{cid}]"] * 3)
        result = controller.run_turn("hypertension treatment", "second", as_of="2025-12-31")
        self.assertEqual(result["status"], "blocked")

    def test_retrieval_error_is_not_treated_as_successful_empty_evidence(self):
        self.rag.retrieve_evidence = Mock(side_effect=RuntimeError("connector unavailable"))
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()] * 3))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_audit_execution_error_does_not_revise_draft(self):
        self.rag.audit_grounded_answer = Mock(side_effect=RuntimeError("verifier unavailable"))
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()]))
        result = controller.run_turn("hypertension treatment", "thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "audit_error")
        self.assertEqual(len(controller.workflow.calls), 1)


if __name__ == "__main__":
    unittest.main()
