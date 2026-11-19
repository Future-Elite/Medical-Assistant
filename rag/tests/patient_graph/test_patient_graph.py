"""Patient graph construction, graph retrieval and service integration (v6.7.3)."""
from __future__ import annotations

import json
import unittest

from rag.his import InMemoryHISAdapter, snapshot_from_dict
from rag.patient_graph import (
    PatientRecordLexicon,
    RetrievalBudget,
    build_patient_graph,
    default_lexicon,
    retrieve_patient_facts,
    retrieve_patient_graph,
)
from rag.tests.fixtures import SYNTHETIC_HIS_SNAPSHOTS


def _record(collection, event_type, record_path, fields, *, time=None, start=None, end=None):
    return {
        "collection": collection,
        "event_type": event_type,
        "record_path": record_path,
        "field_path": record_path,
        "fields": fields,
        "time": time,
        "start": start,
        "end": end,
    }


def _structured_snapshot(as_of="2024-02-21"):
    """One synthetic chart with the record types used by the fact qrels."""
    return snapshot_from_dict({
        "patient_ref": "syn-neo-graph-001",
        "as_of": as_of,
        "records": [
            _record("patient", "PATIENT", "r1:patient", {
                "gender": "女", "pna_days": 6, "ageText": "6天",
                "subject_role": "patient", "data_status": "observed_positive",
            }, time="2024-02-17"),
            _record("admission", "ADMISSION", "r1:admission", {
                "admissionDiagnosis": "新生儿高胆红素血症、早产儿",
                "admissionTime": "2024-02-17T00:00:00",
                "dischargeTime": "2024-02-19T00:00:00",
            }, time="2024-02-17"),
            _record("discharge", "DISCHARGE", "r1:discharge", {
                "dischargeDiagnosis": "新生儿高胆红素血症",
                "treatmentResult": "治愈",
                "dischargeCondition": "一般情况可",
            }, time="2024-02-19", start="2024-02-19"),
            _record("neonatal_record", "NEONATAL_RECORD", "r1:neonatal_record", {
                "gestationalAgeRaw": "35周+6天", "sourceFile": "note.html",
            }, time="2024-02-17"),
            _record("neonatal_summary", "NEONATAL_SUMMARY", "r1:neonatal_summary", {
                "presentingSymptoms": "皮肤黄染2天，母胎膜早破6小时，GBS筛查阴性",
                "laboratoryFindings": "总胆红素 251.9 umol/L",
            }, time="2024-02-17"),
            _record("treatment_course", "TREATMENT_COURSE", "r1:treatment_course", {
                "treatmentCourse": "给予蓝光光疗，抗感染治疗",
                "time_precision": "episode",
            }, time="2024-02-17", start="2024-02-17"),
            _record("derived_birth", "DERIVED_BIRTH", "r1:derived_birth", {
                "subject_role": "patient", "data_status": "observed_positive",
                "ga_days": 245, "ga_text": "35周", "birth_weight_g": 2860,
                "source_paths": ["r1:neonatal_record", "r1:neonatal_summary"],
            }, time="2024-02-17"),
            _record("derived_labs", "DERIVED_LABS", "r1:derived_labs", {
                "subject_role": "patient", "data_status": "observed_positive",
                "max_tbil_value": 251.9, "max_tbil_unit": "umol/L",
                "max_tbil_umol_l": 251.9, "blood_culture_status": "not_recorded",
                "source_paths": ["r1:neonatal_summary"],
            }, time="2024-02-18"),
            _record("derived_treatment", "DERIVED_TREATMENT", "r1:derived_treatment", {
                "subject_role": "patient", "data_status": "observed_positive",
                "phototherapy": True, "phototherapy_status": "observed_positive",
                "antibiotics": None, "antibiotics_status": "not_recorded",
                "hearing_screen": None, "hearing_screen_status": "not_recorded",
                "source_paths": ["r1:treatment_course"],
            }, time="2024-02-17", start="2024-02-17"),
            _record("derived_respiratory_support", "DERIVED_RESPIRATORY_SUPPORT", "r1:derived_resp", {
                "subject_role": "patient", "data_status": "observed_negative",
                "mode": "无", "respiratory_categories": [],
                "source_paths": ["r1:treatment_course", "r1:neonatal_summary"],
            }, time="2024-02-17", start="2024-02-17"),
        ],
    })


class PatientGraphBuildTests(unittest.TestCase):
    def test_graph_has_record_concept_subject_time_nodes(self):
        graph = build_patient_graph(_structured_snapshot())
        types = {node.node_type for node in graph.nodes}
        self.assertTrue({"patient", "record", "concept", "subject", "time", "episode"} <= types)
        self.assertEqual(graph.fact_count, 10)
        self.assertIn("record:r1:derived_birth", graph.node_by_id)
        self.assertIn("concept:ga", graph.node_by_id)
        self.assertIn("subject:patient", {node.node_id for node in graph.nodes})
        self.assertTrue(graph.snapshot_sha256)
        self.assertEqual(len(graph.snapshot_sha256), 64)

    def test_snapshot_hash_changes_with_visible_fact_value(self):
        first = build_patient_graph(_structured_snapshot())
        changed = _structured_snapshot()
        changed = snapshot_from_dict({
            "patient_ref": changed.patient_ref,
            "as_of": changed.as_of,
            "records": [
                {**fact.__dict__, "fields": {**fact.fields, "gender": "男"}}
                if fact.record_id == "r1:patient" else fact.__dict__
                for fact in changed.records
            ],
        })
        second = build_patient_graph(changed)
        self.assertNotEqual(first.snapshot_sha256, second.snapshot_sha256)

    def test_future_facts_are_not_in_the_graph(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        graph = build_patient_graph(snapshot)
        self.assertNotIn("record:reports[0]", graph.node_by_id)
        self.assertEqual(graph.fact_count, len(snapshot.visible_records()))

    def test_graph_payload_does_not_embed_raw_facts(self):
        graph = build_patient_graph(_structured_snapshot())
        payload = graph.to_dict()
        self.assertNotIn("records", payload)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("皮肤黄染2天", encoded)
        self.assertNotIn("给予蓝光光疗", encoded)

    def test_derived_records_keep_provenance_edges(self):
        graph = build_patient_graph(_structured_snapshot())
        edges = [
            edge for edge in graph.edges
            if edge.relation == "DERIVED_FROM" and edge.source == "record:r1:derived_labs"
        ]
        self.assertEqual([edge.target for edge in edges], ["record:r1:neonatal_summary"])
        paths = graph.shortest_path("patient:syn-neo-graph-001", "record:r1:derived_labs")
        self.assertIn("record:r1:derived_labs", paths)

    def test_subject_role_is_not_merged(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-subject", "as_of": "2026-01-01",
            "records": [
                _record("maternal", "PREGNANCY", "m[0]", {
                    "subject_role": "mother", "gbs_status": "positive",
                    "data_status": "observed_positive",
                }, time="2026-01-01"),
                _record("diagnoses", "DIAGNOSIS", "d[0]", {
                    "subject_role": "patient", "name": "新生儿肺炎",
                    "data_status": "observed_positive",
                }, time="2026-01-01"),
            ],
        })
        graph = build_patient_graph(snapshot)
        self.assertEqual(graph.node_by_id["record:m[0]"].attributes["subject"], "mother")
        self.assertEqual(graph.node_by_id["record:d[0]"].attributes["subject"], "patient")


class QueryResolutionTests(unittest.TestCase):
    def test_lexicon_distinguishes_admission_and_discharge_diagnosis(self):
        lex = default_lexicon()
        admission = lex.resolve_queries(["该患儿的入院诊断是什么？"])
        discharge = lex.resolve_queries(["该患儿的出院诊断是什么？"])
        self.assertEqual([item.intent for item in admission.intents], ["admission_diagnosis"])
        self.assertEqual([item.intent for item in discharge.intents], ["discharge_diagnosis"])

    def test_total_and_direct_bilirubin_are_not_merged(self):
        lex = default_lexicon()
        total = set(lex.match_concept_ids("总胆红素是多少？"))
        direct = set(lex.match_concept_ids("直接胆红素是多少？"))
        self.assertIn("concept:bilirubin_total", total)
        self.assertNotIn("concept:bilirubin_direct", total)
        self.assertIn("concept:bilirubin_direct", direct)
        self.assertNotIn("concept:bilirubin_total", direct)

    def test_pre_and_postductal_spo2_are_not_merged(self):
        lex = default_lexicon()
        pre = lex.resolve_queries(["导管前血氧是多少？"])
        generic = lex.resolve_queries(["血氧饱和度是多少？"])
        self.assertIn("concept:preductal_spo2", [spec.concept_id for spec in pre.concepts])
        self.assertNotIn("concept:spo2", [spec.concept_id for spec in pre.concepts])
        self.assertIn("concept:spo2", [spec.concept_id for spec in generic.concepts])

    def test_culture_specimen_is_respected(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-culture", "as_of": "2026-01-02",
            "records": [
                _record("labs", "CULTURE", "labs[0]", {
                    "subject_role": "patient", "specimen": "urine",
                    "culture_status": "positive", "data_status": "observed_positive",
                }, time="2026-01-01"),
            ],
        })
        graph = build_patient_graph(snapshot)
        concepts = set(graph.concept_ids_for_record("record:labs[0]"))
        self.assertIn("concept:urine_culture", concepts)
        self.assertNotIn("concept:blood_culture", concepts)
        blood_hits, _ = retrieve_patient_graph(graph, ["血培养结果如何？"], top_k=5)
        self.assertEqual(blood_hits, ())
        urine_hits, _ = retrieve_patient_graph(graph, ["尿培养结果如何？"], top_k=5)
        self.assertEqual(urine_hits[0].record_path, "labs[0]")

    def test_generic_spo2_is_not_attached_to_preductal_record(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-spo2", "as_of": "2026-01-02",
            "records": [
                _record("vitals", "MEASUREMENT", "vitals[0]", {
                    "subject_role": "patient", "preductal_spo2_percent": 88,
                    "postductal_spo2_percent": 82, "data_status": "observed_positive",
                }, time="2026-01-01"),
            ],
        })
        graph = build_patient_graph(snapshot)
        concepts = set(graph.concept_ids_for_record("record:vitals[0]"))
        self.assertIn("concept:preductal_spo2", concepts)
        self.assertIn("concept:postductal_spo2", concepts)
        self.assertNotIn("concept:spo2", concepts)

    def test_unrecorded_phrase_is_not_treated_as_a_value(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-unrecorded", "as_of": "2026-01-02",
            "records": [
                _record("neonatal_record", "NEONATAL_RECORD", "nr[0]", {
                    "gestationalAgeRaw": "无提及孕周", "data_status": "not_recorded",
                }, time="2026-01-01"),
            ],
        })
        graph = build_patient_graph(snapshot)
        self.assertEqual(graph.node_by_id["record:nr[0]"].attributes["status"], "not_recorded")
        self.assertEqual(graph.concept_status("record:nr[0]", "concept:ga"), "not_recorded")
        hits, _ = retrieve_patient_graph(graph, ["该患儿出生胎龄是多少周？"], top_k=5)
        self.assertEqual(hits[0].record_path, "nr[0]")
        self.assertEqual(hits[0].status, "not_recorded")

    def test_unresolved_text_is_reported_not_forced(self):
        lex = default_lexicon()
        result = lex.resolve_queries(["这个患儿是否存在某种未知的罕见代谢问题？"])
        self.assertEqual(result.intents, ())
        self.assertEqual(result.concepts, ())
        self.assertTrue(result.unresolved_terms)

    def test_english_and_chinese_aliases_resolve(self):
        lex = default_lexicon()
        for query, intent in (
            ("What is the gestational age?", "ga"),
            ("出院诊断是什么？", "discharge_diagnosis"),
            ("是否使用抗生素？", "antibiotic"),
        ):
            result = lex.resolve_queries([query])
            self.assertIn(intent, [item.intent for item in result.intents])


class GraphRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = _structured_snapshot()
        cls.graph = build_patient_graph(cls.snapshot)

    def test_each_question_intent_localizes_the_expected_record(self):
        cases = {
            "该患儿的性别是什么？": "r1:patient",
            "该患儿入院日龄是多少天？": "r1:patient",
            "该患儿出生胎龄是多少周？": "r1:derived_birth",
            "该患儿的入院诊断是什么？": "r1:admission",
            "该患儿的出院诊断是什么？": "r1:discharge",
            "该患儿的治疗结果是什么？": "r1:discharge",
            "该患儿这次住院多少天？": "r1:admission",
            "该患儿住院期间最高总胆红素是多少？": "r1:derived_labs",
            "该患儿住院期间是否接受光疗？": "r1:derived_treatment",
            "该患儿住院期间是否使用抗生素？": "r1:derived_treatment",
            "该患儿是否做过听力筛查？": "r1:derived_treatment",
            "该患儿接受过哪种呼吸支持？": "r1:derived_resp",
        }
        for question, expected in cases.items():
            hits, trace = retrieve_patient_graph(self.graph, [question], top_k=5)
            self.assertTrue(hits, question)
            self.assertEqual(hits[0].record_path, expected, question)
            self.assertIn(trace["status"], {"ok", "partial"})

    def test_paraphrased_questions_localize_the_expected_record(self):
        cases = {
            "这个宝宝是男孩还是女孩？": "r1:patient",
            "孩子出生几天了？": "r1:patient",
            "妈妈怀孕多少周生的？": "r1:derived_birth",
            "刚入院时诊断是什么？": "r1:admission",
            "出院诊断写了什么？": "r1:discharge",
            "最后治好了吗？": "r1:discharge",
            "住了几天院？": "r1:admission",
            "胆红素最高到多少？": "r1:derived_labs",
            "有没有照蓝光？": "r1:derived_treatment",
            "用没用消炎药？": "r1:derived_treatment",
            "听力过了没有？": "r1:derived_treatment",
            "上呼吸机了吗？": "r1:derived_resp",
        }
        for question, expected in cases.items():
            hits, _ = retrieve_patient_graph(self.graph, [question], top_k=5)
            self.assertTrue(hits, question)
            self.assertEqual(hits[0].record_path, expected, question)

    def test_hit_carries_status_concepts_and_graph_path(self):
        hits, _ = retrieve_patient_graph(self.graph, ["该患儿住院期间是否使用抗生素？"], top_k=5)
        self.assertEqual(hits[0].record_path, "r1:derived_treatment")
        self.assertEqual(hits[0].status, "not_recorded")
        self.assertIn("concept:antibiotic", hits[0].concept_ids)
        self.assertTrue(hits[0].graph_path)
        self.assertTrue(any("MENTIONS" in step for step in hits[0].graph_path))

    def test_absent_concept_returns_no_results_not_a_bigram_match(self):
        hits, trace = retrieve_patient_graph(self.graph, ["有没有呼吸暂停？"], top_k=5)
        self.assertEqual(hits, ())
        self.assertEqual(trace["status"], "no_results")

    def test_provenance_expansion_reaches_the_raw_record(self):
        hits, trace = retrieve_patient_graph(self.graph, ["该患儿接受过哪种呼吸支持？"], top_k=10)
        paths = [hit.record_path for hit in hits]
        self.assertIn("r1:derived_resp", paths)
        self.assertIn("r1:treatment_course", paths)
        expanded = [hit for hit in hits if hit.record_path == "r1:treatment_course"][0]
        self.assertTrue(any("DERIVED_FROM" in step for step in expanded.graph_path))
        self.assertTrue(trace["expanded"])

    def test_latest_cue_prefers_newer_lab_record(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-latest", "as_of": "2024-02-25",
            "records": [
                _record("derived_labs", "DERIVED_LABS", "lab[0]", {
                    "data_status": "observed_positive", "max_tbil_umol_l": 200,
                }, time="2024-02-18"),
                _record("derived_labs", "DERIVED_LABS", "lab[1]", {
                    "data_status": "observed_positive", "max_tbil_umol_l": 280,
                }, time="2024-02-24"),
            ],
        })
        graph = build_patient_graph(snapshot)
        hits, _ = retrieve_patient_graph(graph, ["最新总胆红素是多少？"], top_k=5)
        self.assertEqual(hits[0].record_path, "lab[1]")

    def test_subject_cue_filters_mother_records(self):
        snapshot = snapshot_from_dict({
            "patient_ref": "syn-mother", "as_of": "2026-01-02",
            "records": [
                _record("maternal", "PREGNANCY", "m[0]", {
                    "subject_role": "mother", "gbs_status": "positive",
                    "data_status": "observed_positive",
                }, time="2026-01-01"),
                _record("diagnoses", "DIAGNOSIS", "d[0]", {
                    "subject_role": "patient", "name": "新生儿肺炎",
                    "data_status": "observed_positive",
                }, time="2026-01-01"),
            ],
        })
        graph = build_patient_graph(snapshot)
        hits, _ = retrieve_patient_graph(graph, ["母亲GBS结果是什么？"], top_k=5)
        self.assertTrue(hits)
        self.assertTrue(all(hit.collection == "maternal" for hit in hits))

    def test_not_recorded_field_is_returned_as_missing_not_normal(self):
        hits, _ = retrieve_patient_graph(self.graph, ["听力筛查结果如何？"], top_k=5)
        self.assertEqual(hits[0].record_path, "r1:derived_treatment")
        self.assertEqual(hits[0].status, "not_recorded")

    def test_budget_truncation_is_explicit(self):
        hits, trace = retrieve_patient_graph(
            self.graph, ["最新胆红素和光疗记录"], top_k=20,
            budget=RetrievalBudget(max_hops=2, max_nodes=1),
        )
        self.assertTrue(hits)
        self.assertTrue(trace["budget"]["truncated"])
        self.assertTrue(trace["warnings"])

    def test_same_snapshot_and_budget_reproduce_order(self):
        first, _ = retrieve_patient_graph(self.graph, ["新生儿肺炎 光疗"], top_k=10)
        second, _ = retrieve_patient_graph(self.graph, ["新生儿肺炎 光疗"], top_k=10)
        self.assertEqual([hit.record_path for hit in first], [hit.record_path for hit in second])


class ServiceTests(unittest.TestCase):
    def test_service_returns_graph_hits_and_stable_trace(self):
        snapshot = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS).get_snapshot("syn-neo-001", "2026-04-15")
        hits, trace = retrieve_patient_facts(snapshot, ("新生儿呼吸窘迫综合征",))
        self.assertTrue(hits)
        self.assertEqual(trace["patient_graph"]["engine"], "patient_graph")
        self.assertIn("snapshot_sha256", trace["patient_graph"]["graph"])
        self.assertTrue(hits[0].graph_path)
        self.assertEqual(trace["patient_hit_count"], len(hits))

    def test_service_does_not_mutate_or_fabricate_for_empty_snapshot(self):
        snapshot = snapshot_from_dict({"patient_ref": "empty", "as_of": "2026-01-01", "records": []})
        hits, trace = retrieve_patient_facts(snapshot, ("该患儿出生胎龄是多少周？",))
        self.assertEqual(hits, ())
        self.assertEqual(trace["patient_graph"]["status"], "no_results")
        self.assertEqual(trace["visible_fact_count"], 0)


class LexiconValidationTests(unittest.TestCase):
    def test_duplicate_concepts_are_rejected(self):
        with self.assertRaises(ValueError):
            PatientRecordLexicon(
                [default_lexicon().concepts[0], default_lexicon().concepts[0]],
                [],
            )

    def test_unknown_intent_concept_is_rejected(self):
        from rag.patient_graph.lexicon import ConceptSpec, IntentSpec
        with self.assertRaises(ValueError):
            PatientRecordLexicon(
                [ConceptSpec("concept:a", "A", "fact", ("a",), (), ())],
                [IntentSpec("x", ("x",), ("concept:missing",), (), ())],
            )


if __name__ == "__main__":
    unittest.main()
