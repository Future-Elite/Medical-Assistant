"""HTTP contract for the guideline parsing endpoints (offline temp data dir)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from rag.server import RAGRequestHandler


def _write_data(root: Path) -> None:
    guidelines = root / "guidelines"
    guidelines.mkdir(parents=True)
    (guidelines / "manifest.json").write_text(json.dumps({
        "guidelines": [{
            "document_id": "doc-1", "file": "guide.pdf", "title": "指南A", "version_id": "2025",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (root / "doc-1_chunks.jsonl").write_text(json.dumps({
        "chunk_id": "c1", "text": "推荐意见：极低出生体重儿喂养。",
        "source_id": "doc-1", "source_title": "指南A", "version": "2025",
        "section_path": ["指南A", "1 推荐"], "page_start": 2, "page_end": 2,
        "element_type": "text",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    applicability = root / "applicability"
    applicability.mkdir()
    (applicability / "doc-1.json").write_text(json.dumps({
        "logic": "all_of",
        "criteria": [{
            "criterion_id": "vlbw", "field": "birth.birth_weight_g", "operator": "lt",
            "value": 1500, "review_status": "synthetic_fixture",
            "source": {"locator": "1 推荐（第 2 页）"},
        }],
    }, ensure_ascii=False), encoding="utf-8")


SNAPSHOT = {
    "patient_ref": "p1",
    "as_of": "2026-03-10",
    "facts": [{
        "collection": "birth", "event_type": "birth", "record_path": "birth[0]",
        "fields": {"birth_weight_g": 1200, "data_status": "observed_positive"},
        "time": "2026-03-10",
    }],
}


class GuidelineHttpTests(unittest.TestCase):
    def _request(self, path: str, payload: dict | None = None):
        url = f"http://{self.host}:{self.port}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        _write_data(Path(self.directory.name))
        self.patch = mock.patch.dict(os.environ, {"RAG_GUIDELINE_DATA": self.directory.name})
        self.patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RAGRequestHandler)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.patch.stop()
        self.directory.cleanup()

    def test_list_and_chunk_preview(self):
        listing = self._request("/api/guidelines")
        self.assertTrue(listing["configured"])
        self.assertEqual(listing["guidelines"][0]["document_id"], "doc-1")
        self.assertEqual(listing["guidelines"][0]["chunk_count"], 1)
        preview = self._request("/api/guidelines/chunks?document_id=doc-1")
        self.assertEqual(preview["count"], 1)
        self.assertEqual(preview["chunks"][0]["chunk_id"], "c1")

    def test_applicability_preview_uses_patient_facts(self):
        result = self._request("/api/guidelines/applicability", {
            "document_id": "doc-1",
            "patient_snapshot": SNAPSHOT,
        })
        self.assertEqual(result["assessment"]["status"], "possible_match")
        self.assertEqual(result["assessment"]["criteria"][0]["status"], "match")
        self.assertEqual(result["assessment"]["criteria"][0]["observed_value"], "1200")

    def test_unknown_guideline_is_explicit(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._request("/api/guidelines/chunks?document_id=missing")
        self.assertEqual(context.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
