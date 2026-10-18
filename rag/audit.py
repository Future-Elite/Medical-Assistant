"""Grounded-answer auditing: citation structure plus an optional verifier.

This module is deliberately downstream of retrieval.  It never changes evidence
ranking and never turns an unsupported statement into a medical conclusion.
By default it checks citation structure: every claim must cite a ``citation_id``
that belongs to the same retrieval.  Claim-to-evidence semantic support is only
reported when the caller injects a verifier; with no verifier the label is
``not_checked`` and precision/recall stay ``None`` rather than falling back to a
lexical heuristic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .models import EvidencePackage


_CITATION_ID_RE = re.compile(r"[A-Za-z0-9._-]+:cit-\d+", re.IGNORECASE)
_SHORT_CITATION_RE = re.compile(r"cit-\d+", re.IGNORECASE)


class ClaimVerifier(Protocol):
    """A pluggable sentence-to-grounding-document verifier."""

    @property
    def available(self) -> bool: ...

    @property
    def name(self) -> str: ...

    def verify(self, pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[bool, float | None]]: ...


@dataclass
class GroundedAnswerAuditor:
    """Audits an answer only against citations belonging to one retrieval.

    ``verifier`` is optional.  Without one the audit is structural: it detects
    missing citations and citations from another retrieval, and computes
    citation completeness.  Semantic support stays ``not_checked``/``None``.
    """

    verifier: ClaimVerifier | None = None
    _checks: dict[tuple[str, str], tuple[bool, float | None]] = field(default_factory=dict, init=False, repr=False)

    def audit(self, *, package: EvidencePackage, answer: str, claim_records: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("answer must be a non-empty string")
        documents = {item.document.document_id: item.document.text for item in package.ranked_documents}
        citations = {item.citation_id: item for item in package.citations}
        claims = split_answer_claims(answer) if claim_records is None else []
        rows = [_claim_row(claim, citations) for claim in claims]
        if claim_records is not None:
            for record in claim_records:
                text = str(record.get("claim_en") or "").strip()
                ids = list(dict.fromkeys(record.get("citation_ids") or []))
                if not text or not ids or not all(isinstance(item, str) and _CITATION_ID_RE.fullmatch(item) for item in ids):
                    raise ValueError("structured claim requires text and complete citation IDs")
                rows.append({
                    "claim": text,
                    "claim_zh": record.get("claim_zh"),
                    "claim_en": text,
                    "verifier_claim": text,
                    "citation_ids": ids,
                    "valid_citation_ids": [item for item in ids if item in citations],
                    "invalid_citation_ids": [item for item in ids if item not in citations],
                    "citation_checks": [],
                    "ragtruth_label": "not_checked",
                })
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
        verifier_available = bool(self.verifier is not None and self.verifier.available)
        if pairs:
            if verifier_available:
                try:
                    missing = list(dict.fromkeys(pair for pair in pairs if pair not in self._checks))
                    if missing:
                        new_results = list(self.verifier.verify(missing))
                        if len(new_results) != len(missing):
                            raise RuntimeError("Verifier returned an incomplete result set")
                        if len(self._checks) + len(missing) > 512:
                            self._checks.clear()
                        self._checks.update(zip(missing, new_results))
                    results = [self._checks[pair] for pair in pairs]
                except RuntimeError as exc:
                    verification_warning = str(exc)
            else:
                verification_warning = _unavailable_reason(self.verifier)
        elif rows:
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
            "verifier": {
                "name": self.verifier.name if self.verifier is not None else "none",
                "available": verifier_available,
                "mode": "claim_verification" if verifier_available else "structural_only",
            },
            "claims": rows,
            "alce_like": _alce_like(rows, verification_completed=bool(results)),
            "warnings": _warnings(verification_warning, rows),
            "limitations": [
                "默认审计只校验引用结构与 citation_id 归属；未注入校验器时不判断语义支持。",
                "当前 PubMed 摘要通常为英文；中文生成主张的跨语言校验效果尚未在本项目评测。",
            ],
        }


def split_answer_claims(answer: str) -> list[str]:
    """Bind the full preceding text to each citation group, including semicolons."""
    rows = []
    for paragraph in normalize_citation_groups(answer).splitlines():
        matches = list(re.finditer(
            r"(?:\[[A-Za-z0-9._-]+:cit-\d+\][ \t]*)+[。！？!?；;.]?",
            paragraph, re.IGNORECASE,
        ))
        if not matches:
            rows.extend(part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", paragraph) if part.strip())
            continue
        start = 0
        for match in matches:
            text = paragraph[start:match.end()].strip()
            if text:
                rows.append(text)
            start = match.end()
        if paragraph[start:].strip():
            rows.append(paragraph[start:].strip())
    return rows


def parse_citation_ids(text: str) -> tuple[str, ...]:
    """Parse full and abbreviated IDs from citation brackets.

    For example, ``[retrieval-x:cit-6, cit-2]`` expands to two complete IDs.
    Non-citation Markdown brackets are ignored.
    """
    parsed: list[str] = []
    for group in re.findall(r"\[([^\[\]]+)\]", str(text or "")):
        prefix = None
        for token in re.split(r"\s*[,，、;；]\s*", group.strip()):
            token = token.strip()
            full = _CITATION_ID_RE.fullmatch(token)
            if full:
                citation_id = full.group(0)
                prefix = citation_id.rsplit(":", 1)[0]
            elif prefix and _SHORT_CITATION_RE.fullmatch(token):
                citation_id = f"{prefix}:{token}"
            else:
                continue
            if citation_id not in parsed:
                parsed.append(citation_id)
    return tuple(parsed)


def normalize_citation_groups(text: str) -> str:
    """Render every citation as an independent bracketed full ID."""
    def replace(match: re.Match[str]) -> str:
        ids = parse_citation_ids(match.group(0))
        return "".join(f"[{citation_id}]" for citation_id in ids) if ids else match.group(0)

    return re.sub(r"\[[^\[\]]+\]", replace, str(text or ""))


def strip_citation_groups(text: str) -> str:
    """Remove citation brackets while retaining unrelated Markdown text."""
    def replace(match: re.Match[str]) -> str:
        return "" if parse_citation_ids(match.group(0)) else match.group(0)

    return re.sub(r"\[[^\[\]]+\]", replace, str(text or "")).strip()


def _claim_row(claim: str, citations: Mapping[str, Any]) -> dict[str, Any]:
    cited = parse_citation_ids(claim)
    valid = [item for item in cited if item in citations]
    invalid = [item for item in cited if item not in citations]
    return {
        "claim": claim,
        "verifier_claim": strip_citation_groups(claim),
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
        warnings.append(f"校验器判定 {unsupported} 条主张未被其所引证据支持。")
    return warnings


def _unavailable_reason(verifier: ClaimVerifier | None) -> str:
    if verifier is None:
        return "未配置主张级校验器；仅执行引用结构检查，语义支持未测量。"
    return str(getattr(verifier, "unavailable_reason", "校验器不可用；未执行主张级校验。"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _probability(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None




