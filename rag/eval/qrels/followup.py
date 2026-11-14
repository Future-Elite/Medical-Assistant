"""Parallel follow-up retrieval for a guideline-applicability card.

The triage card answers "what fact should I check next".  This module adds the
parallel retrieval that doctors expect after a guideline/fact comparison:

* other local guidelines whose criteria already cover the patient;
* population-qualifier snippets from the local guideline chunks (page/locator);
* guideline-family status conflicts (e.g. feeding guidelines disagree);
* an external-evidence query for cases with no local coverage (not executed
  offline; the caller can send it to the existing PubMed connector).

It does not call the network and does not make clinical recommendations.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from .families import family_index

_CHUNK_DIR = Path(__file__).resolve().parents[3] / "data" / "guideline_parsing"

_POPULATION_TERMS = {
    "ga": ("preterm", "gestational age", "37 weeks", "35 weeks", "早产", "胎龄"),
    "bw": ("very low birth weight", "VLBW", "birth weight", "1500 g", "出生体重", "低出生体重"),
    "resp": ("respiratory support", "CPAP", "mechanical ventilation", "呼吸支持", "机械通气"),
}

def _search_term_map(fixture: Mapping[str, Any]) -> dict[str, str]:
    """Guideline -> English query phrase, derived from declared ``search_terms``."""
    out: dict[str, str] = {}
    for item in fixture.get("guidelines", []):
        document_id = str(item.get("document_id") or "")
        terms = [str(term) for term in (item.get("search_terms") or ()) if str(term).strip()]
        if document_id and terms:
            out[document_id] = terms[0]
    return out


def build_followup(
    *,
    snapshot: Mapping[str, Any],
    derived: Mapping[str, Any],
    target_guideline_id: str | None,
    target_topic: str | None,
    gold: Mapping[str, Any],
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    titles = {item["document_id"]: item.get("title") or item["document_id"]
              for item in fixture.get("guidelines", [])}
    coverage = _coverage_guidelines(gold, titles, target_guideline_id)
    qualifiers = _population_qualifiers(derived, titles, target_topic=target_topic)
    families = _status_families(gold, titles, family_index(fixture.get("guidelines", [])))
    external = _external_query(
        derived, target_guideline_id, target_topic, titles,
        search_terms=_search_term_map(fixture),
        has_local_coverage=bool(coverage),
    )
    return {
        "coverage_guidelines": coverage,
        "population_qualifiers": qualifiers,
        "population_qualifiers_note": (
            "本地没有该主题的指南分块；无法做该主题的亚群原文检索。"
            if target_topic and not qualifiers else None
        ),
        "status_families": families,
        "external_query": external,
        "lifecycle_status": "not_checked",
        "lifecycle_note": (
            "本地 4 份指南没有 PubMed PMID；AAP 为 metadata_only。"
            "v6.7.1 生命周期层存在，但尚未接入本卡片；不得写成 current/已核验。"
        ),
    }


def _coverage_guidelines(
    gold: Mapping[str, Any], titles: Mapping[str, str], target_id: str | None,
) -> list[dict[str, Any]]:
    out = []
    for guideline in gold.get("guidelines", []):
        if guideline["guideline_id"] == target_id:
            continue
        if guideline["status"] != "possible_match":
            continue
        out.append({
            "guideline_id": guideline["guideline_id"],
            "title": titles.get(guideline["guideline_id"], guideline["guideline_id"]),
            "matched_criteria": [
                criterion["criterion_id"] for criterion in guideline["criteria"]
                if criterion["status"] == "match"
            ],
            "locators": sorted({
                criterion.get("locator") for criterion in guideline["criteria"]
                if criterion.get("locator")
            }),
        })
    return out


def _population_qualifiers(
    derived: Mapping[str, Any], titles: Mapping[str, str], *, target_topic: str | None = None,
) -> list[dict[str, Any]]:
    terms = _terms_for_patient(derived)
    if target_topic:
        terms.append(str(target_topic).replace("_", " "))
    if not terms:
        return []
    guideline_ids = _topic_guideline_ids(titles, target_topic)
    rows: list[dict[str, Any]] = []
    for guideline_id in guideline_ids:
        for chunk in _load_chunks(guideline_id):
            text = str(chunk.get("text") or "")
            lowered = text.lower()
            matched = [term for term in terms if term.lower() in lowered]
            if not matched:
                continue
            rows.append({
                "guideline_id": guideline_id,
                "title": titles.get(guideline_id, guideline_id),
                "chunk_id": chunk.get("chunk_id"),
                "section_path": chunk.get("section_path"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "matched_terms": matched,
                "text": text[:240],
            })
    rows.sort(key=lambda row: (-len(row["matched_terms"]), str(row["guideline_id"])))
    # Deduplicate by guideline/chunk, keep at most 2 per guideline.
    seen: set[tuple[str, str]] = set()
    result = []
    per_guideline: dict[str, int] = {}
    for row in rows:
        key = (row["guideline_id"], str(row.get("chunk_id")))
        if key in seen or per_guideline.get(row["guideline_id"], 0) >= 2:
            continue
        seen.add(key)
        per_guideline[row["guideline_id"]] = per_guideline.get(row["guideline_id"], 0) + 1
        result.append(row)
        if len(result) >= 6:
            break
    return result


def _status_families(
    gold: Mapping[str, Any], titles: Mapping[str, str], index: Mapping[str, str],
) -> list[dict[str, Any]]:
    status_by_id = {item["guideline_id"]: item["status"] for item in gold.get("guidelines", [])}
    grouped: dict[str, list[str]] = {}
    for guideline_id, family in index.items():
        grouped.setdefault(family, []).append(guideline_id)
    out = []
    for family, ids in grouped.items():
        members = [
            {
                "guideline_id": guideline_id,
                "title": titles.get(guideline_id, guideline_id),
                "status": status_by_id.get(guideline_id),
            }
            for guideline_id in ids if guideline_id in status_by_id
        ]
        if not members:
            continue
        statuses = {member["status"] for member in members}
        out.append({
            "family": family,
            "members": members,
            "conflict": len(statuses) > 1,
        })
    return out


def _external_query(
    derived: Mapping[str, Any], target_id: str | None, target_topic: str | None,
    titles: Mapping[str, str], *, search_terms: Mapping[str, str] | None = None,
    has_local_coverage: bool = False,
) -> dict[str, Any] | None:
    if has_local_coverage and not target_topic:
        return None
    if target_topic:
        topic = str(target_topic).replace("_", " ")
        return {
            "executed": False,
            "reason": "offline evaluation; send to the existing PubMed connector if needed",
            "query_zh": f"{_population_label(derived)} {topic} 指南",
            "query_en": (
                '("Infant, Newborn"[MeSH] OR neonatal[tiab] OR preterm[tiab]) AND '
                f'("{topic}"[tiab]) AND '
                "(practice guideline[pt] OR guideline[pt])"
            ),
        }
    if not target_id:
        return None
    title = titles.get(target_id, target_id)
    population = _population_label(derived)
    english = (search_terms or {}).get(target_id, title)
    return {
        "executed": False,
        "reason": "offline evaluation; send to the existing PubMed connector if needed",
        "query_zh": f"{population} {title} 指南",
        "query_en": (
            f'("Infant, Newborn"[MeSH] OR neonatal[tiab] OR preterm[tiab]) AND '
            f'("{english}"[tiab] OR guideline[tiab]) AND '
            "(practice guideline[pt] OR guideline[pt])"
        ),
    }


def _topic_guideline_ids(titles: Mapping[str, str], target_topic: str | None) -> tuple[str, ...]:
    if not target_topic:
        return tuple(titles)
    topic = str(target_topic).replace("_", " ").lower()
    return tuple(
        guideline_id for guideline_id, title in titles.items()
        if topic in str(title).lower()
    )


def _terms_for_patient(derived: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    ga = derived.get("ga_days")
    bw = derived.get("birth_weight_g")
    if ga is not None and ga < 259:
        terms.extend(_POPULATION_TERMS["ga"])
    if bw is not None and bw < 1500:
        terms.extend(_POPULATION_TERMS["bw"])
    if derived.get("respiratory_status") == "observed_positive":
        terms.extend(_POPULATION_TERMS["resp"])
    return list(dict.fromkeys(terms))


def _population_label(derived: Mapping[str, Any]) -> str:
    ga = derived.get("ga_days")
    bw = derived.get("birth_weight_g")
    if bw is not None and bw < 1500:
        return "极低出生体重儿"
    if ga is not None and ga < 259:
        return "早产儿"
    return "新生儿"


@lru_cache(maxsize=16)
def _load_chunks(guideline_id: str) -> tuple[dict[str, Any], ...]:
    path = _CHUNK_DIR / f"{guideline_id}_chunks.jsonl"
    if not path.is_file():
        return ()
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return tuple(rows)
