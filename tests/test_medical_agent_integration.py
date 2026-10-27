"""Offline end-to-end orchestration tests using real tools/RAG and a scripted model."""

import json
import re
import asyncio
from pathlib import Path
import sys
import tempfile
from threading import Event
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
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


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


class BudgetSeekingModel(BaseChatModel):
    """Keep requesting retrieval while tools exist; answer only when withdrawn."""
    calls: list[bool] = Field(default_factory=list)
    parallel: bool = False

    @property
    def _llm_type(self):
        return "offline-budget-seeking-model"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        has_tools = bool(kwargs.get("tools"))
        self.calls.append(has_tools)
        last_user = max(index for index, message in enumerate(messages) if isinstance(message, HumanMessage))
        results = [message for message in messages[last_user:] if isinstance(message, ToolMessage)]
        if has_tools:
            if self.parallel and not results:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(
                    content="", tool_calls=[{
                        "name": "retrieve_medical_evidence",
                        "args": {"search_query": "hypertension treatment"},
                        "id": f"parallel-call-{index}",
                    } for index in range(4)],
                ))])
            attempts = [
                {"search_query": "hypertension treatment"},
                {"search_query": "hypertension drug safety"},
                {"search_query": "unmatchedzzzzzz", "evidence_gap": "Need direct evidence",
                 "adjustment_reason": "Search one specific missing topic"},
            ]
            args = attempts[min(len(results), 2)]
            message = AIMessage(content="", tool_calls=[{
                "name": "retrieve_medical_evidence", "args": args,
                "id": f"budget-call-{len(results)}",
            }])
        else:
            citation = re.search(
                r"retrieval-[A-Za-z0-9._-]+:cit-\d+",
                "\n".join(str(result.content) for result in results),
            ).group(0)
            message = AIMessage(content=f"Supported claim[{citation}]")
        return ChatResult(generations=[ChatGeneration(message=message)])


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
        controller = MedicalAgentOrchestrator(workflow, self.tool, audit_mode="strict")
        controller.audit_path = Path(self.tmp.name) / "audit.jsonl"
        self.addCleanup(controller._audit_jobs.join)
        return controller

    def retrieve(self, text="Supported claim", *, query=None, top_k=8):
        turn = ACTIVE_TURN.get()
        content, artifact = self.tool._run(search_query=query or turn.question, top_k=top_k,
            evidence_gap="Prior evidence did not support the requested claim" if turn.retrieval_attempts else None,
            adjustment_reason="Adjust query or selection; retry a transient source error if unchanged" if turn.retrieval_attempts else None)
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
        self.assertEqual(len(audits[0]["claims"]), 2)
        self.assertEqual(
            [claim["verifier_claim"] for claim in audits[0]["claims"]],
            ["Supported claim;", "P < 0.001."],
        )
        self.assertEqual(
            audits[0]["claims"][0]["valid_citation_ids"],
            audits[0]["claims"][1]["valid_citation_ids"],
        )

    def test_current_retrieval_excludes_stale_history_from_answer_scope(self):
        previous = {}

        def initial():
            draft = self.retrieve()
            previous["citation_id"] = next(iter(ACTIVE_TURN.get().answer_citation_map))
            return draft

        def stale_answer_after_new_search():
            self.retrieve(query="hypertension treatment evidence")
            return f"Supported but stale[{previous['citation_id']}]"

        controller = self.controller(ScriptedWorkflow([initial, stale_answer_after_new_search]))
        self.assertEqual(controller.run_turn("hypertension treatment", "thread")["status"], "passed")
        controller.audit_mode = "observe"
        result = controller.run_turn("new evidence question", "thread")
        self.assertEqual(result["status"], "invalid_citation")

    def test_two_retrievals_audited_in_their_own_packages(self):
        def draft():
            first = self.retrieve()
            second = self.retrieve(query="hypertension treatment evidence")
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
            lambda: self.retrieve("Supported replacement", top_k=6),
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
        controller = self.controller(ScriptedWorkflow([
            lambda: self.retrieve(query="unmatchedzzzzzz one"),
            lambda: self.retrieve(query="unmatchedzzzzzz two"),
            lambda: self.retrieve(query="unmatchedzzzzzz three"),
        ]))
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
        with patch("main._create_qwen_model", return_value=ToolCallingModel()), patch.dict("os.environ", {"ANSWER_AUDIT_MODE": "strict"}):
            controller, tools = initialize_medical_agent(str(ROOT / "agent/medrax/docs/system_prompts.txt"))
        controller.audit_path = Path(self.tmp.name) / "graph-audit.jsonl"
        result = controller.run_turn("hypertension treatment", "graph-thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(controller.sessions["graph-thread"].retrievals), 1)
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        final = rows[-1]
        self.assertEqual(final["tool_call_counts"]["model"], 2)
        self.assertEqual(final["tool_call_counts"]["retrieve_medical_evidence"], 1)

    def test_real_agent_finalizes_after_rejected_and_empty_searches_and_resets_next_turn(self):
        from main import initialize_medical_agent
        model = BudgetSeekingModel()
        with patch("main._create_qwen_model", return_value=model), patch.dict("os.environ", {"ANSWER_AUDIT_MODE": "observe"}):
            controller, _ = initialize_medical_agent(str(ROOT / "agent/medrax/docs/system_prompts.txt"))
        self.addCleanup(controller._audit_jobs.join)
        controller.audit_path = Path(self.tmp.name) / "budget-audit.jsonl"
        for question in ("hypertension treatment", "hypertension follow-up"):
            result = controller.run_turn(question, "budget-thread", as_of="2025-12-31")
            self.assertEqual(result["status"], "observed")
            self.assertIn("PMID:", result["answer"])
        controller._audit_jobs.join()
        self.assertEqual(model.calls, [True, True, True, False] * 2)
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        self.assertFalse(any(row["event"] == "agent_error" for row in rows))
        finals = [row for row in rows if row["event"] == "final_response"]
        self.assertEqual(len(finals), 2)
        for row in finals:
            self.assertEqual(row["tool_call_counts"]["retrieve_medical_evidence"], 3)
        rejected = [row for row in rows if row["event"] == "retrieval_rejected"]
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(row["artifact"]["turn_retrieval_summary"]["tool_call_budget"]["remaining"] == 1
                            for row in rejected))
        searches = [row for row in rows if row["event"] == "evidence_search"]
        self.assertEqual(len(searches), 4)
        self.assertEqual([row["artifact"]["turn_retrieval_summary"]["tool_call_budget"]["remaining"]
                          for row in searches], [2, 0, 2, 0])

    def test_parallel_over_budget_tool_calls_still_produce_a_final_answer(self):
        from main import initialize_medical_agent
        model = BudgetSeekingModel(parallel=True)
        with patch("main._create_qwen_model", return_value=model), patch.dict("os.environ", {"ANSWER_AUDIT_MODE": "observe"}):
            controller, _ = initialize_medical_agent(str(ROOT / "agent/medrax/docs/system_prompts.txt"))
        self.addCleanup(controller._audit_jobs.join)
        controller.audit_path = Path(self.tmp.name) / "parallel-budget-audit.jsonl"
        result = controller.run_turn("hypertension treatment", "parallel-thread", as_of="2025-12-31")
        self.assertEqual(result["status"], "observed", controller.audit_path.read_text()[-3000:])
        self.assertIn("PMID:", result["answer"])
        self.assertEqual(model.calls, [True, False])
        controller._audit_jobs.join()
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        final = next(row for row in rows if row["event"] == "final_response")
        self.assertEqual(final["tool_call_counts"]["retrieve_medical_evidence"], 3)

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

    def test_observe_returns_failed_audit_without_revision_and_reuses_evidence(self):
        def followup():
            cid = next(iter(ACTIVE_TURN.get().session.citation_map))
            return f"Supported follow-up[{cid}]"
        controller = self.controller(ScriptedWorkflow([
            lambda: self.retrieve("Unsupported claim"), followup,
        ]))
        controller.audit_mode = "observe"
        first = controller.run_turn("hypertension treatment", "thread")
        second = controller.run_turn("follow-up", "thread")
        self.assertEqual((first["status"], second["status"]), ("observed", "observed"))
        self.assertNotIn("联调草稿", first["answer"])
        self.assertNotIn("证据支持性未全部通过", first["answer"])
        self.assertIn("PMID:", second["answer"])
        self.assertEqual(len(controller.workflow.calls), 2)
        controller._audit_jobs.join()
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        self.assertFalse(next(r for r in rows if r["event"] == "answer_audit")["blocking"])

    def test_background_audit_does_not_delay_output_or_mix_patient_scopes(self):
        started, release = Event(), Event()
        calls = []

        def translate(claims):
            calls.append(list(claims))
            if len(calls) == 1:
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Response waited for background translation")
            return claims

        def patient_draft():
            turn = ACTIVE_TURN.get()
            draft = self.retrieve()
            turn.session.snapshot = {"patient_ref": turn.session.patient_ref, "facts": []}
            return draft

        controller = self.controller(ScriptedWorkflow([patient_draft, patient_draft]))
        controller.audit_mode = "observe"
        patch.object(MedicalEvidenceRAGTool, "translate_claims", side_effect=translate).start()
        events = []
        try:
            first = controller.run_turn("hypertension treatment", "thread", patient_ref="2001", emit=events.append)
            self.assertIn("PMID:", first["answer"])
            self.assertTrue(started.wait(2))
            self.assertEqual(first["status"], "observed")
            self.assertFalse(release.is_set())
            # Remove live evidence while its audit is pending: the frozen scope must survive.
            controller.sessions["thread"].retrievals.clear()
            second = controller.run_turn("hypertension treatment", "thread", patient_ref="2002", emit=events.append)
            self.assertEqual(second["status"], "observed")
        finally:
            release.set()
            controller._audit_jobs.join()

        self.assertEqual(len(controller.workflow.calls), 2)
        self.assertFalse(any(row["event"] in {"audit_stage", "answer_audit"} for row in events))
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        audits = [row for row in rows if row["event"] == "answer_audit"]
        self.assertEqual(len(audits), 2)
        self.assertTrue(all(row["passed"] for row in audits))
        self.assertEqual([row["turn_id"] for row in audits], [first["turn_id"], second["turn_id"]])
        self.assertNotEqual(audits[0]["patient_scope"], audits[1]["patient_scope"])
        for audit in audits:
            final_index = next(i for i, row in enumerate(rows)
                               if row["event"] == "final_response" and row["turn_id"] == audit["turn_id"])
            self.assertLess(final_index, rows.index(audit))

    def test_observe_his_survives_empty_or_failed_retrieval(self):
        payload = {"patient_ref": "2001", "facts": [{"record_path": "his:old", "fields": {"diagnosisName": "hypertension"}}]}
        for fail in (False, True):
            with self.subTest(fail=fail):
                def draft():
                    ACTIVE_TURN.get().session.snapshot = payload
                    self.retrieve(query="unmatchedzzzzzz")
                    return "患者记录：hypertension[his:old]。检索证据：未获得。"
                controller = self.controller(ScriptedWorkflow([draft]))
                controller.audit_mode = "observe"
                with patch.object(self.rag, "retrieve_evidence", side_effect=RuntimeError("offline")) if fail else patch.dict({}, {}):
                    result = controller.run_turn("unmatchedzzzzzz", "thread", patient_ref="2001")
                self.assertEqual(result["status"], "observed")
                self.assertIn("患者记录：hypertension", result["answer"])
                self.assertEqual(len(controller.workflow.calls), 1)

    def test_observe_still_blocks_unknown_public_internal_and_his_citations(self):
        for draft in ("PMID:99999999", "Claim[retrieval-other:cit-1]", "患者[his:other]"):
            with self.subTest(draft=draft):
                controller = self.controller(ScriptedWorkflow([draft]))
                controller.audit_mode = "observe"
                self.assertEqual(controller.run_turn("question", "thread")["status"], "invalid_citation")

    def test_observe_audit_error_does_not_hide_draft(self):
        self.rag.audit_grounded_answer = Mock(side_effect=RuntimeError("unavailable"))
        controller = self.controller(ScriptedWorkflow([lambda: self.retrieve()]))
        controller.audit_mode = "observe"
        result = controller.run_turn("hypertension treatment", "thread")
        self.assertEqual(result["status"], "observed")
        self.assertIn("Supported claim", result["answer"])
        self.assertEqual(len(controller.workflow.calls), 1)
        controller._audit_jobs.join()
        rows = [json.loads(line) for line in controller.audit_path.read_text().splitlines()]
        audit = next(row for row in rows if row["event"] == "answer_audit")
        self.assertEqual(audit["status"], "error")
        self.assertFalse(audit["blocking"])

    def test_observe_combined_his_citations_validate_each_record(self):
        for separator in (", ", "，", "; ", "；", "、"):
            for second, expected in (("his:timeline:DISCHARGE:1", "observed"), ("his:other", "invalid_citation")):
                with self.subTest(separator=separator, second=second):
                    def draft():
                        ACTIVE_TURN.get().session.snapshot = {
                            "facts": [{"record_path": path} for path in (
                                "his:timeline:ADMISSION:1", "his:timeline:DISCHARGE:1")]
                        }
                        return f"患者记录[his:timeline:ADMISSION:1{separator}{second}]"
                    controller = self.controller(ScriptedWorkflow([draft]))
                    controller.audit_mode = "observe"
                    result = controller.run_turn("读取患者记录", "thread", patient_ref="2001")
                    self.assertEqual(result["status"], expected)


if __name__ == "__main__":
    unittest.main()
