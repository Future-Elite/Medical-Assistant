"""Guideline lifecycle as a parallel, auditable signal.

This module does not fetch PubMed.  It reads the existing lifecycle cache
(``data/guideline_lifecycle_cache.json``) populated by
``rag.sources.guideline_lifecycle`` and maps guideline documents to their
current/historical status at the snapshot's ``as_of`` date.

The output is deliberately separate from applicability:

- ``current`` does **not** mean the guideline applies to this patient;
- ``retracted``/``superseded`` is a version-governance signal, not a clinical
  judgment;
- a missing PMID/cache record is ``not_checked``, never ``current``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..sources.guideline_lifecycle import GuidelineLifecycleRecord, LifecycleCache
from ..models import SourceDocument

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "guideline_lifecycle_cache.json"


@dataclass
class LifecycleIndex:
    cache_path: Path = DEFAULT_CACHE_PATH
    records: dict[str, GuidelineLifecycleRecord] | None = None

    def __post_init__(self) -> None:
        self.cache_path = Path(self.cache_path)
        if self.records is None:
            self.records = LifecycleCache(self.cache_path, ttl_days=36500).load()

    def statuses(
        self,
        documents: Sequence[SourceDocument],
        *,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        per_guideline: dict[str, Any] = {}
        statuses: list[str] = []
        checked_at: list[str] = []
        for document in documents:
            pmid = str(document.pmid or "").strip()
            if not pmid:
                per_guideline[document.document_id] = {
                    "pmid": None,
                    "status": "not_checked",
                    "reason": "本地指南没有 PMID；未做 PubMed 生命周期核对。",
                }
                statuses.append("not_checked")
                continue
            record = (self.records or {}).get(pmid)
            if record is None:
                per_guideline[document.document_id] = {
                    "pmid": pmid,
                    "status": "not_checked",
                    "reason": "生命周期缓存中没有该 PMID；未联网补查。",
                }
                statuses.append("not_checked")
                continue
            if record.checked_at:
                checked_at.append(record.checked_at)
            if as_of and record.published_at and str(as_of)[:10] < str(record.published_at)[:10]:
                per_guideline[document.document_id] = {
                    "pmid": pmid,
                    "status": "not_yet_published",
                    "published_at": record.published_at,
                    "checked_at": record.checked_at,
                    "source": record.source,
                    "note": "查询截止时间早于该指南发布日期；历史视图不应用。",
                }
                statuses.append("not_yet_published")
                continue
            status = record.status_at(as_of)
            per_guideline[document.document_id] = {
                **status,
                "published_at": record.published_at,
                "checked_at": record.checked_at,
                "source": record.source,
            }
            statuses.append(str(status.get("status") or "not_checked"))
        return {
            "source": "pubmed_lifecycle_cache",
            "as_of": as_of,
            "overall": _overall(statuses),
            "checked_at": max(checked_at) if checked_at else None,
            "guidelines": per_guideline,
        }


def _overall(statuses: Sequence[str]) -> str:
    present = set(statuses)
    for status in ("retracted", "superseded", "corrected", "not_yet_published"):
        if status in present:
            return status
    if "current" in present and present <= {"current", "not_checked"}:
        return "current" if present == {"current"} else "mixed"
    return "not_checked" if present <= {"not_checked"} else "mixed"


def load_lifecycle_index(cache_path: str | Path | None = None) -> LifecycleIndex:
    return LifecycleIndex(cache_path=Path(cache_path) if cache_path else DEFAULT_CACHE_PATH)
