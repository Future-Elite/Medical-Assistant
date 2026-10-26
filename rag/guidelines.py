"""Guideline source adapters with explicit metadata/license boundaries.

Neonatal scope (v6.6.5): the live registry contains **only neonatal guideline
metadata records** verified against their official bibliographic pages.  The
adult WHO hypertension/diabetes records registered in v6.6.3 are deliberately
not carried over: a neonatal assistant must never let an adult guideline enter
a clinical evidence package through a source quota.

Only formal guideline publications are registered with ``kind="guideline"``.
A Chinese interpretation article is registered as a *secondary* record with
``source_role=secondary_interpretation`` and ``official=false``; it must not be
deduplicated against, or substituted for, the original guideline.

Bibliographic identity is pinned by DOI/PMID/PMCID, and the version label
(e.g. "2025 update") is kept separate from the publication date (2026-03-09):
a retrospective ``as_of`` before that date must not treat the update as
available.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .connectors import KnowledgeConnector
from .models import QueryPlan, SearchResult, SourceDocument
from .query import reviewed_topic_terms

NEONATAL_GUIDELINE_SOURCE_ID = "neonatal-guideline-live"

_TOPIC_STOP_TOKENS = frozenset({
    "neonatal", "newborn", "infant", "preterm", "premature",
    "with", "from", "that", "this", "their", "patient", "disease",
    "respiratory", "monitoring", "support", "ventilation",
    "therapy", "treatment", "management", "clinical",
})


def _matches_topic(document: SourceDocument, plan: QueryPlan) -> bool:
    """Keep registry metadata only when it shares a disease token with the query.

    The registry is tiny and deliberately neonatal-only, but a pneumonia record
    matching only the generic word "新生儿" must not top a hypoglycaemia query.
    """
    terms = reviewed_topic_terms(plan)
    if not terms:
        return True
    haystack = " ".join((
        document.title,
        document.text,
        str(document.metadata.get("keywords") or ""),
        str(document.metadata.get("mesh_terms") or ""),
        str(document.population or ""),
    )).lower()
    for term in terms:
        tokens = [
            token for token in re.findall(r"[a-z][a-z0-9-]{4,}", str(term).lower())
            if token not in _TOPIC_STOP_TOKENS
        ]
        if tokens and any(token in haystack for token in tokens):
            return True
    return False


@dataclass
class LicensedGuidelineSnapshotConnector(KnowledgeConnector):
    source_id: str
    documents: tuple[SourceDocument, ...]
    kinds: tuple[str, ...] = ("guideline",)
    full_text_only: bool = True

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and "guideline" not in allowed:
            return SearchResult(self.source_id, ())
        selected: list[SourceDocument] = []
        warnings: list[str] = []
        requested_version = plan.filters.get("guideline_version")
        for document in self.documents:
            if requested_version and document.version_id != str(requested_version):
                continue
            content_mode = str(document.metadata.get("content_mode") or "licensed_full_text")
            full_text_allowed = bool(document.metadata.get("full_text_allowed", True))
            if self.full_text_only and (content_mode != "licensed_full_text" or not full_text_allowed):
                warnings.append(f"指南 {document.document_id} 仅有元数据或许可未确认，未进入全文检索。")
                continue
            selected.append(document)
        return SearchResult(self.source_id, tuple(selected[:limit]), tuple(dict.fromkeys(warnings)))


@dataclass
class GuidelineMetadataConnector(KnowledgeConnector):
    """Official metadata/link-only records; no page scraping or text fabrication."""
    source_id: str
    records: tuple[SourceDocument, ...]
    kinds: tuple[str, ...] = ("guideline",)

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and "guideline" not in allowed:
            return SearchResult(self.source_id, ())
        selected = list(self.records)
        requested_version = plan.filters.get("guideline_version")
        if requested_version:
            selected = [doc for doc in selected if doc.version_id == str(requested_version)]
        selected = [doc for doc in selected if _matches_topic(doc, plan)]
        warnings = [
            f"来源 {self.source_id} 仅提供官方元数据和链接；未将未确认许可的全文加入检索。"
        ] if selected else []
        return SearchResult(self.source_id, tuple(selected[:limit]), tuple(warnings))


def _metadata_record(
    *,
    document_id: str,
    title: str,
    text: str,
    url: str,
    published_at: str,
    locator: str,
    version_id: str | None,
    population: str,
    status: str,
    doi: str | None,
    pmid: str | None,
    metadata: Mapping[str, object],
) -> SourceDocument:
    return SourceDocument(
        source_id=NEONATAL_GUIDELINE_SOURCE_ID, document_id=document_id, kind="guideline",
        title=title, text=text, url=url, published_at=published_at, locator=locator,
        doi=doi, pmid=pmid, version_id=version_id, population=population, status=status,
        metadata=dict(metadata),
    )


def neonatal_pneumonia_metadata() -> SourceDocument:
    """Chinese grassroots guideline on neonatal infectious pneumonia (2025)."""
    return _metadata_record(
        document_id="neo:guide:pneumonia-2025",
        title="基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）",
        text=(
            "Official bibliographic metadata only; the full text is not indexed. "
            "Neonatal infectious pneumonia: diagnosis, imaging, differential diagnosis and referral."
        ),
        url="https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2412164",
        published_at="2025-09-01",
        locator="中国当代儿科杂志 2025;27(9):1027-1037",
        version_id="2025",
        population="新生儿感染性肺炎（基层指南）",
        status="unknown",
        doi="10.7499/j.issn.1008-8830.2412164",
        pmid="40962511",
        metadata={
            "publisher": "中国当代儿科杂志", "stable_id": "PMCID PMC12447934",
            "official": True, "license": "source page statement; verify for reuse",
            "rights_basis": "source_page_requires_verification", "full_text_allowed": False,
            "content_mode": "metadata_only", "live_source": True, "language": "zh",
            "mesh_terms": "Pneumonia; Infant, Newborn",
            "keywords": "neonatal infectious pneumonia diagnosis imaging differential referral",
            "applicability_criteria": [
                {
                    "criterion_id": "neonatal_period", "field": "pna_hours", "operator": "le",
                    "value": 672, "unit": "hours", "required": True, "review_status": "scope_only",
                    "label": "新生儿期（生后 ≤ 28 天）",
                    "source": {"locator": "指南标题/适用人群", "quote": "新生儿感染性肺炎"},
                },
                {
                    "criterion_id": "pneumonia_diagnosis", "field": "diagnosis", "operator": "contains_any",
                    "value": ["肺炎", "pneumonia"], "required": True, "review_status": "scope_only",
                    "label": "诊断包含肺炎",
                    "source": {"locator": "指南标题", "quote": "新生儿感染性肺炎"},
                },
            ],
        },
    )


def neonatal_rds_metadata() -> SourceDocument:
    """European consensus guideline on RDS, 2025 update (published 2026-03-09)."""
    return _metadata_record(
        document_id="neo:guide:rds-2025",
        title="European Consensus Guidelines on the Management of Respiratory Distress Syndrome: 2025",
        text=(
            "Official bibliographic metadata only; the full text is not indexed. "
            "Respiratory distress syndrome in preterm infants: surfactant, respiratory support, "
            "antenatal transfer and antibiotic stewardship."
        ),
        url="https://pubmed.ncbi.nlm.nih.gov/41802129/",
        published_at="2026-03-09",
        locator="Neonatology 2026;123(4):514-539",
        version_id="2025",
        population="早产儿呼吸窘迫综合征",
        status="current",
        doi="10.1159/000551062",
        pmid="41802129",
        metadata={
            "publisher": "Neonatology", "stable_id": "PMCID PMC13038240",
            "official": True, "license": "source page statement; verify for reuse",
            "rights_basis": "source_page_requires_verification", "full_text_allowed": False,
            "content_mode": "metadata_only", "live_source": True, "language": "en",
            "version_label": "2025 update (7th edition)",
            "mesh_terms": "Respiratory Distress Syndrome, Newborn; Pulmonary Surfactants",
            "keywords": "respiratory distress syndrome RDS preterm surfactant CPAP",
            "applicability_criteria": [
                {
                    "criterion_id": "preterm_infant", "field": "ga_days", "operator": "lt",
                    "value": 259, "unit": "days", "required": True, "review_status": "scope_only",
                    "label": "早产儿（出生胎龄 < 37 周）",
                    "source": {"locator": "指南标题/适用人群", "quote": "preterm infants"},
                },
                {
                    "criterion_id": "rds_diagnosis", "field": "diagnosis", "operator": "contains_any",
                    "value": ["呼吸窘迫综合征", "RDS", "respiratory distress syndrome"], "required": True, "review_status": "scope_only",
                    "label": "诊断包含呼吸窘迫综合征",
                    "source": {"locator": "指南标题", "quote": "Respiratory Distress Syndrome"},
                },
            ],
        },
    )


def neonatal_rds_chinese_interpretation_metadata() -> SourceDocument:
    """Chinese interpretation of the RDS 2025 update (secondary source)."""
    return _metadata_record(
        document_id="neo:guide:rds-2025-cn-interpretation",
        title="《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读",
        text=(
            "中文解读文章的官方书目元数据；未登记全文。作为二级解释来源登记，"
            "不得与英文原版指南合并或互相替代。"
        ),
        url="https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2603060",
        published_at="2026-05-01",
        locator="中国当代儿科杂志 2026;28(5):529-535",
        version_id="2025",
        population="早产儿呼吸窘迫综合征（中文解读）",
        status="unknown",
        doi="10.7499/j.issn.1008-8830.2603060",
        pmid="42130350",
        metadata={
            "publisher": "中国当代儿科杂志", "stable_id": "PMCID PMC13173480",
            "official": False, "source_role": "secondary_interpretation",
            "license": "source page statement; verify for reuse",
            "rights_basis": "source_page_requires_verification", "full_text_allowed": False,
            "content_mode": "metadata_only", "live_source": True, "language": "zh",
            "mesh_terms": "Respiratory Distress Syndrome, Newborn",
            "keywords": "RDS 指南解读 新生儿呼吸窘迫综合征",
            "applicability_criteria": [
                {
                    "criterion_id": "preterm_infant", "field": "ga_days", "operator": "lt",
                    "value": 259, "unit": "days", "required": True, "review_status": "scope_only",
                    "label": "早产儿（出生胎龄 < 37 周）",
                    "source": {"locator": "指南标题/适用人群", "quote": "早产儿呼吸窘迫综合征"},
                },
                {
                    "criterion_id": "rds_diagnosis", "field": "diagnosis", "operator": "contains_any",
                    "value": ["呼吸窘迫综合征", "RDS", "respiratory distress syndrome"], "required": True, "review_status": "scope_only",
                    "label": "诊断包含呼吸窘迫综合征",
                    "source": {"locator": "指南标题", "quote": "呼吸窘迫综合征"},
                },
            ],
        },
    )


def neonatal_guideline_metadata_registry() -> tuple[SourceDocument, ...]:
    """All registered neonatal guideline metadata records (metadata_only).

    Deliberately excludes adult guidelines. Record order is the registry's
    declared order, not a clinical ranking.
    """
    return (
        neonatal_pneumonia_metadata(),
        neonatal_rds_metadata(),
        neonatal_rds_chinese_interpretation_metadata(),
    )


def describe_registry(records: Sequence[SourceDocument]) -> list[dict[str, object]]:
    """Audit-friendly summary; used by health output and tests."""
    return [
        {
            "document_id": record.document_id,
            "title": record.title,
            "version_id": record.version_id,
            "published_at": record.published_at,
            "pmid": record.pmid,
            "doi": record.doi,
            "content_mode": record.metadata.get("content_mode"),
            "official": record.metadata.get("official"),
            "population": record.population,
        }
        for record in records
    ]
