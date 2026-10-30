"""Offline synthetic sources for this package's tests only.

These records are produced inside the test package and are never registered by
the server or any product code path; they are not clinical evidence.

Scope (v6.6.5): every fixture is neonatal. Adult scenarios from v6.6.3
(hypertension / type 2 diabetes / amlodipine) are deliberately removed so that
no test can accidentally re-introduce an adult evidence path into the demo.

Identity conventions exercised by the fixtures:

- guideline version (``version_id``) is separate from publication date;
- one superseded RDS record and one future-dated BPD record exercise the
  pre-ranking status filter;
- the Chinese jaundice record is the only CJK-only evidence source used for
  CJK bigram matching tests; its title deliberately avoids the shared
  ``新生儿`` prefix so cross-disease isolation assertions stay meaningful.
"""
from rag.his import PatientFact, PatientSnapshot
from rag.models import SourceDocument

SYNTHETIC_HIS_SNAPSHOTS = {
    "syn-neo-001": PatientSnapshot(
        patient_ref="syn-neo-001", as_of="2026-04-15", source="synthetic_neonatal_chart",
        reviewed_external_terms=("respiratory distress syndrome", "pulmonary surfactant", "CPAP"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-04-10", fields={"subject_role": "patient", "ga_days": 212, "ga_text": "30周+2天", "birth_weight_g": 1450, "data_status": "observed_positive"}),
            PatientFact(collection="maternal", event_type="pregnancy", record_path="maternal[0]", field_path="maternal[0].gbs_status", time="2026-04-10", fields={"subject_role": "mother", "gbs_status": "not_recorded", "prom_hours": 18, "data_status": "not_recorded"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-04-10", fields={"subject_role": "patient", "name": "新生儿呼吸窘迫综合征", "data_status": "observed_positive"}),
            PatientFact(collection="respiratory_support", event_type="support", record_path="respiratory_support[0]", field_path="respiratory_support[0].mode", start="2026-04-10", end=None, fields={"subject_role": "patient", "mode": "CPAP", "fio2": "0.30", "fio2_unit": "fraction", "data_status": "observed_positive"}),
            PatientFact(collection="labs", event_type="culture", record_path="labs[0]", field_path="labs[0].culture_status", time="2026-04-10", fields={"subject_role": "patient", "specimen": "blood", "culture_status": "pending", "data_status": "pending"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-05-05", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
    "syn-neo-002": PatientSnapshot(
        patient_ref="syn-neo-002", as_of="2026-09-12", source="synthetic_neonatal_chart",
        reviewed_external_terms=("neonatal pneumonia", "chest radiograph", "blood culture"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-09-10", fields={"subject_role": "patient", "ga_days": 274, "ga_text": "39周+1天", "birth_weight_g": 3320, "data_status": "observed_positive"}),
            PatientFact(collection="maternal", event_type="pregnancy", record_path="maternal[0]", field_path="maternal[0].gbs_status", time="2026-09-10", fields={"subject_role": "mother", "gbs_status": "positive", "prom_hours": 26, "maternal_fever_c": 38.2, "data_status": "observed_positive"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-09-11", fields={"subject_role": "patient", "name": "新生儿感染性肺炎", "data_status": "observed_positive"}),
            PatientFact(collection="vitals", event_type="measurement", record_path="vitals[0]", field_path="vitals[0].temp_c", time="2026-09-11", fields={"subject_role": "patient", "temp_c": 38.9, "rr_per_min": 72, "data_status": "observed_positive"}),
            PatientFact(collection="labs", event_type="measurement", record_path="labs[0]", field_path="labs[0].crp_mg_l", time="2026-09-11", fields={"subject_role": "patient", "crp_mg_l": 48, "wbc_10e9_l": 18.2, "data_status": "observed_positive"}),
            PatientFact(collection="labs", event_type="culture", record_path="labs[1]", field_path="labs[1].culture_status", time="2026-09-11", fields={"subject_role": "patient", "specimen": "blood", "culture_status": "pending", "data_status": "pending"}),
            PatientFact(collection="imaging", event_type="chest_xray", record_path="imaging[0]", field_path="imaging[0].impression", time="2026-09-11", fields={"subject_role": "patient", "modality": "chest_xray", "report_status": "signed", "impression": "双肺纹理增粗，右下肺斑片影", "data_status": "observed_positive"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-09-15", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
    "syn-neo-003": PatientSnapshot(
        patient_ref="syn-neo-003", as_of="2026-07-18", source="synthetic_neonatal_chart",
        reviewed_external_terms=("neonatal jaundice", "hyperbilirubinemia", "phototherapy"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-07-15", fields={"subject_role": "patient", "ga_days": 248, "ga_text": "35周+3天", "birth_weight_g": 2400, "data_status": "observed_positive"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-07-18", fields={"subject_role": "patient", "name": "高胆红素血症", "data_status": "observed_positive"}),
            PatientFact(collection="labs", event_type="measurement", record_path="labs[0]", field_path="labs[0].total_bilirubin_umol_l", time="2026-07-18", fields={"subject_role": "patient", "total_bilirubin_umol_l": 285, "direct_bilirubin_umol_l": 12, "data_status": "observed_positive"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-07-20", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
    "syn-neo-004": PatientSnapshot(
        patient_ref="syn-neo-004", as_of="2026-08-20", source="synthetic_neonatal_chart",
        reviewed_external_terms=("transient tachypnea of the newborn", "TTN"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-08-20", fields={"subject_role": "patient", "ga_days": 270, "ga_text": "38周+4天", "birth_weight_g": 3100, "data_status": "observed_positive"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-08-20", fields={"subject_role": "patient", "name": "暂时性呼吸增快", "data_status": "observed_positive"}),
            PatientFact(collection="vitals", event_type="measurement", record_path="vitals[0]", field_path="vitals[0].rr_per_min", time="2026-08-20", fields={"subject_role": "patient", "rr_per_min": 78, "spo2_percent": 94, "respiratory_support": "room air", "data_status": "observed_positive"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-08-22", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
    "syn-neo-005": PatientSnapshot(
        patient_ref="syn-neo-005", as_of="2026-06-12", source="synthetic_neonatal_chart",
        reviewed_external_terms=("persistent pulmonary hypertension of the newborn", "inhaled nitric oxide"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-06-12", fields={"subject_role": "patient", "ga_days": 273, "ga_text": "39周+0天", "birth_weight_g": 3500, "meconium_stained_fluid": True, "data_status": "observed_positive"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-06-12", fields={"subject_role": "patient", "name": "持续性肺动脉高压", "data_status": "observed_positive"}),
            PatientFact(collection="vitals", event_type="measurement", record_path="vitals[0]", field_path="vitals[0].preductal_spo2_percent", time="2026-06-12", fields={"subject_role": "patient", "preductal_spo2_percent": 88, "postductal_spo2_percent": 82, "data_status": "observed_positive"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-06-14", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
    "syn-neo-006": PatientSnapshot(
        patient_ref="syn-neo-006", as_of="2026-05-20", source="synthetic_neonatal_chart",
        reviewed_external_terms=("bronchopulmonary dysplasia", "postmenstrual age"),
        facts=(
            PatientFact(collection="birth", event_type="birth", record_path="birth[0]", field_path="birth[0].ga_days", time="2026-03-25", fields={"subject_role": "patient", "ga_days": 196, "ga_text": "28周+0天", "birth_weight_g": 980, "data_status": "observed_positive"}),
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2026-05-19", fields={"subject_role": "patient", "name": "支气管肺发育不良", "data_status": "observed_positive"}),
            PatientFact(collection="respiratory_support", event_type="support", record_path="respiratory_support[0]", field_path="respiratory_support[0].mode", start="2026-03-25", end=None, fields={"subject_role": "patient", "mode": "CPAP", "fio2": "0.25", "data_status": "observed_positive"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-05-25", fields={"subject_role": "patient", "impression": "future record", "data_status": "observed_positive"}),
        ),
    ),
}

SYNTHETIC_PUBMED = (
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2001", kind="pubmed",
        title="Pulmonary surfactant for respiratory distress syndrome in preterm babies: a randomized trial",
        text="A randomized trial compared pulmonary surfactant strategies for respiratory distress syndrome in preterm babies; respiratory outcomes are reported without clinical recommendation.",
        pmid="2001", published_at="2025-06-01", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Respiratory Distress Syndrome; Pulmonary Surfactants", "keywords": "preterm surfactant CPAP", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2002", kind="pubmed",
        title="Respiratory distress syndrome guideline update: new evidence",
        text="A cohort study examined new evidence after the prior RDS guideline. The findings require comparison with the current recommendation.",
        pmid="2002", published_at="2026-02-03", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Respiratory Distress Syndrome; Practice Guideline", "keywords": "guideline update RDS", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2003", kind="pubmed",
        title="Chest radiograph and lung ultrasound in infectious pneumonia: a cohort study",
        text="A cohort study examined chest radiograph and lung ultrasound findings in infectious pneumonia; diagnostic performance is reported for the study population.",
        pmid="2003", published_at="2025-08-10", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Pneumonia; Ultrasonography", "keywords": "pneumonia chest radiograph lung ultrasound", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2004", kind="pubmed",
        title="Phototherapy thresholds for jaundice: a randomized trial",
        text="A randomized trial compared phototherapy strategies for jaundice; bilirubin outcomes are reported for the study population without clinical recommendation.",
        pmid="2004", published_at="2025-05-12", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Jaundice; Phototherapy", "keywords": "bilirubin phototherapy jaundice", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2005", kind="pubmed",
        title="Transient tachypnea in near-term babies: a prospective cohort",
        text="A prospective cohort described the clinical course of transient tachypnea in near-term babies.",
        pmid="2005", published_at="2025-07-08", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Transient Tachypnea; Infant", "keywords": "TTN tachypnea near-term", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2006", kind="pubmed",
        title="Blood glucose screening for hypoglycemia in low birth weight newborns",
        text="A cohort study evaluated blood glucose screening and feeding support for hypoglycemia in low birth weight newborns.",
        pmid="2006", published_at="2025-09-18", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Hypoglycemia; Blood Glucose", "keywords": "blood glucose screening low birth weight", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-2007", kind="pubmed",
        title="Inhaled nitric oxide for persistent pulmonary hypertension of the newborn",
        text="A trial examined inhaled nitric oxide and oxygenation targets in persistent pulmonary hypertension of the newborn; findings describe the study population only.",
        pmid="2007", published_at="2025-04-02", index_snapshot="synthetic-2026-10-17",
        metadata={"mesh_terms": "Persistent Fetal Circulation Syndrome; Nitric Oxide", "keywords": "PPHN inhaled nitric oxide oxygenation", "official": False},
    ),
)

SYNTHETIC_GUIDELINES = (
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-rds-2025", kind="guideline",
        title="Synthetic respiratory distress syndrome guideline 2025",
        text="For preterm infants with respiratory distress syndrome, the recommendation describes pulmonary surfactant and CPAP conditions, including FiO2 thresholds. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/rds-2025", published_at="2025-04-01", updated_at="2025-04-01",
        locator="Section 3, Recommendation 3.1", version_id="2025", recommendation_id="3.1",
        population="preterm infants with respiratory distress syndrome", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Respiratory Distress Syndrome; Pulmonary Surfactants", "keywords": "surfactant CPAP preterm RDS", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-rds-2023-old", kind="guideline",
        title="Synthetic respiratory distress syndrome guideline 2023",
        text="The prior recommendation was superseded by a later version and should not be treated as the current recommendation.",
        url="https://example.invalid/guideline/rds-2023", published_at="2023-01-01", updated_at="2023-01-01",
        locator="Section 3, Recommendation 3.1", version_id="2023", recommendation_id="3.1",
        population="preterm infants with respiratory distress syndrome", status="superseded", superseded_by="guide-rds-2025",
        index_snapshot="synthetic-2026-10-17", metadata={"official": True, "license": "synthetic-fixture", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-pna-2025", kind="guideline",
        title="Synthetic infectious pneumonia guideline 2025",
        text="For newborns with suspected infectious pneumonia, the recommendation covers chest radiograph, lung ultrasound and blood culture interpretation. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/pna-2025", published_at="2025-09-01", updated_at="2025-09-01",
        locator="Section 2, Recommendation 10", version_id="2025", recommendation_id="10",
        population="suspected infectious pneumonia", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Pneumonia", "keywords": "pneumonia chest radiograph lung ultrasound blood culture", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-jnd-2025", kind="guideline",
        title="合成样例：高胆红素血症光疗规范",
        text="本合成样例说明黄疸的光疗阈值与胆红素监测，仅用于流程测试。",
        url="https://example.invalid/guideline/jnd-2025", published_at="2025-03-10", updated_at="2025-03-10",
        locator="第一章，推荐 1.1", version_id="2025", recommendation_id="1.1",
        population="高胆红素血症", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "language": "zh", "keywords": "黄疸 光疗 胆红素", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-ttn-2025", kind="guideline",
        title="Synthetic transient tachypnea guideline 2025",
        text="For near-term babies with transient tachypnea, the recommendation describes observation and differential diagnosis. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/ttn-2025", published_at="2025-02-01", updated_at="2025-02-01",
        locator="Section 1, Recommendation 1.1", version_id="2025", recommendation_id="1.1",
        population="transient tachypnea", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Transient Tachypnea", "keywords": "TTN tachypnea observation", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-hypo-2025", kind="guideline",
        title="Synthetic hypoglycemia screening guideline 2025",
        text="For low birth weight, the recommendation describes blood glucose screening and feeding support. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/hypo-2025", published_at="2025-05-01", updated_at="2025-05-01",
        locator="Section 2, Recommendation 2.1", version_id="2025", recommendation_id="2.1",
        population="low birth weight", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Hypoglycemia", "keywords": "blood glucose screening feeding", "synthetic": True},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-bpd-2027", kind="guideline",
        title="Synthetic bronchopulmonary dysplasia guideline 2027",
        text="For preterm survivors, the recommendation describes bronchopulmonary dysplasia definition and assessment timing. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/bpd-2027", published_at="2027-01-01", updated_at="2027-01-01",
        locator="Section 1, Recommendation 1.1", version_id="2027", recommendation_id="1.1",
        population="preterm survivors", status="current", index_snapshot="synthetic-2026-10-17",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Bronchopulmonary Dysplasia", "keywords": "BPD definition postmenstrual age", "synthetic": True},
    ),
)
