"""Guideline families, read from guideline metadata rather than hard-coded.

A family groups guidelines that answer the same clinical question (e.g. different
feeding/nutrition guidelines), so the resolver can tell same-question coverage
from cross-topic coverage.  The family is declared per guideline in the
evaluation fixture / catalog metadata; there is no built-in list of guideline
IDs, so adding a guideline only requires metadata, not code.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def family_index(guidelines: Iterable[Mapping[str, Any]] | None) -> dict[str, str]:
    """Map ``document_id`` -> declared family; guidelines without one are omitted."""
    index: dict[str, str] = {}
    for item in guidelines or ():
        document_id = str(item.get("document_id") or "").strip()
        family = str(item.get("family") or "").strip()
        if document_id and family:
            index[document_id] = family
    return index


def family_of(guideline_id: str | None, index: Mapping[str, str] | None = None) -> str | None:
    """Return the declared family for ``guideline_id``, or ``None`` if undeclared."""
    if not guideline_id or not index:
        return None
    return index.get(guideline_id)
