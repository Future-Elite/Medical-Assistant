#!/usr/bin/env python3
"""Live smoke check for the open-access guideline full-text connector.

The script queries Europe PMC at run time.  It records only document metadata,
section names, character counts and warnings; it never writes full guideline
text to disk.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.sources.guideline_fulltext import LiveGuidelineFullTextConnector  # noqa: E402
from rag.models import QueryPlan  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "指南实时全文接入_2026-10-18.json"

PROFILES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "rds": (
        "新生儿呼吸窘迫综合征 CPAP 肺表面活性物质 指南原文",
        ("respiratory distress syndrome", "CPAP", "pulmonary surfactant"),
        ("Respiratory Distress Syndrome, Newborn",),
    ),
    "pneumonia": (
        "新生儿感染性肺炎 诊断 治疗 指南原文",
        ("neonatal pneumonia", "chest radiograph", "C-reactive protein"),
        (),
    ),
    "jaundice": (
        "新生儿黄疸 光疗 指南原文",
        ("neonatal jaundice", "phototherapy", "hyperbilirubinemia"),
        (),
    ),
    "apnea": (
        "早产儿呼吸暂停 咖啡因 指南原文",
        ("apnea of prematurity", "caffeine citrate"),
        (),
    ),
    "bpd": (
        "支气管肺发育不良 经后年龄 指南原文",
        ("bronchopulmonary dysplasia", "postmenstrual age"),
        (),
    ),
}


def _plan(name: str) -> QueryPlan:
    question, terms, mesh = PROFILES[name]
    return QueryPlan(
        question,
        (question,),
        terms,
        ("guideline-fulltext-live",),
        {"source_kinds": ["guideline"]},
        (),
        english_terms=terms,
        mesh_terms=mesh,
    )


def _document_summary(document: Any) -> dict[str, Any]:
    metadata = document.metadata
    return {
        "document_id": document.document_id,
        "title": document.title,
        "content_mode": metadata.get("content_mode"),
        "full_text_allowed": metadata.get("full_text_allowed"),
        "rights_basis": metadata.get("rights_basis"),
        "license": metadata.get("license"),
        "section": metadata.get("section"),
        "chars": len(document.text),
        "pmid": metadata.get("pmid") or document.pmid,
        "doi": metadata.get("doi") or document.doi,
        "url": document.url,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", nargs="+", choices=sorted(PROFILES), default=["rds", "pneumonia", "jaundice"])
    parser.add_argument("--max-articles", type=int, default=8)
    parser.add_argument("--max-chunks", type=int, default=16)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    connector = LiveGuidelineFullTextConnector(
        max_articles=args.max_articles,
        max_chunks_per_article=args.max_chunks,
    )
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Europe PMC REST (live)",
        "note": "Only metadata/section counts are stored; full guideline text is not persisted.",
        "queries": {},
    }
    for name in args.queries:
        search = connector.search(_plan(name), limit=max(20, args.max_chunks * 2))
        documents = [_document_summary(document) for document in search.documents]
        result["queries"][name] = {
            "query": connector.last_query,
            "document_count": len(documents),
            "full_text_chunks": sum(1 for item in documents if item["content_mode"] == "full_text"),
            "open_access_documents": sum(1 for item in documents if item["full_text_allowed"]),
            "documents": documents,
            "warnings": list(search.warnings),
        }
        print(f"{name}: {len(documents)} documents, "
              f"{result['queries'][name]['full_text_chunks']} full-text chunks, "
              f"{len(search.warnings)} warnings")
        for item in documents[:4]:
            print(f"  {item['document_id']} | {item['content_mode']} | "
                  f"allowed={item['full_text_allowed']} | {item['title'][:100]}")
        for warning in search.warnings[:3]:
            print(f"  warn: {warning[:180]}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"result: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
