"""Online drug-label connector (openFDA) for the RAG package.

The connector is intentionally read-only and label-oriented: it returns US FDA
drug-label sections, not dosing advice, Chinese regulatory data, or a patient-
specific prescription.  Only reviewed query terms are sent; no PatientSnapshot
or free-text history leaves the process.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Callable, Mapping

from ..core.config import get_config
from ..core.models import QueryPlan, SearchResult, SourceDocument
from .connectors import KnowledgeConnector

OPENFDA_ENDPOINT = "https://api.fda.gov/drug/label.json"
_FIELDS = (
    "openfda.generic_name",
    "openfda.brand_name",
    "indications_and_usage",
    "purpose",
    "description",
)


class DrugLabelError(RuntimeError):
    def __init__(self, message: str, *, stage: str, status_code: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code


@dataclass
class DrugLabelConnector(KnowledgeConnector):
    source_id: str = "drug-openfda"
    kinds: tuple[str, ...] = ("drug",)
    transport: Callable[[str, float], bytes] | None = None
    timeout_seconds: float | None = None
    max_results: int = 5
    api_key: str = ""

    def __post_init__(self) -> None:
        config = get_config()
        if self.timeout_seconds is None:
            self.timeout_seconds = float(config.request_timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("drug-label timeout must be positive")
        self.api_key = self.api_key or (config.openfda_api_key or "").strip()

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        terms = _safe_terms(plan)
        if not terms:
            return SearchResult(
                self.source_id, (),
                ("没有可用的英文药品线索；未向 openFDA 发送查询。",),
            )
        limit = max(1, min(int(limit), self.max_results))
        url = self._url(terms, limit)
        try:
            raw = self._fetch(url)
        except DrugLabelError as exc:
            return SearchResult(self.source_id, (), (str(exc),))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return SearchResult(self.source_id, (), (f"openFDA 响应无法解析：{type(exc).__name__}: {str(exc)[:120]}",))
        documents: list[SourceDocument] = []
        for row in payload.get("results") or ():
            document = _document(row)
            if document is not None:
                documents.append(document)
        warnings = []
        if not documents:
            warnings.append("openFDA 没有返回药品标签；不能据此判断药品不存在。")
        if payload.get("meta", {}).get("results", {}).get("skip"):
            warnings.append("openFDA 返回分页结果；当前只取第一页。")
        return SearchResult(self.source_id, tuple(documents), tuple(warnings))

    def _url(self, terms: list[str], limit: int) -> str:
        clauses = []
        for term in terms:
            quoted = '"' + term.replace('"', " ") + '"'
            clauses.extend(f"{field}:{quoted}" for field in _FIELDS)
        search = "+OR+".join(clauses)
        params = {"search": search, "limit": str(limit)}
        if self.api_key:
            params["api_key"] = self.api_key
        return OPENFDA_ENDPOINT + "?" + urllib.parse.urlencode(params, safe=':+')

    def _fetch(self, url: str) -> bytes:
        if self.transport is not None:
            try:
                return self.transport(url, float(self.timeout_seconds))
            except Exception as exc:
                raise DrugLabelError(
                    f"openFDA 请求失败：{type(exc).__name__}: {str(exc)[:160]}",
                    stage="openfda",
                ) from exc
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "chinallm-rag/6.7.3"},
        )
        try:
            with urllib.request.urlopen(request, timeout=float(self.timeout_seconds)) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise DrugLabelError(
                f"openFDA HTTP {exc.code}", stage="openfda", status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DrugLabelError(
                f"openFDA 请求失败：{type(exc).__name__}: {str(exc)[:160]}",
                stage="openfda",
            ) from exc


def _safe_terms(plan: QueryPlan) -> list[str]:
    values: list[str] = []
    for item in (
        *plan.english_terms, *plan.mesh_terms, *plan.question_english_terms,
    ):
        text = str(item or "").strip()
        if not text or _is_cjk(text):
            continue
        if text not in values:
            values.append(text)
    return values[:5]


def _is_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _document(row: Mapping[str, Any]) -> SourceDocument | None:
    if not isinstance(row, Mapping):
        return None
    openfda = row.get("openfda") if isinstance(row.get("openfda"), Mapping) else {}
    names = [
        *[str(item) for item in openfda.get("generic_name") or ()],
        *[str(item) for item in openfda.get("brand_name") or ()],
    ]
    label_id = str(row.get("id") or "").strip()
    if not label_id:
        return None
    title = (names[0] if names else "FDA drug label") + " · FDA label"
    text = _sections(row)
    if not text:
        return None
    set_id = str((openfda.get("set_id") or [""])[0] if isinstance(openfda.get("set_id"), list) else openfda.get("set_id") or "")
    url = (
        f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={urllib.parse.quote(set_id)}"
        if set_id else f"{OPENFDA_ENDPOINT}?search=id:{urllib.parse.quote(label_id)}"
    )
    return SourceDocument(
        source_id="drug-openfda",
        document_id=f"openfda:{label_id}",
        kind="drug",
        title=title,
        text=text,
        url=url,
        published_at=_date(row.get("effective_time")),
        locator="openFDA drug label",
        authority="FDA",
        version_id=str(row.get("effective_time") or "") or None,
        population="US label",
        metadata={
            "database": "openFDA",
            "country": "US",
            "note": "美国 FDA 药品说明书；不是中国药品库、剂量建议或处方依据。",
            "generic_name": list(openfda.get("generic_name") or ()),
            "brand_name": list(openfda.get("brand_name") or ()),
            "manufacturer": list(openfda.get("manufacturer_name") or ()),
            "route": list(openfda.get("route") or ()),
        },
    )


def _sections(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("indications_and_usage", "dosage_and_administration", "warnings", "description"):
        value = row.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = str(item or "").strip()
            if text:
                parts.append(f"{key}: {text}")
    return "\n".join(parts)[:5000]


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
        except ValueError:
            return None
    return None
