"""Shared normalization for external medical evidence tools."""

import json
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
            unique.setdefault(source_id.upper(), normalized)
        return list(unique.values())

    @staticmethod
    def format_for_model(evidence: List[Dict[str, Any]]) -> str:
        return "\n\n".join(
            f"{index}. [{item['source_id']}] {item['title']}\n"
            f"Date: {item['date'] or 'unknown'}\n"
            f"URL: {item['url'] or 'unavailable'}\n"
            f"Content: {item['content'] or 'No content available.'}\n"
            f"Metadata: {json.dumps(item['metadata'], ensure_ascii=False)}"
            for index, item in enumerate(evidence, 1)
        )
