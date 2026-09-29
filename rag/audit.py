"""Grounded-answer auditing inspired by MiniCheck, ALCE, and RAGTruth.

This module is deliberately downstream of retrieval.  It never changes evidence
ranking and never turns an unsupported statement into a medical conclusion.
MiniCheck is an optional, real sentence-level verifier.  If its package/model is
unavailable, the result is explicitly marked ``not_checked`` rather than using a
heuristic as a substitute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .config import get_config
from .models import EvidencePackage


class ClaimVerifier(Protocol):
    """A pluggable sentence-to-grounding-document verifier."""

    @property
    def available(self) -> bool: ...

    @property
    def name(self) -> str: ...

    def verify(self, pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[bool, float | None]]: ...


@dataclass
class MiniCheckVerifier:
    """Lazy adapter for the official ``minicheck`` Python package.

    Install the package and fill ``minicheck_model`` in ``config.py`` before enabling
    this adapter.  The official scorer may download model weights on first use;
    that is intentionally not done at import time or as an implicit fallback.
    """

    model_name: str | None = None
    cache_dir: str | None = None
    _scorer: Any = None
    _load_error: str | None = None

    def __post_init__(self) -> None:
        config = get_config()
        if self.model_name is None:
            self.model_name = config.minicheck_model or None
        if self.cache_dir is None:
            self.cache_dir = config.minicheck_cache_dir or None
    @property
    def name(self) -> str:
        return "minicheck"

    @property
    def available(self) -> bool:
        return bool(self.model_name) and self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if not self.model_name:
            return "未在 config.py 中配置 minicheck_model；未执行主张级模型校验。"
        self._ensure_loaded()
        return self._load_error or "MiniCheck 当前不可用。"

    def verify(self, pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[bool, float | None]]:
        if not pairs:
            return []
        if not self.available:
            raise RuntimeError(self.unavailable_reason)
        claims = [claim for claim, _ in pairs]
        documents = [document for _, document in pairs]
        try:
            labels, probabilities, _, _ = self._scorer.score(docs=documents, claims=claims)
        except Exception as exc:
            raise RuntimeError(f"MiniCheck 推理失败：{type(exc).__name__}") from exc
        return [(bool(label), _probability(probability)) for label, probability in zip(labels, probabilities)]

    def _ensure_loaded(self) -> bool:
        if self._scorer is not None:
            return True
        if self._load_error or not self.model_name:
            return False
        try:
            from minicheck.minicheck import MiniCheck  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._scorer = MiniCheck(**kwargs)
            return True
        except Exception as exc:
            self._load_error = f"MiniCheck 未就绪：{type(exc).__name__}。"
            return False


@dataclass
class GroundedAnswerAuditor:
    """Audits an answer only against citations belonging to one retrieval."""

    verifier: ClaimVerifier

    def audit(self, *, package: EvidencePackage, answer: str) -> dict[str, Any]:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("answer must be a non-empty string")
        documents = {item.document.document_id: item.document.text for item in package.ranked_documents}
        citations = {item.citation_id: item for item in package.citations}
        claims = _sentences(answer)
        rows = [_claim_row(claim, citations) for claim in claims]
        pairs: list[tuple[str, str]] = []
        pair_locations: list[tuple[int, int]] = []
        for row_index, row in enumerate(rows):
            for citation_index, citation_id in enumerate(row["valid_citation_ids"]):
                citation = citations[citation_id]
                evidence = documents.get(citation.document_id, "")
                if evidence:
                    pairs.append((row["verifier_claim"], evidence))
                    pair_locations.append((row_index, citation_index))
        verification_warning = None
        results: Sequence[tuple[bool, float | None]] = []
        if pairs:
            if self.verifier.available:
                try:
                    results = self.verifier.verify(pairs)
                except RuntimeError as exc:
                    verification_warning = str(exc)
            else:
                verification_warning = _unavailable_reason(self.verifier)
        elif claims:
            verification_warning = "回答中没有可用于证据校验的有效 citation_id。"
        per_link: dict[tuple[int, int], tuple[bool, float | None]] = dict(zip(pair_locations, results))
        for row_index, row in enumerate(rows):
            links = [per_link[(row_index, citation_index)] for citation_index in range(len(row["valid_citation_ids"])) if (row_index, citation_index) in per_link]
            row["citation_checks"] = [
                {"citation_id": citation_id, "supported": supported, "score": score}
                for citation_id, (supported, score) in zip(row["valid_citation_ids"], links)
            ]
            row["ragtruth_label"] = _ragtruth_label(row, links, bool(results))
        return {
            "retrieval_id": package.retrieval_id,
            "verifier": {"name": self.verifier.name, "available": self.verifier.available},
            "claims": rows,
            "alce_like": _alce_like(rows, verification_completed=bool(results)),
            "warnings": _warnings(verification_warning, rows),
            "limitations": [
                "MiniCheck 为句子级主张-文档支持校验，不等同于临床正确性或指南推荐等级验证。",
                "当前 PubMed 摘要通常为英文；中文生成主张的跨语言校验效果尚未在本项目评测。",
            ],
        }


def _sentences(answer: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", answer)
    return [part.strip() for part in parts if part.strip()]


def _claim_row(claim: str, citations: Mapping[str, Any]) -> dict[str, Any]:
    cited = tuple(dict.fromkeys(re.findall(r"\[([^\[\]]+)\]", claim)))
    valid = [item for item in cited if item in citations]
    invalid = [item for item in cited if item not in citations]
    return {
        "claim": claim,
        "verifier_claim": re.sub(r"\[[^\[\]]+\]", "", claim).strip(),
        "citation_ids": list(cited),
        "valid_citation_ids": valid,
        "invalid_citation_ids": invalid,
        "citation_checks": [],
        "ragtruth_label": "not_checked",
    }


def _ragtruth_label(row: Mapping[str, Any], links: Sequence[tuple[bool, float | None]], verification_completed: bool) -> str:
    if not row["citation_ids"]:
        return "citation_missing"
    if row["invalid_citation_ids"]:
        return "invalid_citation"
    if not verification_completed:
        return "not_checked"
    return "supported" if any(supported for supported, _ in links) else "evident_baseless_info"


def _alce_like(rows: Sequence[Mapping[str, Any]], *, verification_completed: bool) -> dict[str, float | int | None]:
    total_claims = len(rows)
    cited_claims = sum(1 for row in rows if row["valid_citation_ids"])
    valid_links = sum(len(row["valid_citation_ids"]) for row in rows)
    supported_claims = sum(1 for row in rows if row["ragtruth_label"] == "supported")
    supported_links = sum(
        1 for row in rows for check in row["citation_checks"] if check["supported"]
    )
    return {
        "claim_count": total_claims,
        "citation_completeness": _ratio(cited_claims, total_claims),
        "citation_precision": _ratio(supported_links, valid_links) if verification_completed else None,
        "citation_recall": _ratio(supported_claims, total_claims) if verification_completed else None,
        "valid_citation_links": valid_links,
    }


def _warnings(verification_warning: str | None, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings = [verification_warning] if verification_warning else []
    missing = sum(1 for row in rows if row["ragtruth_label"] == "citation_missing")
    invalid = sum(1 for row in rows if row["ragtruth_label"] == "invalid_citation")
    unsupported = sum(1 for row in rows if row["ragtruth_label"] == "evident_baseless_info")
    if missing:
        warnings.append(f"发现 {missing} 条主张没有绑定本次检索的引用。")
    if invalid:
        warnings.append(f"发现 {invalid} 条主张引用了本次检索范围外的 citation_id。")
    if unsupported:
        warnings.append(f"MiniCheck 判定 {unsupported} 条主张未被其所引证据支持。")
    return warnings


def _unavailable_reason(verifier: ClaimVerifier) -> str:
    return str(getattr(verifier, "unavailable_reason", "校验器未配置；未执行主张级模型校验。"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _probability(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None




