"""Offline synthetic sources for RAG tests and demonstrations only.

These records are not clinical evidence and are not a replacement for an
authorized guideline corpus or a frozen PubMed snapshot.
"""
from .models import SourceDocument
from .his import PatientSnapshot, PatientFact


SYNTHETIC_HIS_SNAPSHOTS = {
    "syn-patient-001": PatientSnapshot(
        patient_ref="syn-patient-001", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("hypertension",),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2024-02-01", fields={"name": "高血压"}),
            PatientFact(collection="medications", event_type="medication", record_path="medications[0]", field_path="medications[0].name", start="2024-02-02", end=None, fields={"name": "amlodipine", "reason_if_any": "blood pressure"}),
            PatientFact(collection="labs", event_type="lab", record_path="labs[0]", field_path="labs[0].name", time="2025-08-10", fields={"name": "blood pressure", "value": "145/92", "flag": "high"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-01-05", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-002": PatientSnapshot(
        patient_ref="syn-patient-002", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("type 2 diabetes", "metformin"),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2023-05-10", fields={"name": "2型糖尿病"}),
            PatientFact(collection="medications", event_type="medication", record_path="medications[0]", field_path="medications[0].name", start="2023-05-11", end=None, fields={"name": "metformin", "reason_if_any": "blood glucose"}),
            PatientFact(collection="labs", event_type="lab", record_path="labs[0]", field_path="labs[0].name", time="2025-09-02", fields={"name": "HbA1c", "value": "7.8%", "flag": "high"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-02-01", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-003": PatientSnapshot(
        patient_ref="syn-patient-003", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("neonatal jaundice", "hyperbilirubinemia"),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2025-12-22", fields={"name": "新生儿黄疸"}),
            PatientFact(collection="labs", event_type="lab", record_path="labs[0]", field_path="labs[0].name", time="2025-12-22", fields={"name": "总胆红素", "value": "285 μmol/L", "flag": "high"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-01-15", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-004": PatientSnapshot(
        patient_ref="syn-patient-004", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("fever",),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2025-12-28", fields={"name": "发热"}),
            PatientFact(collection="labs", event_type="lab", record_path="labs[0]", field_path="labs[0].name", time="2025-12-28", fields={"name": "体温", "value": "38.9 °C", "flag": "high"}),
            PatientFact(collection="labs", event_type="lab", record_path="labs[1]", field_path="labs[1].name", time="2025-12-28", fields={"name": "CRP", "value": "48 mg/L", "flag": "high"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-02-05", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-005": PatientSnapshot(
        patient_ref="syn-patient-005", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("vaccination",),
        facts=(
            PatientFact(collection="immunizations", event_type="vaccination", record_path="immunizations[0]", field_path="immunizations[0].name", time="2025-12-15", fields={"name": "hepatitis B vaccine", "dose": "3"}),
            PatientFact(collection="growth", event_type="measurement", record_path="growth[0]", field_path="growth[0].weight", time="2025-12-15", fields={"weight": "7.2 kg"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-03-01", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-006": PatientSnapshot(
        patient_ref="syn-patient-006", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("amlodipine",),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2022-03-15", fields={"name": "高血压"}),
            PatientFact(collection="medications", event_type="medication", record_path="medications[0]", field_path="medications[0].name", start="2022-03-20", end=None, fields={"name": "amlodipine", "reason_if_any": "blood pressure"}),
            PatientFact(collection="symptoms", event_type="symptom", record_path="symptoms[0]", field_path="symptoms[0].name", time="2025-11-02", fields={"name": "下肢水肿"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-02-10", fields={"impression": "future record"}),
        ),
    ),
    "syn-patient-007": PatientSnapshot(
        patient_ref="syn-patient-007", as_of="2025-12-31", source="synthetic_chart",
        reviewed_external_terms=("asthma",),
        facts=(
            PatientFact(collection="diagnoses", event_type="diagnosis", record_path="diagnoses[0]", field_path="diagnoses[0].name", time="2024-09-12", fields={"name": "支气管哮喘"}),
            PatientFact(collection="medications", event_type="medication", record_path="medications[0]", field_path="medications[0].name", start="2024-09-15", end=None, fields={"name": "budesonide", "reason_if_any": "asthma control"}),
            PatientFact(collection="reports", event_type="report", record_path="reports[0]", field_path="reports[0].impression", time="2026-03-20", fields={"impression": "future record"}),
        ),
    ),
}
SYNTHETIC_PUBMED = (
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1001", kind="pubmed",
        title="Hypertension treatment outcomes in adults",
        text="A randomized study evaluated hypertension treatment outcomes in adults. The result was reported for the study population.",
        pmid="1001", published_at="2024-01-10", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Hypertension; Treatment Outcome", "keywords": "blood pressure adult", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1002", kind="pubmed",
        title="Hypertension guideline update evidence",
        text="A cohort study examined new evidence after a prior guideline. The findings require comparison with the current recommendation.",
        pmid="1002", published_at="2025-02-03", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Hypertension; Practice Guideline", "keywords": "guideline update", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1003", kind="pubmed",
        title="Metformin as first-line therapy for type 2 diabetes: a randomized trial",
        text="A randomized trial compared metformin with alternative first-line therapy for type 2 diabetes in adults; glycemic outcomes are reported without clinical recommendation.",
        pmid="1003", published_at="2025-06-01", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Diabetes Mellitus, Type 2; Metformin", "keywords": "first-line therapy glycemic control", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1004", kind="pubmed",
        title="Phototherapy for neonatal jaundice: a randomized trial",
        text="A randomized trial compared phototherapy strategies for neonatal jaundice; bilirubin outcomes are reported for the study population without clinical recommendation.",
        pmid="1004", published_at="2025-05-12", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Jaundice, Neonatal; Phototherapy", "keywords": "bilirubin neonate phototherapy", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1005", kind="pubmed",
        title="Evaluation of fever in infants younger than 3 months",
        text="A cohort study examined structured evaluation of fever in infants younger than 3 months, including laboratory criteria.",
        pmid="1005", published_at="2025-07-08", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Fever; Infant", "keywords": "young infant fever evaluation", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1006", kind="pubmed",
        title="Adverse events following routine infant vaccination: a surveillance study",
        text="A surveillance study describes reported adverse events following routine infant vaccination in the study population.",
        pmid="1006", published_at="2025-09-18", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Vaccination; Infant", "keywords": "adverse events immunization surveillance", "official": False},
    ),
    SourceDocument(
        source_id="pubmed-fixture", document_id="pmid-1007", kind="pubmed",
        title="Amlodipine-associated peripheral edema: a cohort study",
        text="A cohort study examined peripheral edema among adults receiving amlodipine for blood pressure control; findings describe observed associations only.",
        pmid="1007", published_at="2025-04-02", index_snapshot="synthetic-2026-10-03",
        metadata={"mesh_terms": "Amlodipine; Edema", "keywords": "peripheral edema calcium channel blocker", "official": False},
    ),
)

SYNTHETIC_GUIDELINES = (
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-2024-a", kind="guideline",
        title="Synthetic hypertension practice guideline 2024",
        text="For adults with hypertension, the recommendation is conditional and depends on patient context. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/2024", published_at="2024-03-01", updated_at="2024-03-01",
        locator="Section 2, Recommendation 2.1", version_id="2024", recommendation_id="2.1",
        population="adults with hypertension", status="current", index_snapshot="synthetic-2026-10-03",
        metadata={"official": True, "license": "synthetic-fixture"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-2023-old", kind="guideline",
        title="Synthetic hypertension practice guideline 2023",
        text="The prior recommendation was superseded by a later version and should not be treated as the current recommendation.",
        url="https://example.invalid/guideline/2023", published_at="2023-01-01", updated_at="2023-01-01",
        locator="Section 2, Recommendation 2.1", version_id="2023", recommendation_id="2.1",
        population="adults with hypertension", status="superseded", superseded_by="guide-2024-a",
        index_snapshot="synthetic-2026-10-03", metadata={"official": True, "license": "synthetic-fixture"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-dm-2025", kind="guideline",
        title="Synthetic type 2 diabetes practice guideline 2025",
        text="For adults with type 2 diabetes, the recommendation supports metformin as initial pharmacotherapy in the absence of contraindications. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/dm-2025", published_at="2025-04-15", updated_at="2025-04-15",
        locator="Section 3, Recommendation 3.2", version_id="2025", recommendation_id="3.2",
        population="adults with type 2 diabetes", status="current", index_snapshot="synthetic-2026-10-03",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Diabetes Mellitus, Type 2; Metformin",
                  "keywords": "initial pharmacotherapy metformin"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-nnj-2025", kind="guideline",
        title="Synthetic neonatal jaundice management guideline 2025",
        text="For neonates with jaundice, the recommendation defines phototherapy thresholds by bilirubin level. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/nnj-2025", published_at="2025-03-10", updated_at="2025-03-10",
        locator="Section 4, Recommendation 4.1", version_id="2025", recommendation_id="4.1",
        population="neonates with jaundice", status="current", index_snapshot="synthetic-2026-10-03",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Jaundice, Neonatal; Hyperbilirubinemia",
                  "keywords": "phototherapy bilirubin threshold"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-fever-2024", kind="guideline",
        title="Synthetic infant fever evaluation guideline 2024",
        text="For infants with fever, the recommendation describes structured evaluation steps and observation criteria. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/fever-2024", published_at="2024-05-20", updated_at="2024-05-20",
        locator="Section 2, Recommendation 2.3", version_id="2024", recommendation_id="2.3",
        population="infants with fever", status="current", index_snapshot="synthetic-2026-10-03",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Fever; Infant",
                  "keywords": "fever evaluation structured observation"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-vax-2025", kind="guideline",
        title="Synthetic infant immunization schedule guideline 2025",
        text="For infants, the schedule recommendation lists routine vaccination timing and catch-up doses. See the recommendation and exceptions.",
        url="https://example.invalid/guideline/vaccine-2025", published_at="2025-01-15", updated_at="2025-01-15",
        locator="Section 1, Recommendation 1.2", version_id="2025", recommendation_id="1.2",
        population="infants", status="current", index_snapshot="synthetic-2026-10-03",
        metadata={"official": True, "license": "synthetic-fixture", "mesh_terms": "Immunization Schedule; Vaccines",
                  "keywords": "vaccination schedule catch-up"},
    ),
    SourceDocument(
        source_id="guideline-fixture", document_id="guide-cn-asthma-2025", kind="guideline",
        title="合成样例：儿童哮喘长期管理规范",
        text="本合成样例说明儿童哮喘长期管理的主要建议，仅用于流程测试。",
        url="https://example.invalid/guideline/cn-asthma-2025", published_at="2025-06-01", updated_at="2025-06-01",
        locator="第一章，主要建议", version_id="2025", recommendation_id="1.1",
        population="儿童哮喘", status="current", index_snapshot="synthetic-2026-10-08",
        metadata={"official": True, "license": "synthetic-fixture", "language": "zh",
                  "keywords": "儿童哮喘 长期管理"},
    ),
)
