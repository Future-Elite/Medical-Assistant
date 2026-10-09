"""Guideline source adapters with explicit metadata/license boundaries.

Only formal guideline publications are registered with ``kind="guideline"``.
WHO technical documents (for example "HEARTS D", which WHO labels a
"Technical document", not a "Guideline") must not be indexed under the
guideline kind; verify the official page label before adding records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .connectors import KnowledgeConnector
from .models import QueryPlan, SearchResult, SourceDocument


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
        warnings = [
            f"来源 {self.source_id} 仅提供官方元数据和链接；未将未确认许可的全文加入检索。"
        ] if selected else []
        return SearchResult(self.source_id, tuple(selected[:limit]), tuple(warnings))


def who_hypertension_metadata() -> SourceDocument:
    return SourceDocument(
        source_id="who-guideline-live", document_id="who:isbn:9789240033986", kind="guideline",
        title="Guideline for the pharmacological treatment of hypertension in adults",
        text="Official guideline metadata only; open the canonical WHO page for the authorized document. Hypertension pharmacological treatment in adults.",
        url="https://www.who.int/publications/i/item/9789240033986",
        published_at="2021-08-24", locator="WHO official publication page", authority="WHO",
        version_id=None, status="unknown", metadata={
            "publisher": "World Health Organization", "stable_id": "ISBN 978-92-4-003398-6",
            "official": True, "license": "CC BY-NC-SA 3.0 IGO (page statement; verify for reuse)",
            "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/igo/",
            "rights_basis": "source_page_requires_verification", "full_text_allowed": False,
            "content_mode": "metadata_only", "live_source": True,
            "mesh_terms": "Hypertension", "keywords": "hypertension pharmacological treatment adults",
        },
    )


def who_diabetes_glucose_metadata() -> SourceDocument:
    """WHO formal guideline (page label: 'Guideline'), 2018.

    Verified 2026-10-07 against the official who.int publication page
    (title/date/ISBN/type label). The 2021 hypertension guideline and this
    record are the two formal WHO guideline metadata records currently
    registered; other diabetes-related WHO items are technical documents or
    pregnancy-specific and are intentionally not registered here.
    """
    return SourceDocument(
        source_id="who-guideline-live", document_id="who:isbn:9789241550284", kind="guideline",
        title="Guidelines on second- and third-line medicines and type of insulin for the control of blood glucose levels in non-pregnant adults with diabetes mellitus",
        text="Official guideline metadata only; open the canonical WHO page for the authorized document. Blood glucose control with second- and third-line medicines and insulin in non-pregnant adults with diabetes mellitus.",
        url="https://www.who.int/publications/i/item/9789241550284",
        published_at="2018-04-21", locator="WHO official publication page", authority="WHO",
        version_id=None, status="unknown", metadata={
            "publisher": "World Health Organization", "stable_id": "ISBN 978-92-4-155028-4",
            "official": True, "license": "CC BY-NC-SA 3.0 IGO (page statement; verify for reuse)",
            "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/igo/",
            "rights_basis": "source_page_requires_verification", "full_text_allowed": False,
            "content_mode": "metadata_only", "live_source": True,
            "mesh_terms": "Diabetes Mellitus, Type 2; Hypoglycemic Agents; Insulin",
            "keywords": "blood glucose control second-line third-line insulin diabetes",
        },
    )


def who_guideline_metadata_registry() -> tuple[SourceDocument, ...]:
    """All registered WHO formal guideline metadata records (metadata_only)."""
    return (who_hypertension_metadata(), who_diabetes_glucose_metadata())
