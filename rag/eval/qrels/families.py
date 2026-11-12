"""Guideline families used to distinguish same-question coverage from cross-topic coverage."""
from __future__ import annotations

FAMILIES = {
    "喂养/营养": (
        "pdf-neo-feeding-vlbw-2015",
        "pdf-neo-human-milk-fortification-2019",
        "pdf-neo-parenteral-nutrition-2012",
    ),
    "呼吸支持": ("pdf-neo-pocus-espnic-2020",),
    "黄疸": ("pdf-aap-hyperbilirubinemia-2022",),
}


def family_of(guideline_id: str | None) -> str | None:
    for family, members in FAMILIES.items():
        if guideline_id in members:
            return family
    return None
