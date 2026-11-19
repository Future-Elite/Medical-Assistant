"""患者事实回流到检索流程：Agent 查询进入患者检索，患者事实进入本地指南检索。

两条约定：
1. 患者图检索使用 Agent 传入的完整查询（问题 + 审核术语 + 术语映射展开）。
2. 患者图命中的概念标签/匹配别名回流为本地指南检索的额外查询变体（降权），
   但不进入外部来源（PubMed/drug），保持最小信息边界。
"""
from __future__ import annotations

import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.core.his import snapshot_from_dict
from rag.models import RetrievalRequest, SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline


def _snapshot():
    return snapshot_from_dict({
        "patient_ref": "p1", "as_of": "2026-04-15",
        "records": [
            {"record_id": "r:dx", "record_type": "diagnosis", "event_type": "DIAG",
             "fields": {"name": "新生儿肺炎", "data_status": "observed_positive"},
             "provenance": {"record_path": "r:dx", "field_path": "r:dx"},
             "event_time": "2026-04-10T00:00:00"},
            {"record_id": "r:resp", "record_type": "derived_respiratory_support",
             "event_type": "DERIVED_RESPIRATORY_SUPPORT",
             "fields": {"mode": "经鼻 CPAP", "data_status": "observed_positive"},
             "provenance": {"record_path": "r:resp", "field_path": "r:resp"},
             "event_time": "2026-04-11T00:00:00"},
        ],
    })


def _pipeline():
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", (
        SourceDocument(source_id="guideline-fixture", document_id="g-a", kind="guideline",
                       title="CPAP 通气策略推荐", text="CPAP 通气策略推荐：无创通气。"),
        SourceDocument(source_id="guideline-fixture", document_id="g-b", kind="guideline",
                       title="呼吸支持方式", text="呼吸支持方式的选择。"),
    )))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), (
        SourceDocument(source_id="pubmed-fixture", document_id="p-1", kind="pubmed",
                       title="呼吸支持综述", text="呼吸支持综述。"),
    )))
    return EvidenceRetrievalPipeline(registry)


class PatientFactExpansionTests(unittest.TestCase):
    def test_agent_query_feeds_patient_retrieval(self):
        # 术语映射展开后的英文/MeSH 一并进入患者图检索。
        from dataclasses import asdict
        request = RetrievalRequest.from_dict({
            "question": "新生儿肺炎的通气策略推荐",
            "patient_terms": ["新生儿肺炎"],
            "term_mapping": {"新生儿肺炎": {"english": ["neonatal pneumonia"], "mesh": ["Pneumonia"]}},
            "top_k": 10,
            "patient_snapshot": asdict(_snapshot()),
        })
        pkg = _pipeline().retrieve(request)
        queries = pkg.trace.get("patient_retrieval_queries") or []
        self.assertIn("新生儿肺炎", queries)
        self.assertIn("neonatal pneumonia", queries)  # 映射展开进入患者检索
        self.assertIn("Pneumonia", queries)

    def test_patient_facts_join_local_guideline_retrieval(self):
        from dataclasses import asdict
        request = RetrievalRequest.from_dict({
            "question": "CPAP 通气策略推荐",
            "top_k": 10,
            "patient_snapshot": asdict(_snapshot()),
        })
        pkg = _pipeline().retrieve(request)
        # 患者事实（呼吸支持/肺炎）回流到指南检索，g-b 仅命中患者事实变体也被召回。
        ids = {row.document.document_id for row in pkg.ranked_documents}
        self.assertIn("g-a", ids)
        self.assertIn("g-b", ids, "患者事实应把只命中患者概念的本地指南拉入候选")
        # 回流记录可审计。
        expansion = pkg.trace.get("patient_fact_expansion")
        self.assertIsNotNone(expansion)
        self.assertTrue(expansion["terms"])
        self.assertIn("guideline", expansion["applied_to"])
        # g-b 的命中查询含患者事实变体（呼吸支持）。
        g_b = next(row for row in pkg.ranked_documents if row.document.document_id == "g-b")
        self.assertTrue(any("呼吸支持" in q for q in g_b.matched_queries))

    def test_patient_facts_do_not_reach_external_sources(self):
        from dataclasses import asdict
        request = RetrievalRequest.from_dict({
            "question": "CPAP 通气策略推荐",
            "top_k": 10,
            "patient_snapshot": asdict(_snapshot()),
        })
        pkg = _pipeline().retrieve(request)
        ids = {row.document.document_id for row in pkg.ranked_documents}
        # p-1 只命中患者事实概念（呼吸支持），不命中问题；外部来源不加入患者事实变体，
        # 因此不应被召回（最小信息边界）。
        self.assertNotIn("p-1", ids)


if __name__ == "__main__":
    unittest.main()
