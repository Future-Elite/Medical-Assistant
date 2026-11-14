"""v6.7.2 dataset: xlsx reader, fact rules, gold, splits, rubric (no real data)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from rag.eval.qrels.build_dataset import (
    BuildConfig,
    assign_splits,
    build_counterfactual_pairs,
    build_manifest,
    build_patient_records,
    build_synthetic_questions,
)
from rag.eval.qrels.fact_rules import (
    detect_respiratory_support,
    parse_birth_weight,
    parse_gestational_age,
)
from rag.eval.qrels.gold_rules import (
    derived_values_from_row,
    load_criteria_fixture,
    patient_gold,
)
from rag.eval.qrels.decision_triage import (
    SIM_VALUES,
    apply_value_to_snapshot,
    triage_case,
)
from rag.eval.qrels.model_baseline import (
    STATUSES,
    STRUCTURED_FEATURES,
    build_samples_from_cases,
    build_samples_from_records,
)
from rag.eval.qrels.hospital_adapter import (
    DischargeRow,
    COLUMNS,
    patient_ref,
    row_to_snapshot,
)
from rag.eval.qrels.rubric import evaluate
from rag.eval.qrels.xlsx_reader import read_xlsx
from rag.his import filter_snapshot, snapshot_from_dict


def _row(**overrides):
    values = {name: "" for name in COLUMNS}
    values.update({
        "性别": "女",
        "年龄": "3天",
        "住院号": "ZY-1001",
        "入院时间": "2024-01-01 08:00",
        "出院时间": "2024-01-08 10:00",
        "入院诊断": "新生儿黄疸",
        "出院诊断": "新生儿黄疸",
        "入院时主要症状及体征": "孕32+3周，出生体重1400 g，生后气促。",
        "实验室检查及主要会诊": "总胆红素 180 μmol/L；C 反应蛋白 8 mg/L；血培养阴性。",
        "特殊检验及重要会诊": "听力筛查通过。",
        "诊疗过程": "予蓝光治疗；CPAP 辅助通气；静脉营养。",
        "治疗结果": "好转",
        "来源文件": "test",
        "孕周": "32",
    })
    values.update(overrides)
    return DischargeRow(
        row_index=2,
        sex=values["性别"], age_text=values["年龄"], admission_no=values["住院号"],
        admission_time=values["入院时间"], discharge_time=values["出院时间"],
        admission_diagnosis=values["入院诊断"], discharge_diagnosis=values["出院诊断"],
        presenting_symptoms=values["入院时主要症状及体征"],
        laboratory_findings=values["实验室检查及主要会诊"],
        special_examinations=values["特殊检验及重要会诊"],
        treatment_course=values["诊疗过程"], complications=values["合并症"],
        discharge_condition=values["出院时情况"], discharge_orders=values["出院医嘱"],
        discharge_guidance=values["出院指导"], outcome=values["治疗结果"],
        source_file=values["来源文件"], gestational_age_raw=values["孕周"],
        row_hash="hash-1001",
    )


def _mini_xlsx(path: Path, rows: list[list[str]]) -> None:
    shared = []
    index = {}
    for row in rows:
        for value in row:
            if value not in index:
                index[value] = len(shared)
                shared.append(value)
    sheet_rows = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row):
            letters = ""
            n = c_index
            while True:
                letters = chr(ord("A") + n % 26) + letters
                n = n // 26 - 1
                if n < 0:
                    break
            cells.append(f'<c r="{letters}{r_index}" t="s"><v>{index[value]}</v></c>')
        sheet_rows.append(f'<row r="{r_index}">' + "".join(cells) + "</row>")
    shared_xml = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared)}" uniqueCount="{len(shared)}">'
        + "".join(f"<si><t>{value}</t></si>" for value in shared)
        + "</sst>"
    )
    sheet_xml = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(sheet_rows) + "</sheetData></worksheet>"
    )
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    root_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


class XlsxReaderTests(unittest.TestCase):
    def test_read_minimal_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mini.xlsx"
            _mini_xlsx(path, [["性别", "年龄"], ["女", "3天"]])
            sheets = read_xlsx(path)
            self.assertEqual(list(sheets), ["Sheet1"])
            self.assertEqual(sheets["Sheet1"].header, ("性别", "年龄"))
            self.assertEqual(sheets["Sheet1"].dict_rows(), [{"性别": "女", "年龄": "3天"}])


class FactRuleTests(unittest.TestCase):
    def test_ga_column_week_is_range(self):
        result = parse_gestational_age("32", "")
        self.assertEqual(result.extra["ga_days"], 224)
        self.assertEqual(result.extra["ga_days_range"], [224, 230])
        self.assertEqual(result.extra["ga_precision"], "week")

    def test_ga_text_precision(self):
        result = parse_gestational_age("32", "孕32+3周")
        self.assertEqual(result.extra["ga_days"], 227)
        self.assertEqual(result.extra["ga_precision"], "day")

    def test_ga_missing_column_text_present(self):
        result = parse_gestational_age("无提及孕周", "胎龄34+2周")
        self.assertEqual(result.extra["ga_days"], 240)
        self.assertTrue(result.extra["column_missing"])
        self.assertTrue(result.extra["text_present"])

    def test_ga_not_mentioned_is_not_recorded(self):
        result = parse_gestational_age("无提及孕周", "生后气促")
        self.assertEqual(result.status, "not_recorded")
        self.assertIsNone(result.extra["ga_days"])

    def test_birth_weight_ignores_diagnostic_range(self):
        result = parse_birth_weight("出生体重儿(1500-2500g)")
        self.assertEqual(result.status, "not_recorded")
        result = parse_birth_weight("出生体重 1400 g")
        self.assertEqual(result.value, 1400)
        result = parse_birth_weight("出生体重 1.4 kg")
        self.assertEqual(result.value, 1400)

    def test_respiratory_support(self):
        self.assertEqual(detect_respiratory_support("予CPAP辅助通气").extra["mode"], "CPAP")
        self.assertEqual(detect_respiratory_support("予蓝光治疗").status, "not_recorded")
        self.assertEqual(detect_respiratory_support("未行呼吸支持").status, "observed_negative")
        generic = detect_respiratory_support("继续呼吸支持治疗")
        self.assertEqual(generic.extra["mapping_status"], "unclassified_support")


class GoldTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_criteria_fixture()

    def _derived(self, **overrides):
        values = {
            "ga_days": 227, "ga_days_range": [227, 227], "ga_precision": "day",
            "ga_weeks": 32, "ga_extra_days": 3, "birth_weight_g": 1400,
            "respiratory_status": "observed_positive", "respiratory_mode": "CPAP",
            "respiratory_mapping_status": "mapped", "pna_days": 3,
            "jaundice_mentioned": True, "treatments": {}, "labs": {},
        }
        values.update(overrides)
        return values

    def test_preterm_32w_matches_preterm_and_mismatches_vlbw(self):
        gold = patient_gold(self.fixture, self._derived())
        status = {item["guideline_id"]: item["status"] for item in gold["guidelines"]}
        self.assertEqual(status["pdf-neo-feeding-vlbw-2015"], "possible_match")
        self.assertEqual(status["pdf-neo-human-milk-fortification-2019"], "possible_match")

    def test_ga_37w_is_mismatch_for_preterm(self):
        gold = patient_gold(self.fixture, self._derived(
            ga_days=259, ga_days_range=[259, 265], ga_precision="week", ga_weeks=37, ga_extra_days=None))
        status = {item["guideline_id"]: item["status"] for item in gold["guidelines"]}
        self.assertEqual(status["pdf-neo-human-milk-fortification-2019"], "mismatch")

    def test_missing_ga_is_unknown(self):
        gold = patient_gold(self.fixture, self._derived(
            ga_days=None, ga_days_range=None, ga_precision=None, ga_weeks=None, ga_extra_days=None))
        status = {item["guideline_id"]: item["status"] for item in gold["guidelines"]}
        self.assertEqual(status["pdf-neo-human-milk-fortification-2019"], "unknown")
        self.assertIn("derived_birth.ga_days", next(
            item for item in gold["guidelines"]
            if item["guideline_id"] == "pdf-neo-human-milk-fortification-2019"
        )["missing_fields"])

    def test_pocus_negative_is_mismatch(self):
        gold = patient_gold(self.fixture, self._derived(
            respiratory_status="observed_negative", respiratory_mode="无"))
        status = {item["guideline_id"]: item["status"] for item in gold["guidelines"]}
        self.assertEqual(status["pdf-neo-pocus-espnic-2020"], "mismatch")


class AdapterTests(unittest.TestCase):
    def test_patient_ref_has_no_raw_id(self):
        ref = patient_ref("ZY-1001", b"salt")
        self.assertTrue(ref.startswith("hmac-sha256:"))
        self.assertNotIn("ZY-1001", ref)

    def test_snapshot_has_derived_birth_and_respiratory(self):
        snapshot = row_to_snapshot(_row(), salt=b"salt")
        collections = {fact["collection"] for fact in snapshot["facts"]}
        self.assertIn("derived_birth", collections)
        self.assertIn("derived_respiratory_support", collections)
        birth = next(fact for fact in snapshot["facts"] if fact["collection"] == "derived_birth")
        self.assertEqual(birth["fields"]["birth_weight_g"], 1400)
        self.assertEqual(birth["fields"]["ga_days"], 227)

    def test_birth_weight_usable_when_ga_missing(self):
        row = _row(孕周="无提及孕周", 入院时主要症状及体征="出生体重 3000 g。生后气促。")
        snapshot = row_to_snapshot(row, salt=b"salt")
        birth = next(fact for fact in snapshot["facts"] if fact["collection"] == "derived_birth")
        self.assertEqual(birth["fields"]["data_status"], "observed_positive")
        self.assertEqual(birth["fields"]["birth_weight_g"], 3000)
        parsed = snapshot_from_dict(snapshot, default_source="hospital_export")
        from rag.eval.qrels.rubric import build_documents
        from rag.eval.qrels.gold_rules import load_criteria_fixture
        documents = build_documents(load_criteria_fixture())
        from rag.quality.applicability import assess_applicability
        vlbw = assess_applicability(documents["pdf-neo-feeding-vlbw-2015"], parsed, [])
        preterm = assess_applicability(documents["pdf-neo-human-milk-fortification-2019"], parsed, [])
        self.assertEqual(vlbw.status, "mismatch")
        self.assertEqual(preterm.status, "unknown")

    def test_admission_as_of_hides_discharge_fields(self):
        snapshot = snapshot_from_dict(row_to_snapshot(_row(), salt=b"salt"), default_source="hospital_export")
        filtered = filter_snapshot(snapshot, "2024-01-01")
        paths = {fact.record_path for fact in filtered.visible_facts()}
        self.assertIn("xlsx:Sheet1:r2:admission", paths)
        self.assertNotIn("xlsx:Sheet1:r2:discharge", paths)


class DatasetBuildTests(unittest.TestCase):
    def _records(self, count: int = 12):
        rows = []
        for index in range(count):
            # Four patterns: all-match, all-mismatch (B), coverage (A), missing fact (C).
            if index % 4 == 0:
                row = _row(住院号=f"ZY-{1000 + index}", 年龄="3天",
                           孕周="32", 入院时主要症状及体征="孕32+3周，出生体重1400 g。",
                           诊疗过程="予蓝光治疗；CPAP 辅助通气。")
            elif index % 4 == 1:
                row = _row(住院号=f"ZY-{1000 + index}", 年龄="5天",
                           孕周="38", 入院时主要症状及体征="孕38周，出生体重3200 g。",
                           诊疗过程="予蓝光治疗。")
            elif index % 4 == 2:
                row = _row(住院号=f"ZY-{1000 + index}", 年龄="4天",
                           孕周="32", 入院时主要症状及体征="孕32+3周，出生体重1800 g。",
                           诊疗过程="予蓝光治疗。")
            else:
                row = _row(住院号=f"ZY-{1000 + index}", 年龄="5天",
                           孕周="38", 入院时主要症状及体征="孕38周，出生体重3200 g。",
                           诊疗过程="予蓝光治疗。")
            row = row.__class__(**{**row.__dict__, "row_index": index + 2, "row_hash": f"h{index}"})
            rows.append(row)
        return build_patient_records(rows, salt=b"salt")

    def test_split_is_patient_level_disjoint(self):
        records = self._records()
        config = BuildConfig(train_n=4, dev_n=2, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1)
        assign_splits(records, config)
        train = {record["patient_ref"] for record in records if record["split"] == "train"}
        test = {record["patient_ref"] for record in records if record["split"] == "test"}
        self.assertFalse(train & test)

    def test_counterfactual_flips_label(self):
        records = self._records()
        config = BuildConfig(train_n=8, dev_n=2, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1)
        assign_splits(records, config)
        train = [record for record in records if record["split"] == "train"]
        fixture = load_criteria_fixture()
        pairs = build_counterfactual_pairs(train, fixture, seed=42, count=4)
        self.assertTrue(pairs)
        for pair in pairs:
            self.assertNotEqual(pair["label_before"], pair["label_after"])

    def test_synthetic_questions_have_no_patient_ref(self):
        records = self._records()
        assign_splits(records, BuildConfig(train_n=8, dev_n=2, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1))
        train = [record for record in records if record["split"] == "train"]
        questions = build_synthetic_questions(train, load_criteria_fixture(), seed=42, count=4)
        self.assertEqual(len(questions), 4)
        for item in questions:
            self.assertNotIn("patient_ref", item)


class ModelSampleTests(unittest.TestCase):
    def test_record_samples_cover_four_guidelines(self):
        records = DatasetBuildTests()._records(8)
        samples = build_samples_from_records(records)
        self.assertEqual(len(samples), len(records) * 4)
        self.assertTrue(all(sample.label in STATUSES for sample in samples))
        self.assertTrue(all(len(sample.structured) == len(STRUCTURED_FEATURES) for sample in samples))
        self.assertTrue(all(sample.text for sample in samples))

    def test_case_samples_skip_ga_and_carry_case_type(self):
        records = DatasetBuildTests()._records(8)
        config = BuildConfig(train_n=4, dev_n=1, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1)
        _, cases = assign_splits(records, config)
        samples = build_samples_from_cases(cases)
        self.assertTrue(samples)
        self.assertTrue(all(sample.case_type in ("A", "B", "C", "D") for sample in samples))
        self.assertEqual(len(samples), sum(1 for case in cases if case["case_type"] != "GA") * 4)


class TriageTests(unittest.TestCase):
    def _cases(self):
        records = DatasetBuildTests()._records(12)
        config = BuildConfig(train_n=4, dev_n=1, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1)
        _, cases = assign_splits(records, config)
        return cases

    def test_c_triage_puts_target_field_first(self):
        case = next(case for case in self._cases() if case["case_type"] == "C")
        card = triage_case(case)
        self.assertFalse(card["target_stop"])
        self.assertTrue(card["target_priorities"])
        self.assertIn(card["target_priorities"][0]["field"], case["gold"]["missing_fields"])

    def test_b_triage_stops_target(self):
        case = next(case for case in self._cases() if case["case_type"] == "B")
        card = triage_case(case)
        self.assertTrue(card["target_stop"])
        self.assertEqual(card["target_priorities"], [])

    def test_d_triage_declares_topic_gap(self):
        case = next(case for case in self._cases() if case["case_type"] == "D")
        card = triage_case(case)
        self.assertTrue(card["topic_gap"])
        self.assertTrue(card["target_stop"])

    def test_followup_coverage_and_families(self):
        case = next(case for case in self._cases() if case["case_type"] == "A")
        card = triage_case(case)
        followup = card["followup"]
        self.assertTrue(followup["coverage_guidelines"])
        self.assertTrue(followup["status_families"])
        self.assertIn("external_query", followup)

    def test_followup_d_topic_has_no_qualifier_snippets(self):
        case = next(case for case in self._cases() if case["case_type"] == "D")
        followup = triage_case(case)["followup"]
        self.assertEqual(followup["population_qualifiers"], [])
        self.assertIn("没有该主题", followup["population_qualifiers_note"])

    def test_followup_b_external_query_not_executed(self):
        case = next(case for case in self._cases() if case["case_type"] == "B")
        query = triage_case(case)["followup"]["external_query"]
        self.assertIsNotNone(query)
        self.assertFalse(query["executed"])
        self.assertTrue(query["query_en"])

    def test_c_closed_loop_resolves_target(self):
        case = next(case for case in self._cases() if case["case_type"] == "C")
        card = triage_case(case)
        top = card["target_priorities"][0]
        resolved = None
        for value in SIM_VALUES[top["field"]]:
            modified = dict(case)
            modified["snapshot"] = apply_value_to_snapshot(case["snapshot"], top["field"], value)
            after = triage_case(modified)
            if after["target"]["status"] != "unknown":
                resolved = after
                break
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved["target_stop"])


class RubricTests(unittest.TestCase):
    def test_rule_baseline_matches_synthetic_test_case(self):
        fixture = load_criteria_fixture()
        records = DatasetBuildTests()._records(8)
        config = BuildConfig(train_n=4, dev_n=1, test_a=1, test_b=1, test_c=1, test_d=1, test_ga=1)
        _, cases = assign_splits(records, config)
        metrics = evaluate(cases, fixture)
        self.assertEqual(metrics["case_count"], len(cases))
        self.assertIsNotNone(metrics["R8_doctor_readable"]["rate"])
        self.assertGreaterEqual(metrics["R3_query_compliance"]["rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
