"""Cross-component contract test: HIS -> Agent -> RAG.

Offline and deterministic. The chain under test:

1. the **Agent side** reads HIS (recorded responses from ``contracts/examples/``)
   and turns them into the ``patient_snapshot`` payload
   (reference implementation: ``contracts/agent_his_adapter.py``);
2. **RAG** accepts that payload and drives patient facts into an evidence package.

What this test owns (unlike the package unit tests): the contract
(``contracts/his_rag.md``) stays in sync with the examples and the mapper, RAG
never talks to HIS, and identifiers never reach an evidence package.
"""
import json
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from contracts.agent_his_adapter import HISClientError, AgentHisClient, build_payload
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.his import snapshot_from_dict
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"
BASE_URL = "http://127.0.0.1:8080"
PATIENT_REF = "2001"
AS_OF = "2025-12-31"

EXAMPLE_ROUTES = {
    f"{BASE_URL}/api/patients/{PATIENT_REF}": "his_rag_patient_2001.json",
    f"{BASE_URL}/api/patients/{PATIENT_REF}/timeline": "his_rag_patient_2001_timeline.json",
    f"{BASE_URL}/api/admissions/7101/timeline": "his_rag_admission_7101_timeline.json",
}

# Read-only HIS paths only; the Agent-side client must never issue anything else.
READ_ONLY_URL = r"^http://[^/]+/api/(patients/\d+|patients/\d+/timeline|admissions/\d+/timeline)$"

REQUEST = {
    "question": "该患者原发性高血压的长期管理建议和依据是什么？",
    "as_of": AS_OF,
    "patient_terms": ["高血压"],
    "user_terms": {"高血压": ["hypertension"]},
    "english_terms": ["hypertension"],
    "mesh_terms": ["Hypertension"],
    "top_k": 6,
}


def load_example(name: str):
    with open(EXAMPLES / name, encoding="utf-8") as handle:
        return json.load(handle)


def example_payload() -> dict:
    """The payload the Agent would send, built from the recorded HIS responses."""
    return build_payload(
        load_example("his_rag_patient_2001.json"),
        load_example("his_rag_patient_2001_timeline.json"),
        [load_example("his_rag_admission_7101_timeline.json")],
        patient_ref=PATIENT_REF,
        as_of=AS_OF,
    )


def envelope(data):
    return {"code": 0, "message": "success", "data": data}


def canned(patient=None, events=(), admission_events=(), patient_ref=PATIENT_REF):
    """Agent-side client plus the URLs it was asked for."""
    calls: list[str] = []
    table = {
        f"{BASE_URL}/api/patients/{patient_ref}": envelope({"id": int(patient_ref), "gender": 1, "birthDate": "1958-04-12"} if patient is None else patient),
        f"{BASE_URL}/api/patients/{patient_ref}/timeline": envelope({"patientId": int(patient_ref), "events": list(events)}),
        f"{BASE_URL}/api/admissions/7101/timeline": envelope({"patientId": int(patient_ref), "events": list(admission_events)}),
    }

    def transport(url: str, timeout: float):
        calls.append(url)
        if url not in table:
            raise HISClientError(f"unexpected url {url}", stage="test")
        return table[url]

    return AgentHisClient(base_url=BASE_URL, transport=transport), calls


def build_pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


class MapperTests(unittest.TestCase):
    """Agent-side mapping rules (pure functions, no HTTP)."""

    def test_examples_map_to_the_golden_payload(self):
        golden = load_example("his_rag_snapshot_payload_expected.json")
        payload = example_payload()
        self.assertEqual(payload, {key: value for key, value in golden.items() if not key.startswith("_")})

    def test_identifiers_never_enter_the_payload(self):
        blob = json.dumps(example_payload(), ensure_ascii=False)
        for leaked in ("演示患者A", "110101195804120011", "13800000000", "合成地址", "patientId"):
            self.assertNotIn(leaked, blob)
        patient_fact = next(fact for fact in example_payload()["facts"] if fact["record_path"] == "his:patient")
        self.assertEqual(patient_fact["fields"], {"gender": 1, "birthDate": "1958-04-12"})

    def test_range_fields_and_ongoing_order(self):
        facts = {fact["record_path"]: fact for fact in example_payload()["facts"]}
        admission = facts["his:timeline:ADMISSION:1"]
        self.assertEqual((admission["start"], admission["end"]), ("2025-03-01T10:00:00", "2025-03-08T09:30:00"))
        order = facts["his:timeline:ORDER:1"]
        self.assertEqual((order["start"], order["end"]), ("2025-03-01T11:00:00", None))  # 进行中

    def test_future_facts_are_carried_and_left_to_rag(self):
        """映射器不裁剪时间；未来事实照样进载荷，由 RAG 的 as_of 过滤。"""
        future = [fact for fact in example_payload()["facts"] if fact["time"] == "2026-01-20T07:15:00"]
        self.assertEqual(len(future), 1)

    def test_missing_payload_and_missing_type_are_warned_not_dropped(self):
        payload = build_payload(
            envelope({"id": 2001, "gender": 1, "birthDate": "1958-04-12"}),
            envelope({"patientId": 2001, "events": [
                {"type": "CHARGE", "time": "2025-03-08T10:00:00", "data": None},
                {"time": "2025-03-08T11:00:00", "data": {"amount": 1}},
            ]}),
            patient_ref=PATIENT_REF,
            as_of=AS_OF,
        )
        paths = [fact["record_path"] for fact in payload["facts"]]
        self.assertIn("his:timeline:CHARGE:1", paths)
        self.assertIn("his:timeline:UNKNOWN:1", paths)
        self.assertTrue(any("缺少实体载荷" in warning for warning in payload["warnings"]))
        self.assertTrue(any("没有 type" in warning for warning in payload["warnings"]))

    def test_non_object_payload_keeps_raw_value(self):
        payload = build_payload(
            envelope({"id": 2001, "gender": 1}),
            envelope({"patientId": 2001, "events": [{"type": "VITAL_SIGN", "time": "2025-03-02T06:30:00", "data": "36.6"}]}),
            patient_ref=PATIENT_REF,
            as_of=AS_OF,
        )
        fact = next(item for item in payload["facts"] if item["record_path"] == "his:timeline:VITAL_SIGN:1")
        self.assertEqual(fact["fields"], {"value": "36.6"})
        self.assertEqual(fact["time"], "2025-03-02T06:30:00")

    def test_unknown_patient_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_payload(envelope({}), envelope({}), patient_ref="2001", as_of=AS_OF)
        self.assertIn("unknown patient_ref: 2001", str(ctx.exception))

    def test_business_error_and_missing_envelope_are_rejected(self):
        with self.assertRaises(HISClientError) as ctx:
            build_payload({"code": 1, "message": "患者不存在", "data": None}, envelope({}), patient_ref="2001", as_of=AS_OF)
        self.assertEqual(ctx.exception.stage, "patient")
        self.assertIn("code=1", str(ctx.exception))
        with self.assertRaises(HISClientError) as ctx:
            build_payload({"data": {}}, envelope({}), patient_ref="2001", as_of=AS_OF)
        self.assertIn("envelope", str(ctx.exception))


class AgentClientTests(unittest.TestCase):
    """Agent-side client wiring: which endpoints are read and how failures surface."""

    def example_table(self):
        return {url: load_example(name) for url, name in EXAMPLE_ROUTES.items()}

    def test_client_reads_exactly_the_three_read_only_endpoints(self):
        table = self.example_table()
        calls: list[str] = []

        def transport(url: str, timeout: float):
            calls.append(url)
            return table[url]

        payload = AgentHisClient(base_url=BASE_URL, transport=transport).snapshot_payload(PATIENT_REF, AS_OF)
        self.assertEqual(sorted(calls), sorted(EXAMPLE_ROUTES))
        for url in calls:
            self.assertRegex(url, READ_ONLY_URL)
        self.assertEqual(payload, example_payload())

    def test_admission_feed_of_another_patient_is_ignored(self):
        table = self.example_table()

        def leaky(url: str, timeout: float):
            if "/api/admissions/" in url:
                return envelope({"patientId": 9999, "events": load_example("his_rag_admission_7101_timeline.json")["data"]["events"]})
            return table[url]

        payload = AgentHisClient(base_url=BASE_URL, transport=leaky).snapshot_payload(PATIENT_REF, AS_OF)
        paths = [fact["record_path"] for fact in payload["facts"]]
        self.assertNotIn("his:timeline:ORDER:1", paths)
        self.assertTrue(any("与本快照患者" in warning for warning in payload["warnings"]))

    def test_default_transport_works_over_real_http(self):
        routes = {
            "/api/patients/2001": load_example("his_rag_patient_2001.json"),
            "/api/patients/2001/timeline": load_example("his_rag_patient_2001_timeline.json"),
            "/api/admissions/7101/timeline": load_example("his_rag_admission_7101_timeline.json"),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                payload = routes.get(self.path)
                if payload is None:
                    self.send_error(404)
                    return
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802 - must never be reached
                self.send_error(405)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = AgentHisClient(base_url=f"http://127.0.0.1:{server.server_address[1]}")
            payload = client.snapshot_payload(PATIENT_REF, AS_OF)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(payload, example_payload())


class RagBoundaryTests(unittest.TestCase):
    """RAG accepts the Agent's structured data -- and never reaches out to HIS."""

    def test_payload_drives_patient_facts_into_the_evidence_package(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": example_payload()}))
        self.assertTrue(package.patient_hits, "Agent 传入的快照必须能驱动患者事实检索")
        self.assertIn("his:timeline:DIAGNOSIS:1", {hit.record_path for hit in package.patient_hits})
        self.assertEqual(package.patient_context["context_id"], f"his_demo:{PATIENT_REF}:{AS_OF}")
        self.assertEqual(package.patient_context["suppressed_future"], 1)
        self.assertTrue(package.trace["patient_context"]["received"])
        self.assertTrue(package.ranked_documents)  # 外部证据照常返回

    def test_identifiers_never_reach_the_evidence_package(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": example_payload()}))
        blob = json.dumps(package.to_dict(), ensure_ascii=False)
        for leaked in ("演示患者A", "110101195804120011", "13800000000", "合成地址", "patientId"):
            self.assertNotIn(leaked, blob)

    def test_payload_dict_and_snapshot_object_are_equivalent(self):
        payload = example_payload()
        from_dict = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": payload}))
        from_object = build_pipeline().retrieve(
            RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": snapshot_from_dict(payload)})
        )
        self.assertEqual(
            {hit.record_path for hit in from_dict.patient_hits},
            {hit.record_path for hit in from_object.patient_hits},
        )

    def test_rag_package_contains_no_his_client(self):
        """架构边界：HIS 由 Agent 读取；rag 包内不得出现 HIS 端点或客户端模块。"""
        offenders = []
        for path in (ROOT / "rag").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in ("/api/patients", "/api/admissions", "his_http", "DEFAULT_HIS_BASE_URL"):
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(offenders, [])

    def test_examples_are_synthetic(self):
        for name in EXAMPLE_ROUTES.values():
            self.assertIn("合成", json.dumps(load_example(name), ensure_ascii=False))
        patient = load_example("his_rag_patient_2001.json")["data"]
        self.assertEqual(patient["name"], "演示患者A")
        self.assertTrue(patient["address"].startswith("合成地址"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
