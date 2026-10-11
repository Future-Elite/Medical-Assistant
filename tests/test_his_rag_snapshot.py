"""Cross-component test: HIS example payloads -> ``rag.his_http`` -> evidence package.

Offline and deterministic: HIS responses come from ``contracts/examples/`` and are
served through the adapter's transport seam, so no HIS server, MySQL or network is
involved. What this test owns (unlike the package unit tests) is the *contract*:

- the example responses and the golden snapshot in ``contracts/examples/`` must stay
  in sync with ``rag/his_http.py``'s mapping;
- only read-only HIS endpoints may be called;
- identifiers from HIS payloads must never reach an evidence package;
- the snapshot produced by the adapter must drive patient facts in a real retrieval.
"""
import json
import pathlib
import re
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.his_http import HisHttpAdapter
from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_PUBMED

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples"
BASE_URL = "http://127.0.0.1:8080"
PATIENT_REF = "2001"
AS_OF = "2025-12-31"

# Every URL the adapter is allowed to request: read-only GET paths only.
READ_ONLY_URL = re.compile(r"^http://[^/]+/api/(patients/\d+|patients/\d+/timeline|admissions/\d+/timeline)$")

EXAMPLE_ROUTES = {
    f"{BASE_URL}/api/patients/{PATIENT_REF}": "his_rag_patient_2001.json",
    f"{BASE_URL}/api/patients/{PATIENT_REF}/timeline": "his_rag_patient_2001_timeline.json",
    f"{BASE_URL}/api/admissions/7101/timeline": "his_rag_admission_7101_timeline.json",
}

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


def build_adapter():
    """Adapter wired to the example responses, recording every requested URL."""
    table = {url: load_example(name) for url, name in EXAMPLE_ROUTES.items()}
    calls: list[str] = []

    def transport(url: str, timeout: float):
        calls.append(url)
        return table[url]

    return HisHttpAdapter(base_url=BASE_URL, transport=transport), calls


def project(snapshot) -> dict:
    return {
        "patient_ref": snapshot.patient_ref,
        "as_of": snapshot.as_of,
        "source": snapshot.source,
        "suppressed_future": snapshot.suppressed_future,
        "facts": [
            {
                "collection": fact.collection,
                "event_type": fact.event_type,
                "record_path": fact.record_path,
                "field_path": fact.field_path,
                "time": fact.time,
                "start": fact.start,
                "end": fact.end,
                "fields": dict(fact.fields),
            }
            for fact in snapshot.facts
        ],
    }


def build_pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


class ExampleContractTests(unittest.TestCase):
    def test_examples_map_to_the_golden_snapshot(self):
        adapter, _ = build_adapter()
        snapshot = adapter.get_snapshot(PATIENT_REF, AS_OF)
        golden = load_example("his_rag_snapshot_expected.json")
        self.assertEqual(project(snapshot), {key: value for key, value in golden.items() if not key.startswith("_")})

    def test_only_read_only_endpoints_are_called(self):
        adapter, calls = build_adapter()
        adapter.get_snapshot(PATIENT_REF, AS_OF)
        self.assertEqual(sorted(calls), sorted(EXAMPLE_ROUTES))
        for url in calls:
            self.assertRegex(url, READ_ONLY_URL)

    def test_examples_are_synthetic(self):
        for name in EXAMPLE_ROUTES.values():
            example = load_example(name)
            self.assertIn("合成", json.dumps(example, ensure_ascii=False))
        patient = load_example("his_rag_patient_2001.json")["data"]
        self.assertEqual(patient["name"], "演示患者A")
        self.assertTrue(patient["address"].startswith("合成地址"))


class IntegrationTests(unittest.TestCase):
    def test_snapshot_drives_patient_facts_into_the_evidence_package(self):
        adapter, _ = build_adapter()
        snapshot = adapter.get_snapshot(PATIENT_REF, AS_OF)
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": snapshot}))

        self.assertTrue(package.patient_hits, "HIS 快照必须能驱动患者事实检索")
        hit_paths = {hit.record_path for hit in package.patient_hits}
        self.assertIn("his:timeline:DIAGNOSIS:1", hit_paths)
        # 快照来源与时间边界随证据包一起可审计
        self.assertEqual(package.patient_context["context_id"], f"his_demo:{PATIENT_REF}:{AS_OF}")
        self.assertEqual(package.patient_context["suppressed_future"], 1)
        self.assertTrue(package.trace["patient_context"]["received"])
        self.assertGreaterEqual(package.trace["patient_context"]["visible_fact_count"], len(package.patient_hits))
        # 外部证据仍然正常（患者事实 + PubMed/指南联合检索）
        self.assertTrue(package.ranked_documents)

    def test_identifiers_never_reach_the_evidence_package(self):
        adapter, _ = build_adapter()
        snapshot = adapter.get_snapshot(PATIENT_REF, AS_OF)
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": snapshot}))
        blob = json.dumps(package.to_dict(), ensure_ascii=False)
        for leaked in ("演示患者A", "110101195804120011", "13800000000", "合成地址", "patientId"):
            self.assertNotIn(leaked, blob)

    def test_snapshot_dict_round_trip_is_supported(self):
        """Agent 侧可以传快照对象；服务端路径（JSON）传 dict 也必须等价。"""
        adapter, _ = build_adapter()
        snapshot = adapter.get_snapshot(PATIENT_REF, AS_OF)
        payload = {
            "patient_snapshot": {
                "patient_ref": snapshot.patient_ref,
                "as_of": snapshot.as_of,
                "source": snapshot.source,
                "facts": [
                    {
                        "collection": fact.collection,
                        "event_type": fact.event_type,
                        "record_path": fact.record_path,
                        "field_path": fact.field_path,
                        "fields": dict(fact.fields),
                        "time": fact.time,
                        "start": fact.start,
                        "end": fact.end,
                    }
                    for fact in snapshot.facts
                ],
            }
        }
        from_object = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, "patient_snapshot": snapshot}))
        from_dict = build_pipeline().retrieve(RetrievalRequest.from_dict({**REQUEST, **payload}))
        self.assertEqual(
            {hit.record_path for hit in from_object.patient_hits},
            {hit.record_path for hit in from_dict.patient_hits},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
