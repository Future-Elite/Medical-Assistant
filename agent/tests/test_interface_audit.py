import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from interface import ChatInterface, create_demo


class AuditLogTest(unittest.TestCase):
    def test_demo_builds_with_installed_gradio(self):
        self.assertIsNotNone(create_demo(object(), {}))

    def test_medical_event_card_summarizes_sources_and_keeps_details(self):
        title, content = ChatInterface._format_medical_event("evidence_search", {
            "query": "成人高血压治疗",
            "retrieval_id": "retrieval-test",
            "evidence": [
                {"source_type": "pubmed", "source_id": "PMID:12345678"},
                {"source_type": "pubmed", "source_id": "PMID:23456789"},
                {"source_type": "guideline", "source_id": "WHO:1"},
            ],
        })

        self.assertEqual(title, "医学证据检索")
        self.assertIn("共获得 3 条候选证据", content)
        self.assertIn("PubMed 2", content)
        self.assertIn("指南 1", content)
        self.assertIn("成人高血压治疗", content)
        self.assertIn("查看原始详情", content)
        self.assertIn("PMID:12345678", content)

    def test_his_error_card_is_explicit(self):
        title, content = ChatInterface._format_medical_event(
            "his_error", {"error": "HIS service unavailable"}
        )

        self.assertEqual(title, "HIS 连接异常")
        self.assertIn("HIS service unavailable", content)
        self.assertIn("medical-event-card--error", content)

    def test_extracts_and_validates_all_supported_citations(self):
        text = (
            "PMID: 32065842、29453021；"
            "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee；"
            "SPL ID:11111111-2222-3333-4444-555555555555；NCT01234567；"
            "RAG_CITATION:retrieval-test:cit-1。"
        )
        self.assertEqual(
            ChatInterface._extract_citation_ids(text),
            {
                "PMID:32065842",
                "PMID:29453021",
                "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "SPL_ID:11111111-2222-3333-4444-555555555555",
                "NCT01234567",
                "RAG_CITATION:retrieval-test:cit-1",
            },
        )
        artifact = {
            "evidence": [{
                "source_id": "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "metadata": {"spl_id": "11111111-2222-3333-4444-555555555555"},
            }]
        }
        self.assertEqual(
            ChatInterface._artifact_citation_ids(artifact),
            {
                "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "SPL_ID:11111111-2222-3333-4444-555555555555",
            },
        )
        self.assertEqual(ChatInterface._extract_citation_ids("2020年普通回答"), set())

    def test_jsonl_is_appended_and_reset_changes_thread(self):
        interface = ChatInterface(object(), {})
        with tempfile.TemporaryDirectory() as directory:
            interface.audit_path = Path(directory) / "audit.jsonl"
            old_thread = interface.current_thread_id
            interface._append_audit({
                "event": "first",
                "turn_id": "turn-1",
                "tool_args": {"question": "高血压治疗", "top_k": 5},
            })
            interface._append_audit({"event": "second", "turn_id": "turn-1"})

            records = [
                json.loads(line)
                for line in interface.audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["event"] for record in records], ["first", "second"])
            self.assertEqual(records[0]["tool_args"]["top_k"], 5)
            self.assertTrue(all(record["thread_id"] == old_thread for record in records))

            interface.reset_conversation()
            self.assertNotEqual(interface.current_thread_id, old_thread)

    def test_extracts_serializable_model_tool_arguments(self):
        self.assertEqual(
            ChatInterface._tool_call_arguments({
                "id": "call-1",
                "args": {"question": "高血压治疗", "top_k": 5},
            }),
            {"question": "高血压治疗", "top_k": 5},
        )

    def test_evidence_event_persists_model_and_effective_tool_arguments(self):
        class Workflow:
            def stream(self, *args, **kwargs):
                yield {"model": {"messages": [SimpleNamespace(
                    content="",
                    tool_calls=[{
                        "id": "call-1",
                        "name": "retrieve_medical_evidence",
                        "args": {"question": "高血压治疗", "top_k": 5},
                    }],
                )]}}
                yield {"tools": {"messages": [SimpleNamespace(
                    name="retrieve_medical_evidence",
                    tool_call_id="call-1",
                    content="[PMID:12345678] Example",
                    artifact={
                        "query": "高血压治疗",
                        "tool_args": {
                            "question": "高血压治疗",
                            "top_k": 5,
                            "as_of": None,
                            "user_terms": {},
                        },
                        "retrieval_id": "retrieval-test",
                        "evidence": [{
                            "source_id": "PMID:12345678",
                            "source_type": "pubmed",
                            "metadata": {},
                        }],
                        "trace": {
                            "candidate_count": 20,
                            "selected_count": 5,
                            "connector_trace": [{
                                "source_id": "pubmed-live",
                                "status": "ok",
                                "returned": 20,
                            }],
                        },
                        "warnings": [],
                    },
                )]}}

        interface = ChatInterface(object(), {})
        interface.workflow = Workflow()

        async def consume():
            async for _ in interface.process_message("高血压治疗", None, []):
                pass

        with tempfile.TemporaryDirectory() as directory:
            interface.audit_path = Path(directory) / "audit.jsonl"
            asyncio.run(consume())
            record = json.loads(interface.audit_path.read_text(encoding="utf-8"))

        self.assertEqual(record["model_tool_args"]["top_k"], 5)
        self.assertEqual(record["tool_args"]["top_k"], 5)
        self.assertEqual(record["candidate_count"], 20)
        self.assertEqual(record["selected_count"], 5)
        self.assertEqual(record["retrieval_id"], "retrieval-test")


if __name__ == "__main__":
    unittest.main()
