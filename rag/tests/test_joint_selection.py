"""联合检索契约：分源排序 + 来源可见性配额 + 检索式自检。

这些测试锁定"分源联合检索"相对旧版单池排序的核心行为变化：

- 各来源在**自己的词表内**独立排序（不再共享 IDF 统计）；
- 联合选择保证每个有相关候选的来源至少一个名额，并按意图给首选来源配额；
- 跨来源原始分数不可比，补齐只用来源内排名轮转（不比较跨来源分数）；
- top_k 不足时给出可见性警告；
- PRESS 风格检索式自检只报告发现、不修改检索计划。
"""
import unittest

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS, SYNTHETIC_PUBMED
from rag.his import InMemoryHISAdapter
from rag.models import QueryPlan, RetrievalRequest, SourceDocument
from rag.pipeline import EvidenceRetrievalPipeline
from rag.query import build_query_plan, review_query_plan
from rag.ranking import merge_joint_selection, rank_documents


def build_pipeline() -> EvidenceRetrievalPipeline:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return EvidenceRetrievalPipeline(registry)


HYPERTENSION = {
    "question": "该患者高血压的指南推荐和最新证据是什么？", "as_of": "2025-12-31",
    "english_terms": ["hypertension"], "mesh_terms": ["Hypertension"],
}


class JointSelectionTests(unittest.TestCase):
    def test_every_relevant_source_keeps_a_slot(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}))
        kinds = {item.document.kind for item in package.ranked_documents}
        self.assertEqual(kinds, {"guideline", "pubmed"})
        trace = package.trace["joint_selection"]
        self.assertTrue(all(value >= 1 for value in trace["selected_per_source"].values()))
        self.assertEqual(trace["intent_preference"], "guideline")

    def test_intent_share_respects_available_candidates(self):
        # 高血压 fixture 只有 1 条 current 指南（另一条 superseded 被排除），
        # 因此指南拿到的名额受候选数封顶；PubMed 补满其余名额。
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}))
        trace = package.trace["joint_selection"]
        self.assertEqual(trace["guaranteed_minimums"]["guideline"], 1)
        self.assertGreaterEqual(trace["selected_per_source"]["pubmed"], 2)
        # 糖尿病问题同样是指南优先意图，但指南候选仍在：保底生效。
        diabetes = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "该患者2型糖尿病的初始降糖治疗有哪些指南推荐和近期研究证据？",
            "as_of": "2025-12-31", "top_k": 6,
            "english_terms": ["type 2 diabetes", "metformin"],
            "mesh_terms": ["Diabetes Mellitus, Type 2"],
        }))
        d_trace = diabetes.trace["joint_selection"]
        self.assertEqual(d_trace["intent_preference"], "guideline")
        self.assertGreaterEqual(d_trace["selected_per_source"]["guideline"], 1)

    def test_evidence_update_intent_prefers_pubmed(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "该患者高血压的最新研究证据更新是什么？", "as_of": "2025-12-31",
            "english_terms": ["hypertension"], "top_k": 4,
        }))
        trace = package.trace["joint_selection"]
        self.assertEqual(trace["intent_preference"], "pubmed")
        self.assertGreaterEqual(trace["selected_per_source"]["pubmed"], len(package.ranked_documents) // 2)

    def test_single_source_query_not_affected(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({
            "question": "指南推荐", "source_kinds": ["pubmed"], "top_k": 4,
        }))
        self.assertTrue(all(item.document.kind == "pubmed" for item in package.ranked_documents))
        self.assertNotIn("guideline", package.trace["joint_selection"]["selected_per_source"])

    def test_small_top_k_warns_when_source_cannot_get_a_slot(self):
        # top_k=1 时无法同时给两个来源名额：应有可见性警告，且选择仍然确定。
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 1}))
        self.assertTrue(any("来源" in warning and "名额" in warning for warning in package.warnings))
        self.assertEqual(len(package.ranked_documents), 1)

    def test_merge_is_deterministic_and_interleaved(self):
        plan_pipeline = build_pipeline()
        first = plan_pipeline.retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 3}))
        second = plan_pipeline.retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 3}))
        self.assertEqual(
            [item.document.document_id for item in first.ranked_documents],
            [item.document.document_id for item in second.ranked_documents],
        )
        kinds = [item.document.kind for item in first.ranked_documents]
        # 交错：第一条是首选来源，第二条是另一来源（top_k=3 且两来源都有候选时）
        self.assertEqual(len(set(kinds)), 2)
        self.assertNotEqual(kinds[0], kinds[1])


class PerSourceRankingTests(unittest.TestCase):
    def test_idf_is_computed_within_each_source_pool(self):
        """同一术语在指南池与 PubMed 池内的 IDF 统计互不影响。"""
        cn_guideline = next(doc for doc in SYNTHETIC_GUIDELINES if doc.document_id == "guide-cn-asthma-2025")
        plan = build_query_plan(RetrievalRequest.from_dict({"question": "哮喘长期管理"}), [])
        guideline_pool = [cn_guideline]
        pubmed_pool = list(SYNTHETIC_PUBMED)
        guide_scores, _ = rank_documents(plan, guideline_pool, as_of="2025-12-31", top_k=3, source_label="指南")
        pubmed_scores, _ = rank_documents(plan, pubmed_pool, as_of="2025-12-31", top_k=3, source_label="PubMed")
        self.assertTrue(guide_scores)
        # 中文指南池内命中；英文 PubMed 池内同一查询全零（相关度门排除）。
        self.assertEqual(pubmed_scores, [])
        self.assertTrue(any("无检索相关度" in warning for warning in _warnings_for(plan, pubmed_pool)))

    def test_source_label_prefixes_warnings(self):
        plan = build_query_plan(RetrievalRequest.from_dict({"question": "完全无关查询"}), [])
        _, warnings = rank_documents(plan, list(SYNTHETIC_PUBMED), as_of="2025-12-31", top_k=3, source_label="PubMed")
        self.assertTrue(warnings)
        self.assertTrue(all(warning.startswith("PubMed：") for warning in warnings))


def _warnings_for(plan: QueryPlan, pool):
    _, warnings = rank_documents(plan, pool, as_of="2025-12-31", top_k=3, source_label="PubMed")
    return warnings


class MergeRuleUnitTests(unittest.TestCase):
    def _item(self, doc_id: str, kind: str, score: float, source: str = "fixture"):
        document = SourceDocument(source_id=source, document_id=doc_id, kind=kind, title=doc_id, text=doc_id)
        from rag.models import EvidenceStatus, RankedDocument
        return RankedDocument(
            document=document, score=score, lexical_score=score, fusion_score=0.0,
            authority_score=0.5, freshness_score=0.5, evidence_status=EvidenceStatus.CURRENT,
            matched_queries=(), rank_explanation=(),
        )

    def test_fill_is_rank_round_robin_and_ignores_raw_score_scales(self):
        per_source = {
            "guideline": [self._item("g1", "guideline", 100.0), self._item("g2", "guideline", 90.0)],
            "pubmed": [self._item("p1", "pubmed", 1.0), self._item("p2", "pubmed", 0.5)],
        }
        merged, trace, _ = merge_joint_selection(
            per_source, top_k=4, intent="clinical_question", source_order=["guideline", "pubmed"],
        )
        # 指南原始分数 100 >> PubMed 1.0，但补齐只用来源内排名轮转：
        # 两个来源各得 2 个名额，而不是指南按分数垄断。
        self.assertEqual(trace["selected_per_source"], {"guideline": 2, "pubmed": 2})
        self.assertEqual(len(merged), 4)
        self.assertEqual(trace["strategy"], "per_source_ranking + visibility_guarantee + rank_round_robin")

    def test_round_robin_gives_leftover_slots_to_the_richer_source(self):
        per_source = {
            "guideline": [self._item(f"g{i}", "guideline", 10.0 - i) for i in range(5)],
            "pubmed": [self._item("p1", "pubmed", 1.0)],
        }
        merged, trace, _ = merge_joint_selection(
            per_source, top_k=4, intent="clinical_question", source_order=["guideline", "pubmed"],
        )
        # 轮转一轮后 PubMed 无候选，剩余名额回到指南；总数仍是 top_k。
        self.assertEqual(trace["selected_per_source"], {"guideline": 3, "pubmed": 1})
        self.assertEqual(len(merged), 4)

    def test_empty_sources_do_not_receive_slots(self):
        per_source = {"guideline": [self._item("g1", "guideline", 1.0)], "pubmed": []}
        merged, trace, _ = merge_joint_selection(
            per_source, top_k=3, intent="guideline_recommendation", source_order=["guideline", "pubmed"],
        )
        self.assertEqual([item.document.document_id for item in merged], ["g1"])
        self.assertNotIn("pubmed", trace["guaranteed_minimums"])

    def test_no_candidates_returns_empty_with_warning(self):
        merged, trace, warnings = merge_joint_selection(
            {}, top_k=3, intent="clinical_question", source_order=["guideline"],
        )
        self.assertEqual(merged, [])
        self.assertTrue(warnings)
        self.assertEqual(trace["participating_sources"], [])


class QueryReviewTests(unittest.TestCase):
    def test_missing_english_terms_flags_review(self):
        plan = build_query_plan(RetrievalRequest.from_dict({"question": "高血压的指南推荐是什么？"}), [])
        review = review_query_plan(plan, english_source_present=True)
        self.assertEqual(review["status"], "review_needed")
        self.assertTrue(any("英文" in finding for finding in review["findings"]))

    def test_english_terms_present_is_ok(self):
        plan = build_query_plan(
            RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}), [],
        )
        review = review_query_plan(plan, english_source_present=True)
        self.assertEqual(review["status"], "ok")
        self.assertEqual(review["findings"], [])

    def test_numeric_and_negation_findings(self):
        plan = build_query_plan(RetrievalRequest.from_dict({
            "question": "2月龄婴儿发热的评估推荐是什么？病历未记录疫苗接种。",
            "english_terms": ["fever evaluation"],
        }), [])
        review = review_query_plan(plan, english_source_present=True)
        self.assertEqual(review["status"], "review_needed")
        joined = " ".join(review["findings"])
        self.assertIn("2", joined)      # 数值未在英文术语中承载
        self.assertIn("否定", joined)   # 否定类表述提示

    def test_review_does_not_change_plan(self):
        request = RetrievalRequest.from_dict({"question": "高血压指南推荐？"})
        plan = build_query_plan(request, [])
        review = review_query_plan(plan, english_source_present=True)
        self.assertIn("checks", review)
        self.assertNotIn("variants", review)  # 自检不是检索计划的一部分


class CitationLinkageTests(unittest.TestCase):
    """ranked_documents ↔ citations 的稳定外键契约（不再依赖下标同序）。"""

    def test_every_ranked_document_carries_its_citation_id(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}))
        citation_ids = {citation.citation_id for citation in package.citations}
        self.assertTrue(package.ranked_documents)
        for item in package.ranked_documents:
            self.assertIsNotNone(item.citation_id, "ranked 必须带 citation_id 外键")
            self.assertIn(item.citation_id, citation_ids)
        # 外键唯一且与列表长度一致，可反向一一对应
        self.assertEqual(len({item.citation_id for item in package.ranked_documents}), len(package.ranked_documents))
        self.assertEqual(len(package.citations), len(package.ranked_documents))

    def test_foreign_key_survives_grouping_and_open_citation(self):
        from rag.agent import EvidenceRAG
        from rag.tests.helpers import fixture_custom_registry

        rag = EvidenceRAG(EvidenceRetrievalPipeline(fixture_custom_registry()))
        result = rag.retrieve_evidence({**HYPERTENSION, "source_mode": "custom", "top_k": 4})
        # evidence_groups 是同一批对象的视图：分组后外键仍在，可直接用于关联引用。
        grouped = [item for rows in result["evidence_groups"].values() for item in rows]
        self.assertTrue(grouped)
        for item in grouped:
            self.assertIn(item["citation_id"], {c["citation_id"] for c in result["citations"]})
        first = result["ranked_documents"][0]["citation_id"]
        opened = rag.open_citation({"retrieval_id": result["retrieval_id"], "citation_id": first})
        self.assertEqual(opened["citation"]["citation_id"], first)

    def test_key_kind_and_retrieval_scope(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}))
        for item in package.ranked_documents:
            self.assertTrue(item.citation_id.startswith(package.retrieval_id + ":cit-"))

    def test_linkage_breaker_is_rejected_instead_of_silent_mismatch(self):
        """校验函数在错配时必须抛错（防止后续维护静默破坏关联）。"""
        from rag.pipeline import _assert_citation_linkage

        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 2}))
        _assert_citation_linkage(list(package.ranked_documents), list(package.citations))  # 正常无异
        with self.assertRaisesRegex(ValueError, "linkage broken"):
            _assert_citation_linkage(list(package.ranked_documents), list(package.citations[:-1]))
        with self.assertRaisesRegex(ValueError, "linkage broken"):
            _assert_citation_linkage(list(package.ranked_documents), list(reversed(package.citations)))


class JointTraceTests(unittest.TestCase):
    def test_trace_documents_joint_selection_and_review(self):
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "top_k": 4}))
        trace = package.trace
        self.assertIn("joint_selection", trace)
        self.assertIn("query_review", trace)
        # fixture 内无重复键：去重后数量与候选数一致（去重逻辑不丢失候选）。
        self.assertEqual(trace["deduplicated_count"], trace["candidate_count"])
        self.assertEqual(
            trace["joint_selection"]["candidates_per_source"],
            {"guideline": len(SYNTHETIC_GUIDELINES), "pubmed": len(SYNTHETIC_PUBMED)},
        )

    def test_patient_snapshot_flows_through_pipeline(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-patient-001", "2025-12-31")
        package = build_pipeline().retrieve(RetrievalRequest.from_dict({**HYPERTENSION, "patient_snapshot": snapshot, "top_k": 4}))
        self.assertTrue(package.patient_hits)
        self.assertTrue(package.applicability)
        self.assertEqual(package.trace["patient_context"]["received"], True)
        self.assertEqual(package.trace["patient_context"]["suppressed_future"], 1)


if __name__ == "__main__":
    unittest.main()
