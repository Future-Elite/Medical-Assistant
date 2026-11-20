"""Deterministic sibling / hierarchy confusion probes from a MeSH snapshot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mesh import ConceptIndex


@dataclass(frozen=True)
class SiblingProbe:
    probe_id: str
    target_ui: str
    target_label: str
    query_zh: str
    query_en: str
    confusable_uis: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "target_ui": self.target_ui,
            "target_label": self.target_label,
            "query_zh": self.query_zh,
            "query_en": self.query_en,
            "confusable_uis": list(self.confusable_uis),
        }


def build_sibling_probes(
    index: ConceptIndex,
    *,
    max_probes: int = 50,
    max_confusables: int = 3,
    require_chinese: bool = False,
) -> tuple[SiblingProbe, ...]:
    """Build probes for descriptors that have a deterministic confusion set.

    Descriptors with a reviewed Chinese alias are ranked first because the
    target task is Chinese query -> English evidence.  ``require_chinese=True``
    keeps only those descriptors; otherwise English-only probes are included
    with an empty ``query_zh`` and an explicit warning in the report.
    """
    rows: list[SiblingProbe] = []
    for descriptor in index.lexicon.descriptors:
        confusables = index.confusion_set(descriptor.ui, size=max_confusables)
        if not confusables:
            continue
        surfaces = index.chinese_surfaces(descriptor.ui)
        if require_chinese and not surfaces:
            continue
        query_zh = f"{surfaces[0]}的临床表现和处理是什么？" if surfaces else ""
        rows.append(SiblingProbe(
            probe_id=f"sibling-{descriptor.ui.lower()}",
            target_ui=descriptor.ui,
            target_label=descriptor.label,
            query_zh=query_zh,
            query_en=f"{descriptor.label} clinical features and management",
            confusable_uis=confusables,
        ))
    rows.sort(key=lambda row: (not bool(row.query_zh), row.target_ui))
    return tuple(rows[:max_probes])
