"""Guideline lifecycle governance from real PubMed records.

A guideline can be retracted, updated, corrected, or marked with an expression
of concern long after it was published.  Those facts live in PubMed's
``CommentsCorrectionsList`` and ``PublicationTypeList``, not in a guideline's
title or version label.  This module reads that real metadata and applies it to
retrieval with an explicit ``as_of`` boundary:

* an event dated after ``as_of`` is recorded but does **not** change the
  historical view;
* a retraction/update with an unknown date is reported and **not** used to
  filter evidence (no guessing);
* ``shadow`` records lifecycle facts only; ``active`` maps a retracted
  guideline to ``withdrawn`` and an updated guideline to ``superseded`` so the
  existing pre-ranking status filter can remove it.

The network client is injectable; tests replay real PubMed XML captured from
E-utilities.  No lifecycle fact is inferred from titles or publication years.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..quality.metrics import METRICS

GUIDELINE_LIFECYCLE_MODES = ("off", "shadow", "active")
_CACHE_SCHEMA_VERSION = "3"

_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# RefType values that describe the *current* article's lifecycle.
_RETRACTION_TYPES = {"RetractionIn"}
_UPDATE_TYPES = {"UpdateIn"}
_CORRECTION_TYPES = {"ErratumIn", "CorrectedandRepublishedIn"}
_CONCERN_TYPES = {"ExpressionOfConcernIn"}
_RETRACTION_NOTICE_TYPES = {"RetractionOf"}
_UPDATE_NOTICE_TYPES = {"UpdateOf"}


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str
    ref_type: str
    source: str
    pmid: str | None = None
    date: str | None = None
    date_precision: str = "unknown"
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref_type": self.ref_type,
            "source": self.source,
            "pmid": self.pmid,
            "date": self.date,
            "date_precision": self.date_precision,
            "title": self.title,
        }


@dataclass(frozen=True)
class GuidelineLifecycleRecord:
    pmid: str
    published_at: str | None
    title: str | None
    publication_types: tuple[str, ...]
    events: tuple[LifecycleEvent, ...]
    checked_at: str
    source: str = "pubmed_efetch"

    @property
    def retraction_events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(event for event in self.events if event.kind == "retraction")

    @property
    def update_events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(event for event in self.events if event.kind == "update")

    @property
    def correction_events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(event for event in self.events if event.kind in {"correction", "concern"})

    def status_at(self, as_of: str | date | None) -> dict[str, Any]:
        cutoff = _parse_date(as_of) or date.today()
        applied: list[LifecycleEvent] = []
        future: list[LifecycleEvent] = []
        unknown: list[LifecycleEvent] = []
        for event in self.events:
            event_date = _parse_date(event.date)
            if event_date is None:
                unknown.append(event)
            elif event_date <= cutoff:
                applied.append(event)
            else:
                future.append(event)

        def latest(events: Iterable[LifecycleEvent]) -> LifecycleEvent | None:
            dated = [(event, _parse_date(event.date)) for event in events]
            dated = [(event, value) for event, value in dated if value is not None]
            return max(dated, key=lambda item: item[1])[0] if dated else None

        retractions = [event for event in applied if event.kind == "retraction"]
        updates = [event for event in applied if event.kind == "update"]
        corrections = [event for event in applied if event.kind in {"correction", "concern"}]
        unknown_retractions = [event for event in unknown if event.kind == "retraction"]
        current_state_known = as_of is None or cutoff >= date.today()
        if retractions:
            status = "retracted"
        elif unknown_retractions and current_state_known:
            # PubMed can carry PublicationType="Retracted Publication" without a
            # dated RetractionIn notice.  The current record is known to be
            # retracted; only an explicitly historical as_of must not apply it.
            status = "retracted"
        elif updates:
            status = "superseded"
        elif corrections:
            status = "corrected"
        else:
            status = "current"
        return {
            "pmid": self.pmid,
            "as_of": cutoff.isoformat(),
            "status": status,
            "applied_events": [event.to_dict() for event in applied],
            "future_events": [event.to_dict() for event in future],
            "unknown_date_events": [event.to_dict() for event in unknown],
            "latest_retraction": latest(retractions).to_dict() if retractions else None,
            "latest_update": latest(updates).to_dict() if updates else None,
            "latest_correction": latest(corrections).to_dict() if corrections else None,
            "notes": _status_notes(status, applied, future, unknown),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "pmid": self.pmid,
            "published_at": self.published_at,
            "title": self.title,
            "publication_types": list(self.publication_types),
            "events": [event.to_dict() for event in self.events],
            "checked_at": self.checked_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GuidelineLifecycleRecord":
        return cls(
            pmid=str(payload.get("pmid") or ""),
            published_at=payload.get("published_at"),
            title=payload.get("title"),
            publication_types=tuple(str(item) for item in payload.get("publication_types") or ()),
            events=tuple(
                LifecycleEvent(
                    kind=str(row.get("kind") or "unknown"),
                    ref_type=str(row.get("ref_type") or ""),
                    source=str(row.get("source") or ""),
                    pmid=row.get("pmid"),
                    date=row.get("date"),
                    date_precision=str(row.get("date_precision") or "unknown"),
                    title=row.get("title"),
                )
                for row in payload.get("events") or ()
                if isinstance(row, Mapping)
            ),
            checked_at=str(payload.get("checked_at") or ""),
            source=str(payload.get("source") or "pubmed_efetch"),
        )


def _status_notes(status: str, applied: Sequence[LifecycleEvent], future: Sequence[LifecycleEvent], unknown: Sequence[LifecycleEvent]) -> list[str]:
    notes: list[str] = []
    if status == "retracted":
        if any(event.date is None for event in applied) or not applied:
            notes.append("PubMed 记录当前标记为 Retracted Publication；撤回通知日期未知，当前查询按已撤回处理，历史 as_of 不应用。")
        else:
            notes.append("该指南在查询截止时间前已被撤回；active 模式会将其标记为 withdrawn。")
    elif status == "superseded":
        notes.append("该指南在查询截止时间前已有更新版本记录；active 模式会将其标记为 superseded。")
    elif status == "corrected":
        notes.append("该指南有勘误或关注声明；未撤回，但阅读时需核对更正内容。")
    if future:
        notes.append("存在晚于查询截止时间的事件；历史视图不应用这些事件。")
    if unknown:
        notes.append("存在日期未知的事件；不据此过滤证据。")
    return notes


def _parse_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_pub_date(element: ET.Element | None) -> tuple[str | None, str]:
    if element is None:
        return None, "unknown"
    year_text = _text(element.find("Year"))
    month_text = _text(element.find("Month"))
    day_text = _text(element.find("Day"))
    medline = _text(element.find("MedlineDate"))
    if not year_text and medline:
        match = re.search(r"(19|20)\d{2}", medline)
        year_text = match.group(0) if match else ""
        month_match = re.search(r"([A-Za-z]{3})", medline)
        month_text = month_text or (month_match.group(1) if month_match else "")
    if not year_text:
        return None, "unknown"
    try:
        year = int(year_text)
    except ValueError:
        return None, "unknown"
    month = 1
    precision = "year"
    if month_text:
        lowered = month_text.strip().lower()[:3]
        parsed_month = _MONTHS.get(lowered)
        if parsed_month is None:
            try:
                parsed_month = int(month_text)
            except ValueError:
                parsed_month = None
        if parsed_month:
            month = parsed_month
            precision = "month"
    day = 1
    if day_text:
        try:
            day = int(day_text)
            precision = "day"
        except ValueError:
            day = 1
    try:
        return date(year, month, day).isoformat(), precision
    except ValueError:
        return date(year, 1, 1).isoformat(), "year"


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def parse_pubmed_xml(xml_text: str | bytes) -> dict[str, dict[str, Any]]:
    """Parse ``efetch`` XML into per-PMID metadata (no interpretation)."""
    root = ET.fromstring(xml_text)
    parsed: dict[str, dict[str, Any]] = {}
    for article in root.findall(".//PubmedArticle"):
        citation = article.find("MedlineCitation")
        if citation is None:
            continue
        pmid = _text(citation.find("PMID"))
        if not pmid:
            continue
        article_node = citation.find("Article")
        pub_date, precision = _parse_pub_date(article_node.find("Journal/JournalIssue/PubDate") if article_node is not None else None)
        if pub_date is None and article_node is not None:
            article_date = article_node.find("ArticleDate")
            pub_date, precision = _parse_pub_date(article_date)
        if pub_date is None and citation.find("DateCompleted") is not None:
            pub_date, precision = _parse_pub_date(citation.find("DateCompleted"))
        publication_types = tuple(
            _text(item) for item in citation.findall("Article/PublicationTypeList/PublicationType") if _text(item)
        )
        events: list[LifecycleEvent] = []
        for correction in citation.findall(".//CommentsCorrectionsList/CommentsCorrections"):
            ref_type = str(correction.get("RefType") or "").strip()
            source = _text(correction.find("RefSource"))
            related_pmid = _text(correction.find("PMID")) or None
            kind = _event_kind(ref_type, publication_types)
            if kind is None:
                continue
            events.append(LifecycleEvent(
                kind=kind, ref_type=ref_type, source=source, pmid=related_pmid,
            ))
        for pub_type in publication_types:
            lowered = pub_type.casefold()
            if "retracted publication" in lowered and not any(event.kind == "retraction" for event in events):
                events.append(LifecycleEvent(kind="retraction", ref_type="PublicationType", source=pub_type))
            if "expression of concern" in lowered and not any(event.kind == "concern" for event in events):
                events.append(LifecycleEvent(kind="concern", ref_type="PublicationType", source=pub_type))
        parsed[pmid] = {
            "pmid": pmid,
            "published_at": pub_date,
            "published_at_precision": precision,
            "title": _text(article_node.find("ArticleTitle")) if article_node is not None else None,
            "publication_types": publication_types,
            "events": events,
        }
    return parsed


def _event_kind(ref_type: str, publication_types: Sequence[str]) -> str | None:
    if ref_type in _RETRACTION_TYPES:
        return "retraction"
    if ref_type in _UPDATE_TYPES:
        return "update"
    if ref_type in _CORRECTION_TYPES:
        return "correction"
    if ref_type in _CONCERN_TYPES:
        return "concern"
    if ref_type in _RETRACTION_NOTICE_TYPES or ref_type in _UPDATE_NOTICE_TYPES:
        return "notice"
    return None


def _default_transport(url: str, *, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ChinaLLM-guideline-lifecycle/1.0 (research prototype)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _efetch_url(pmids: Sequence[str], *, timeout: float = 60.0) -> str:
    params = urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    })
    return f"{_EFETCH}?{params}"


def fetch_pubmed_lifecycle(
    pmids: Sequence[str],
    *,
    transport: Callable[..., bytes] | None = None,
    timeout: float = 60.0,
) -> dict[str, GuidelineLifecycleRecord]:
    """Fetch and parse lifecycle records for up to 200 PMIDs per request."""
    ids = [str(pmid).strip() for pmid in pmids if str(pmid).strip()]
    if not ids:
        return {}
    fetch = transport or _default_transport
    first = parse_pubmed_xml(fetch(_efetch_url(ids, timeout=timeout), timeout=timeout))
    related_ids = [
        event.pmid for record in first.values() for event in record["events"]
        if event.pmid and event.pmid not in first and event.kind in {"retraction", "update", "correction", "concern"}
    ]
    related = parse_pubmed_xml(fetch(_efetch_url(list(dict.fromkeys(related_ids)), timeout=timeout), timeout=timeout)) if related_ids else {}
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    records: dict[str, GuidelineLifecycleRecord] = {}
    for pmid, row in first.items():
        events: list[LifecycleEvent] = []
        for event in row["events"]:
            related_row = related.get(event.pmid) if event.pmid else None
            if related_row is None and event.pmid:
                related_row = first.get(event.pmid)
            if related_row is not None:
                events.append(replace(
                    event,
                    date=related_row.get("published_at"),
                    date_precision=str(related_row.get("published_at_precision") or "unknown"),
                    title=related_row.get("title"),
                ))
            else:
                events.append(event)
        records[pmid] = GuidelineLifecycleRecord(
            pmid=pmid,
            published_at=row.get("published_at"),
            title=row.get("title"),
            publication_types=tuple(row.get("publication_types") or ()),
            events=tuple(events),
            checked_at=checked_at,
        )
    return records


class LifecycleCache:
    """Small JSON cache with per-record TTL; writes atomically."""

    def __init__(self, path: str | Path, *, ttl_days: float = 7.0):
        self.path = Path(path).expanduser()
        self.ttl = timedelta(days=float(ttl_days))

    def load(self) -> dict[str, GuidelineLifecycleRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if str(payload.get("schema_version") or "") != _CACHE_SCHEMA_VERSION:
            return {}
        records: dict[str, GuidelineLifecycleRecord] = {}
        for row in payload.get("records") or ():
            if not isinstance(row, Mapping):
                continue
            record = GuidelineLifecycleRecord.from_dict(row)
            if record.pmid:
                records[record.pmid] = record
        return records

    def fresh(self, record: GuidelineLifecycleRecord, *, now: datetime | None = None) -> bool:
        checked = _parse_datetime(record.checked_at)
        if checked is None:
            return False
        return (now or datetime.now(timezone.utc)) - checked <= self.ttl

    def save(self, records: Mapping[str, GuidelineLifecycleRecord]) -> None:
        if not self.path.parent:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "source": "pubmed_efetch",
            "saved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "records": [record.to_dict() for record in records.values()],
        }
        handle, temp_name = tempfile.mkstemp(prefix=".lifecycle-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class GuidelineLifecycleResolver:
    mode: str = "shadow"
    cache_path: str = ""
    ttl_days: float = 7.0
    transport: Callable[..., bytes] | None = None
    timeout: float = 60.0
    _cache: LifecycleCache | None = field(default=None, init=False, repr=False)
    last_trace: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized = str(self.mode or "shadow").strip().lower()
        if normalized not in {"shadow", "active"}:
            raise ValueError("GuidelineLifecycleResolver mode must be shadow or active")
        self.mode = normalized
        if self.cache_path:
            self._cache = LifecycleCache(self.cache_path, ttl_days=self.ttl_days)

    def resolve(self, pmids: Sequence[str], *, as_of: str | date | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        ids = [str(pmid).strip() for pmid in pmids if str(pmid).strip()]
        cached = self._cache.load() if self._cache is not None else {}
        stale = [pmid for pmid in ids if pmid not in cached or not self._cache.fresh(cached[pmid])]
        warnings: list[str] = []
        fetched: dict[str, GuidelineLifecycleRecord] = {}
        if stale:
            try:
                fetched = fetch_pubmed_lifecycle(stale, transport=self.transport, timeout=self.timeout)
            except Exception as exc:
                warnings.append(f"PubMed 指南生命周期查询失败（{type(exc).__name__}）：无法确认撤回/更新状态。")
        missing_after_fetch = [pmid for pmid in stale if pmid not in fetched]
        records = {
            **{pmid: cached[pmid] for pmid in ids if pmid in cached and pmid not in stale},
            **{pmid: cached[pmid] for pmid in missing_after_fetch if pmid in cached},
            **fetched,
        }
        if missing_after_fetch and any(pmid in cached for pmid in missing_after_fetch):
            warnings.append(f"{len(missing_after_fetch)} 个 PMID 未刷新，沿用过期缓存；其生命周期状态可能已变化。")
        if self._cache is not None and fetched:
            try:
                self._cache.save({**cached, **fetched})
            except OSError as exc:
                warnings.append(f"指南生命周期缓存写入失败（{type(exc).__name__}）。")
        statuses = {pmid: record.status_at(as_of) for pmid, record in records.items()}
        unresolved = [pmid for pmid in ids if pmid not in records]
        if unresolved:
            warnings.append(f"{len(unresolved)} 个 PMID 未取得生命周期记录；未判定撤回/更新。")
        trace = {
            "mode": self.mode,
            "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "as_of": (_parse_date(as_of) or date.today()).isoformat(),
            "requested": ids,
            "resolved": sorted(records),
            "unresolved": unresolved,
            "records": {pmid: record.to_dict() for pmid, record in records.items()},
            "statuses": statuses,
            "warnings": warnings,
        }
        METRICS.observe("guideline_lifecycle_duration_ms", (time.perf_counter() - started) * 1000)
        METRICS.increment("guideline_lifecycle_records_total", len(records))
        self.last_trace = trace
        return trace

    def annotate(self, documents: Sequence[Any], *, as_of: str | date | None = None) -> tuple[list[Any], dict[str, Any]]:
        pmids: list[str] = []
        synthetic_excluded: list[str] = []
        for document in documents:
            if str(getattr(document, "kind", "") or "") != "guideline":
                continue
            pmid = str(getattr(document, "pmid", None) or "").strip()
            if not pmid:
                continue
            if bool((getattr(document, "metadata", {}) or {}).get("synthetic", False)):
                synthetic_excluded.append(pmid)
                continue
            pmids.append(pmid)
        trace = self.resolve(list(dict.fromkeys(pmids)), as_of=as_of)
        if synthetic_excluded:
            trace["synthetic_excluded"] = synthetic_excluded
        trace["applied"] = self.mode == "active"
        trace["documents"] = []
        annotated: list[Any] = []
        for document in documents:
            pmid = str(getattr(document, "pmid", None) or "").strip()
            status = trace["statuses"].get(pmid)
            if not pmid or status is None or str(getattr(document, "kind", "") or "") != "guideline":
                annotated.append(document)
                continue
            lifecycle = dict(status)
            lifecycle_status = status["status"]
            if self.mode == "active":
                metadata = {**dict(getattr(document, "metadata", {}) or {}), "lifecycle": lifecycle}
                if lifecycle_status == "retracted":
                    annotated.append(replace(document, status="withdrawn", metadata=metadata))
                elif lifecycle_status == "superseded":
                    latest = status.get("latest_update") or {}
                    annotated.append(replace(document, status="superseded", superseded_by=latest.get("pmid"), metadata=metadata))
                else:
                    annotated.append(replace(document, metadata=metadata))
            else:
                annotated.append(document)
            trace["documents"].append({
                "document_id": str(getattr(document, "document_id", "")),
                "pmid": pmid,
                "lifecycle_status": lifecycle_status,
                "applied": self.mode == "active" and lifecycle_status in {"retracted", "superseded"},
            })
        return annotated, trace

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cache_path": str(self._cache.path) if self._cache is not None else "",
            "ttl_days": self.ttl_days,
            "source": "pubmed_efetch",
        }


def load_lifecycle_resolver(
    *,
    mode: str,
    cache_path: str = "",
    ttl_days: float = 7.0,
    transport: Callable[..., bytes] | None = None,
) -> GuidelineLifecycleResolver | None:
    if str(mode or "off").strip().lower() == "off":
        return None
    return GuidelineLifecycleResolver(mode=mode, cache_path=cache_path, ttl_days=ttl_days, transport=transport)
