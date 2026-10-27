"""Shared normalization for external medical evidence tools."""

import hashlib
import re
from typing import Any, Dict, Iterable, List


class EvidenceAggregator:
    """Normalize, deduplicate, and format evidence without changing RAG rank order."""

    REQUIRED_FIELDS = (
        "source_type",
        "source_id",
        "title",
        "content",
        "date",
        "url",
        "metadata",
    )
    MODEL_EXCERPT_CHARS = 900

    @staticmethod
    def _fragment_key(record: Dict[str, Any]) -> tuple[str, str, str, str]:
        """Deduplicate exact fragments, not every passage from the same paper."""
        metadata = record.get("metadata") or {}
        content = re.sub(r"\s+", " ", str(record.get("content") or "")).strip()
        return (
            str(record.get("source_id") or "").strip().upper(),
            str(metadata.get("document_id") or "").strip(),
            str(metadata.get("locator") or "").strip(),
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def aggregate(cls, records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique = {}
        for record in records:
            normalized = {field: record.get(field) for field in cls.REQUIRED_FIELDS}
            normalized["metadata"] = normalized["metadata"] or {}
            source_id = str(normalized["source_id"] or "").strip()
            if not source_id:
                continue
            normalized["source_id"] = source_id
            unique.setdefault(cls._fragment_key(normalized), normalized)
        return list(unique.values())

    @staticmethod
    def format_for_model(evidence: List[Dict[str, Any]]) -> str:
        cards = []
        for index, item in enumerate(evidence, 1):
            metadata = item["metadata"]
            content = re.sub(r"\s+", " ", str(item["content"] or "")).strip()
            if len(content) > EvidenceAggregator.MODEL_EXCERPT_CHARS:
                content = content[:EvidenceAggregator.MODEL_EXCERPT_CHARS].rstrip() + "…"
            cards.append(
                f"Evidence card {index}\n"
                f"Citation: {metadata.get('citation_id') or item['source_id']}\n"
                f"Source: [{item['source_id']}] {item['title']}\n"
                f"Date: {item['date'] or 'unknown'}\n"
                f"Question relevance: {metadata.get('question_relevance', 'not_assessed')}\n"
                f"Population: {metadata.get('population') or 'not_reported'}\n"
                f"Population applicability: {metadata.get('population_applicability', 'not_assessed')}\n"
                f"Content mode: {metadata.get('content_mode', 'substantive')}\n"
                f"Locator: {metadata.get('locator') or 'unavailable'}\n"
                f"Excerpt: {content or 'No substantive content available.'}"
            )
        return "\n\n".join(cards)
