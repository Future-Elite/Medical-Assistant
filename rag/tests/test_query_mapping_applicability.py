"""Tests for reviewed term mapping and structured guideline applicability."""
from __future__ import annotations

from dataclasses import replace
import unittest

from rag.applicability import assess_applicability, parse_applicability_criteria
from rag.connectors import ConnectorRegistry, InMemoryConnector
from rag.his import PatientFact, PatientSnapshot, snapshot_from_dict
from rag.models import RetrievalRequest, SourceDocument, _jsonable, parse_term_mapping
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import _safe_term
from rag.query import build_query_plan, review_query_plan
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS

SOURCES = [
    {"source_id": "guideline-fixture", "kinds": ["guideline"]},
    {"source_id": "pubmed-fixture", "kinds": ["pubmed"]},
]


class TermMappingParseTests(unittest.TestCase):
    def test_row_list_keeps_concept_english_mesh_and_aliases(self):
        entries = parse_term_mapping([
            {
                "concept": "新生儿呼吸窘迫综合征",
                "aliases": ["NRDS"],
                "english": ["respiratory distress syndrome"],
                "mesh": ["Respiratory Distress Syndrome, Newborn"],
                "reviewed_by": "clinician-a",
            }
        ])
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.concept, "新生儿呼吸窘迫综合征")
        self.assertEqual(entry.english, ("respiratory distress syndrome",))
        self.assertEqual(entry.mesh, ("Respiratory Distress Syndrome, Newborn",))
        self.assertEqual(entry.aliases, ("NRDS",))
        self.assertEqual(entry.metadata["reviewed_by"], "clinician-a")
        self.assertEqual(entry.surfaces(), ("新生儿呼吸窘迫综合征", "NRDS"))

    def test_object_and_legacy_list_forms_are_accepted(self):
        entries = parse_term_mapping({
            "新生儿呼吸窘迫综合征": {
                "english": ["respiratory distress syndrome"],
                "mesh": ["Respiratory Distress Syndrome, Newborn"],
            },
            "肺表面活性物质": ["pulmonary surfactant"],
        })
        by_concept = {entry.concept: entry for entry in entries}
        self.assertEqual(by_concept["新生儿呼吸窘迫综合征"].mesh, ("Respiratory Distress Syndrome, Newborn",))
        self.assertEqual(by_concept["肺表面活性物质"].english, ("pulmonary surfactant",))
        self.assertEqual(by_concept["肺表面活性物质"].mesh, ())

    def test_duplicate_or_empty_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_term_mapping([
                {"concept": "x", "english": ["a"]},
                {"concept": "X", "english": ["b"]},
            ])
        with self.assertRaises(ValueError):
            parse_term_mapping([{"concept": "x"}])
        with self.assertRaises(ValueError):
            parse_term_mapping([{"concept": "", "english": ["a"]}])

    def test_from_dict_exposes_term_mapping(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征",
            "term_mapping": [{"concept": "新生儿呼吸窘迫综合征", "english": ["RDS"]}],
        })
        self.assertEqual(request.term_mapping[0].concept, "新生儿呼吸窘迫综合征")
        self.assertEqual(request.term_mapping[0].english, ("RDS",))


class MappingQueryPlanTests(unittest.TestCase):
    def _request(self, **overrides):
        payload = {
            "question": "该患儿出生胎龄30周+2天，CPAP下FiO2 0.30，新生儿呼吸窘迫综合征的肺表面活性物质指征？",
            "patient_terms": ["新生儿呼吸窘迫综合征", "肺表面活性物质"],
            "facets": {"population": ["早产儿"]},
            "term_mapping": [
                {
                    "concept": "新生儿呼吸窘迫综合征", "aliases": ["NRDS"],
                    "english": ["respiratory distress syndrome"],
                    "mesh": ["Respiratory Distress Syndrome, Newborn"],
                },
                {
                    "concept": "肺表面活性物质",
                    "english": ["pulmonary surfactant"],
                    "mesh": ["Pulmonary Surfactants"],
                },
                {
                    "concept": "早产儿",
                    "english": ["preterm infant"],
                    "mesh": ["Infant, Premature"],
                },
                {
                    "concept": "支气管肺发育不良",
                    "english": ["bronchopulmonary dysplasia"],
                    "mesh": ["Bronchopulmonary Dysplasia"],
                },
            ],
        }
        payload.update(overrides)
        return RetrievalRequest.from_dict(payload)

    def test_concepts_expand_only_from_matched_mapping_rows(self):
        plan = build_query_plan(self._request(), SOURCES)
        self.assertIn("respiratory distress syndrome", plan.english_terms)
        self.assertIn("pulmonary surfactant", plan.english_terms)
        self.assertIn("preterm infant", plan.english_terms)
        self.assertNotIn("bronchopulmonary dysplasia", plan.english_terms)
        self.assertIn("Respiratory Distress Syndrome, Newborn", plan.mesh_terms)
        self.assertNotIn("Bronchopulmonary Dysplasia", plan.mesh_terms)
        statuses = {entry["concept"]: entry["status"] for entry in plan.mapping_trace}
        self.assertEqual(statuses["新生儿呼吸窘迫综合征"], "expanded")
        self.assertEqual(statuses["支气管肺发育不良"], "not_matched")
        expanded = next(entry for entry in plan.mapping_trace if entry["concept"] == "新生儿呼吸窘迫综合征")
        self.assertEqual(expanded["matched_in"], "question")
        self.assertEqual(expanded["source"], "reviewed_term_mapping")

    def test_mapping_path_builds_role_labelled_concept_variants(self):
        plan = build_query_plan(self._request(), SOURCES)
        roles = [entry["role"] for entry in plan.variant_trace]
        self.assertEqual(roles[0], "original_question")
        self.assertIn("zh_concepts", roles)
        self.assertIn("mapping_english", roles)
        self.assertIn("mapping_mesh", roles)
        self.assertIn("mapping_combined", roles)
        self.assertIn("english_and_mesh", roles)
        self.assertIn("concepts_and_terms", roles)
        self.assertIn("respiratory distress syndrome", plan.variants)
        self.assertIn("Respiratory Distress Syndrome, Newborn", plan.variants)
        self.assertIn("respiratory distress syndrome Respiratory Distress Syndrome, Newborn", plan.variants)

    def test_ascii_alias_uses_word_boundaries(self):
        request = self._request(
            question="该NRDS患儿是否需要用肺表面活性物质？",
            patient_terms=[],
            facets={},
            term_mapping=[
                {"concept": "新生儿呼吸窘迫综合征", "aliases": ["RDS"],
                 "english": ["respiratory distress syndrome"]},
            ],
        )
        plan = build_query_plan(request, SOURCES)
        # "RDS" must not match inside "NRDS".
        self.assertEqual(plan.mapping_trace[0]["status"], "not_matched")

        request_alias = self._request(
            question="该NRDS患儿是否需要用肺表面活性物质？",
            patient_terms=[],
            facets={},
            term_mapping=[
                {"concept": "新生儿呼吸窘迫综合征", "aliases": ["NRDS"],
                 "english": ["respiratory distress syndrome"]},
            ],
        )
        plan_alias = build_query_plan(request_alias, SOURCES)
        self.assertEqual(plan_alias.mapping_trace[0]["status"], "expanded")
        self.assertEqual(plan_alias.mapping_trace[0]["matched_surface"], "NRDS")

    def test_unmatched_mapping_does_not_switch_query_design(self):
        request = RetrievalRequest.from_dict({
            "question": "该患儿黄疸怎么处理？",
            "term_mapping": [{"concept": "新生儿呼吸窘迫综合征", "english": ["RDS"]}],
        })
        plan = build_query_plan(request, SOURCES)
        self.assertEqual(plan.variant_trace, ())
        self.assertEqual(plan.mapping_trace[0]["status"], "not_matched")
        self.assertEqual(plan.variants[0], request.question)

    def test_mapping_gating_changes_retrieval_not_just_trace(self):
        """An unmatched mapping row must not contribute external query terms.

        The BPD document shares no token with the RDS question.  With a mapping
        table it is excluded; with the same terms passed as legacy free lists it
        is retrieved.  This proves the mapping gate changes the retrieval input,
        not only the trace.
        """
        rds = SourceDocument(
            source_id="g", document_id="rds-doc", kind="guideline",
            title="Respiratory distress syndrome guideline",
            text="Respiratory distress syndrome in preterm infants: surfactant and CPAP recommendations.",
            metadata={"mesh_terms": "Respiratory Distress Syndrome, Newborn"},
        )
        bpd = SourceDocument(
            source_id="g", document_id="bpd-doc", kind="guideline",
            title="Bronchopulmonary dysplasia guideline",
            text="Bronchopulmonary dysplasia definition and oxygen dependence in survivors.",
            metadata={"mesh_terms": "Bronchopulmonary Dysplasia"},
        )
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("g", ("guideline",), (rds, bpd)))
        pipeline = EvidenceRetrievalPipeline(registry)
        common = {
            "question": "该患儿新生儿呼吸窘迫综合征的肺表面活性物质指征是什么？",
            "patient_terms": ["新生儿呼吸窘迫综合征"],
            "source_kinds": ["guideline"],
            "top_k": 2,
            "source_mode": "custom",
        }
        mapping_package = pipeline.retrieve(RetrievalRequest.from_dict({
            **common,
            "term_mapping": [
                {"concept": "新生儿呼吸窘迫综合征", "english": ["respiratory distress syndrome"],
                 "mesh": ["Respiratory Distress Syndrome, Newborn"]},
                {"concept": "支气管肺发育不良", "english": ["bronchopulmonary dysplasia"],
                 "mesh": ["Bronchopulmonary Dysplasia"]},
            ],
        }))
        legacy_package = pipeline.retrieve(RetrievalRequest.from_dict({
            **common,
            "english_terms": ["respiratory distress syndrome", "bronchopulmonary dysplasia"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn", "Bronchopulmonary Dysplasia"],
        }))
        self.assertEqual([item.document.document_id for item in mapping_package.ranked_documents], ["rds-doc"])
        self.assertEqual(mapping_package.query_plan.mapping_trace[1]["status"], "not_matched")
        self.assertIn("bpd-doc", [item.document.document_id for item in legacy_package.ranked_documents])

    def test_legacy_fields_still_work_without_mapping(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征",
            "english_terms": ["respiratory distress syndrome"],
            "mesh_terms": ["Respiratory Distress Syndrome, Newborn"],
            "user_terms": {"新生儿呼吸窘迫综合征": ["neonatal RDS"]},
        })
        plan = build_query_plan(request, SOURCES)
        self.assertEqual(plan.variant_trace, ())
        self.assertIn("respiratory distress syndrome", plan.english_terms)
        self.assertIn("neonatal RDS", plan.english_terms)
        self.assertTrue(any(entry.get("source") == "reviewed_user_terms" for entry in plan.expansion_trace))

    def test_mesh_only_mapping_row_still_allows_legacy_english_mapping(self):
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征",
            "term_mapping": [{"concept": "新生儿呼吸窘迫综合征", "mesh": ["Respiratory Distress Syndrome, Newborn"]}],
            "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]},
        })
        plan = build_query_plan(request, SOURCES)
        self.assertIn("Respiratory Distress Syndrome, Newborn", plan.mesh_terms)
        self.assertIn("respiratory distress syndrome", plan.english_terms)

    def test_query_review_flags_unmatched_mapping_rows(self):
        plan = build_query_plan(self._request(), SOURCES)
        review = review_query_plan(plan, english_source_present=True)
        check = next(item for item in review["checks"] if item["item"] == "term_mapping_coverage")
        self.assertEqual(check["status"], "review_needed")
        self.assertTrue(any("支气管肺发育不良" in finding for finding in review["findings"]))

    def test_pubmed_query_uses_tiab_and_mesh_tags(self):
        plan = build_query_plan(self._request(), SOURCES)
        query = _safe_term(plan)
        self.assertIn('"respiratory distress syndrome"[tiab]', query)
        self.assertIn('"Respiratory Distress Syndrome, Newborn"[MeSH Terms]', query)
        self.assertIn('"preterm infant"[tiab]', query)
        self.assertIn('"Infant, Premature"[MeSH Terms]', query)
        self.assertIn(" AND (", query)

    def test_pubmed_query_does_not_treat_disease_row_as_population(self):
        plan = build_query_plan(self._request(), SOURCES)
        query = _safe_term(plan)
        topic, population = query.split(" AND (", 1)
        self.assertIn("Respiratory Distress Syndrome, Newborn", topic)
        self.assertNotIn("Respiratory Distress Syndrome, Newborn", population)
        self.assertIn("preterm infant", population)


class ApplicabilityCriteriaTests(unittest.TestCase):
    def _document(self, criteria):
        normalized = []
        for criterion in criteria:
            row = dict(criterion)
            row.setdefault("review_status", "reviewed")
            row.setdefault("source", {"locator": "test guideline", "quote": "test quote"})
            normalized.append(row)
        return SourceDocument(
            source_id="test", document_id="guide", kind="guideline", title="Guide", text="Guide text",
            metadata={"applicability_criteria": normalized},
        )

    def _snapshot(self):
        return SYNTHETIC_HIS_SNAPSHOTS["syn-neo-001"]

    def test_all_required_criteria_match(self):
        document = self._document([
            {"criterion_id": "dx", "field": "diagnosis", "operator": "contains_any",
             "value": ["呼吸窘迫综合征"], "required": True},
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
             "unit": "days", "required": True},
            {"criterion_id": "fio2", "field": "respiratory_support.fio2", "operator": "ge",
             "value": 0.30, "unit": "fraction", "required": True},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual([item.status for item in assessment.criteria], ["match", "match", "match"])
        self.assertIn("diagnoses[0]", assessment.criteria[0].patient_record_paths)
        self.assertIn("respiratory_support[0]", assessment.criteria[2].patient_record_paths)

    def test_required_numeric_mismatch_is_mismatch(self):
        document = self._document([
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 200, "required": True},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "mismatch")
        self.assertEqual(assessment.criteria[0].status, "mismatch")
        self.assertEqual(assessment.criteria[0].observed_value, "212")

    def test_missing_or_pending_never_becomes_negative(self):
        document = self._document([
            {"criterion_id": "culture", "field": "culture_status", "operator": "in",
             "value": ["negative"], "required": True},
            {"criterion_id": "gbs", "field": "maternal.gbs_status", "operator": "in",
             "value": ["positive"], "subject_role": "mother", "required": True},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertEqual([item.status for item in assessment.criteria], ["unknown", "unknown"])
        self.assertIn("不能作为阴性或阳性证据", assessment.criteria[0].reason)

    def test_patient_role_does_not_read_mother_facts(self):
        document = self._document([
            {"criterion_id": "gbs", "field": "gbs_status", "operator": "in",
             "value": ["positive"], "required": True},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")

    def test_mother_role_reads_maternal_fact(self):
        document = self._document([
            {"criterion_id": "gbs", "field": "maternal.gbs_status", "operator": "in",
             "value": ["positive"], "subject_role": "mother", "required": True},
        ])
        assessment = assess_applicability(document, SYNTHETIC_HIS_SNAPSHOTS["syn-neo-002"], ())
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual(assessment.criteria[0].status, "match")

    def test_optional_mismatch_does_not_override_required_match(self):
        document = self._document([
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259, "required": True},
            {"criterion_id": "fio2", "field": "respiratory_support.fio2", "operator": "ge",
             "value": 0.80, "unit": "fraction", "required": False},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual(assessment.criteria[1].status, "mismatch")

    def test_unit_mismatch_is_unknown_not_mismatch(self):
        document = self._document([
            {"criterion_id": "fio2", "field": "respiratory_support.fio2", "operator": "ge",
             "value": 30, "unit": "percent", "required": True},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertIn("单位不一致", assessment.criteria[0].reason)

    def test_unsupported_operator_is_unknown(self):
        document = self._document([
            {"criterion_id": "ga", "field": "ga_days", "operator": "approximately", "value": 212},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertIn("不支持的操作符", assessment.criteria[0].reason)

    def test_draft_or_scope_only_criterion_is_not_used(self):
        for review_status in ("draft", "scope_only"):
            document = self._document([
                {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
                 "review_status": review_status},
            ])
            assessment = assess_applicability(document, self._snapshot(), ())
            self.assertEqual(assessment.status, "unknown", review_status)
            self.assertEqual(assessment.criteria[0].status, "unknown")
            self.assertEqual(assessment.criteria[0].review_status, review_status)
            self.assertIn("条件未审核", assessment.criteria[0].reason)
            self.assertIn("未审核或缺少原文定位", assessment.reason)

    def test_missing_source_locator_is_unknown(self):
        document = SourceDocument(
            source_id="test", document_id="guide", kind="guideline", title="Guide", text="Guide text",
            metadata={"applicability_criteria": [
                {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
                 "review_status": "reviewed"},
            ]},
        )
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertIn("缺少原文定位", assessment.criteria[0].reason)

    def test_synthetic_fixture_criterion_participates(self):
        document = self._document([
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
             "review_status": "synthetic_fixture"},
        ])
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual(assessment.criteria[0].status, "match")

    def test_any_of_accepts_one_matching_criterion(self):
        document = SourceDocument(
            source_id="test", document_id="guide", kind="guideline", title="Guide", text="Guide text",
            metadata={"applicability_criteria": {"logic": "any_of", "criteria": [
                {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 200,
                 "review_status": "reviewed", "source": {"locator": "test", "quote": "test"}},
                {"criterion_id": "dx", "field": "diagnosis", "operator": "contains_any",
                 "value": ["呼吸窘迫综合征"], "review_status": "reviewed",
                 "source": {"locator": "test", "quote": "test"}},
            ]}},
        )
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual([item.status for item in assessment.criteria], ["mismatch", "match"])
        self.assertIn("any_of", assessment.reason)

    def test_any_of_requires_all_mismatch_for_mismatch(self):
        document = SourceDocument(
            source_id="test", document_id="guide", kind="guideline", title="Guide", text="Guide text",
            metadata={"applicability_criteria": {"logic": "any_of", "criteria": [
                {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 200,
                 "review_status": "reviewed", "source": {"locator": "test", "quote": "test"}},
                {"criterion_id": "culture", "field": "culture_status", "operator": "in",
                 "value": ["negative"], "review_status": "reviewed",
                 "source": {"locator": "test", "quote": "test"}},
            ]}},
        )
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertIn("any_of", assessment.reason)

    def test_unparseable_criteria_are_reported(self):
        document = SourceDocument(
            source_id="test", document_id="guide", kind="guideline", title="Guide", text="Guide text",
            metadata={"applicability_criteria": {"not": "a list"}},
        )
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertIn("无法解析", assessment.reason)

    def test_legacy_population_criteria_semantics_are_preserved(self):
        document = SourceDocument(
            source_id="test", document_id="legacy", kind="guideline", title="Guide", text="Guide text",
            metadata={"population_criteria": {"population": ["新生儿"], "missing": ["成人"]}},
        )
        assessment = assess_applicability(document, self._snapshot(), ())
        self.assertEqual(assessment.status, "unknown")
        self.assertEqual(assessment.matched_patient_fields, ("population",))
        self.assertEqual(assessment.missing_patient_fields, ("missing",))
        self.assertEqual(assessment.criteria, ())

    def test_no_snapshot_or_no_criteria_is_not_assessed(self):
        document = self._document([{"criterion_id": "ga", "field": "ga_days", "operator": "exists"}])
        self.assertEqual(assess_applicability(document, None, ()).status, "not_assessed")
        self.assertEqual(
            assess_applicability(SourceDocument("s", "d", "guideline", "t", "x"), self._snapshot(), ()).status,
            "not_assessed",
        )

    def test_parse_applicability_criteria_accepts_wrapped_object(self):
        criteria, errors = parse_applicability_criteria({"criteria": [
            {"criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259},
        ]})
        self.assertEqual(len(criteria), 1)
        self.assertEqual(errors, ())
        self.assertEqual(criteria[0].expected, ("259",))

    def test_pipeline_returns_structured_applicability(self):
        guideline = replace(
            SYNTHETIC_GUIDELINES[0],
            metadata={
                **SYNTHETIC_GUIDELINES[0].metadata,
                "applicability_criteria": [
                    {"criterion_id": "dx", "field": "diagnosis", "operator": "contains_any",
                     "value": ["呼吸窘迫综合征"], "required": True,
                     "review_status": "reviewed", "source": {"locator": "test", "quote": "test"}},
                    {"criterion_id": "culture", "field": "culture_status", "operator": "in",
                     "value": ["negative"], "required": False,
                     "review_status": "reviewed", "source": {"locator": "test", "quote": "test"}},
                ],
            },
        )
        registry = ConnectorRegistry()
        registry.register(InMemoryConnector("guideline-fixture", ("guideline",), (guideline,)))
        pipeline = EvidenceRetrievalPipeline(registry)
        request = RetrievalRequest.from_dict({
            "question": "新生儿呼吸窘迫综合征的肺表面活性物质指征？",
            "patient_terms": ["新生儿呼吸窘迫综合征"],
            "term_mapping": [
                {"concept": "新生儿呼吸窘迫综合征", "english": ["respiratory distress syndrome"]},
            ],
            "source_kinds": ["guideline"],
            "patient_snapshot": {
                "patient_ref": "syn-neo-001", "as_of": "2026-04-15",
                "facts": [
                    {"collection": "diagnoses", "event_type": "diagnosis", "record_path": "diagnoses[0]",
                     "field_path": "diagnoses[0].name", "time": "2026-04-10",
                     "fields": {"subject_role": "patient", "name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}},
                ],
            },
        })
        package = pipeline.retrieve(request)
        self.assertTrue(package.applicability)
        assessment = package.applicability[0]
        self.assertEqual(assessment.status, "possible_match")
        self.assertEqual(assessment.criteria[0].criterion_id, "dx")
        self.assertEqual(assessment.criteria[0].status, "match")
        payload = package.to_dict()
        self.assertEqual(payload["applicability"][0]["criteria"][0]["status"], "match")


class ApplicabilityPreviewTests(unittest.TestCase):
    """试算预览：未参与判定的条件附 preview 试算；status 不变，预览不构成判定。"""

    def _doc(self, review_status):
        return SourceDocument.from_dict({
            "document_id": "guide-x", "kind": "guideline", "title": "t", "text": "t",
            "metadata": {"applicability_criteria": [{
                "criterion_id": "ga", "field": "ga_days", "operator": "lt", "value": 259,
                "unit": "days", "required": True, "review_status": review_status,
                "label": "早产儿", "source": {"locator": "指南标题/适用人群", "quote": "preterm"},
            }]},
        })

    def _snap(self):
        return snapshot_from_dict({
            "patient_ref": "syn-1", "as_of": "2026-04-15", "source": "synthetic_chart",
            "facts": [{
                "collection": "birth", "event_type": "birth", "record_path": "birth[0]",
                "fields": {"ga_days": 212}, "time": "2026-04-01",
            }],
        })

    def test_default_off_omits_preview_key(self):
        result = assess_applicability(self._doc("scope_only"), self._snap(), ())
        row = result.criteria[0]
        self.assertEqual(row.status, "unknown")
        self.assertIsNone(row.preview)
        self.assertNotIn("preview", _jsonable(row))   # 默认输出与旧版逐字段一致

    def test_preview_shows_trial_judgment_without_changing_status(self):
        result = assess_applicability(self._doc("scope_only"), self._snap(), (), preview=True)
        row = result.criteria[0]
        self.assertEqual(row.status, "unknown")           # 官方结论不变
        self.assertIn("未审核", row.reason)
        self.assertEqual(row.preview["status"], "match")  # 212 < 259
        self.assertEqual(row.preview["observed_value"], "212")
        self.assertEqual(row.preview["unit"], "days")
        self.assertIn("birth[0]", row.preview["patient_record_paths"])
        self.assertIn("不构成判定", row.preview["note"])

    def test_preview_mismatch_and_missing_record(self):
        doc = SourceDocument.from_dict({
            "document_id": "guide-y", "kind": "guideline", "title": "t", "text": "t",
            "metadata": {"applicability_criteria": [
                {"criterion_id": "ga_ge", "field": "ga_days", "operator": "ge", "value": 245,
                 "required": True, "review_status": "draft", "label": "≥35 周",
                 "source": {"locator": "x", "quote": "y"}},
                {"criterion_id": "missing", "field": "fio2", "operator": "exists",
                 "required": False, "review_status": "draft", "label": "FiO2",
                 "source": {"locator": "x", "quote": "y"}},
            ]},
        })
        result = assess_applicability(doc, self._snap(), (), preview=True)
        self.assertEqual(result.criteria[0].preview["status"], "mismatch")
        self.assertEqual(result.criteria[1].preview["status"], "unknown")
        self.assertIsNone(result.criteria[1].preview["observed_value"])

    def test_participating_rows_get_no_preview(self):
        result = assess_applicability(self._doc("synthetic_fixture"), self._snap(), (), preview=True)
        row = result.criteria[0]
        self.assertEqual(row.status, "match")
        self.assertIsNone(row.preview)


if __name__ == "__main__":
    unittest.main()
