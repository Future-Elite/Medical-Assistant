import json
import tempfile
import unittest
from pathlib import Path

from interface import ChatInterface


class AuditLogTest(unittest.TestCase):
    def test_extracts_only_explicit_pmid_citations(self):
        text = "证据（PMID: 32065842、29453021）和 PMID 27032221，发表于2020年。"
        self.assertEqual(
            ChatInterface._extract_pmids(text),
            {"32065842", "29453021", "27032221"},
        )
        self.assertEqual(ChatInterface._extract_pmids("2020年普通回答"), set())

        interface = ChatInterface(object(), {})
        self.assertEqual(interface._unverified_pmids(text), ["27032221", "29453021", "32065842"])
        interface.verified_pmids.update({"32065842", "29453021", "27032221"})
        self.assertEqual(interface._unverified_pmids(text), [])

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
            self.assertEqual(interface.pubmed_call_count, 0)
            self.assertEqual(interface.verified_pmids, set())


if __name__ == "__main__":
    unittest.main()
