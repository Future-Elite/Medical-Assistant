import json
import tempfile
import unittest
from pathlib import Path

from interface import ChatInterface, create_demo


class AuditLogTest(unittest.TestCase):
    def test_demo_builds_with_installed_gradio(self):
        self.assertIsNotNone(create_demo(object(), {}))

    def test_extracts_and_validates_all_supported_citations(self):
        text = (
            "PMID: 32065842、29453021；"
            "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee；"
            "SPL ID:11111111-2222-3333-4444-555555555555；NCT01234567。"
        )
        self.assertEqual(
            ChatInterface._extract_citation_ids(text),
            {
                "PMID:32065842",
                "PMID:29453021",
                "FDA_SET_ID:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "SPL_ID:11111111-2222-3333-4444-555555555555",
                "NCT01234567",
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
            interface._append_audit({"event": "first", "turn_id": "turn-1"})
            interface._append_audit({"event": "second", "turn_id": "turn-1"})

            records = [
                json.loads(line)
                for line in interface.audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["event"] for record in records], ["first", "second"])
            self.assertTrue(all(record["thread_id"] == old_thread for record in records))

            interface.reset_conversation()
            self.assertNotEqual(interface.current_thread_id, old_thread)


if __name__ == "__main__":
    unittest.main()
