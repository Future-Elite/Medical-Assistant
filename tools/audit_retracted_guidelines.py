"""Validate lifecycle parsing against real PubMed retracted practice guidelines.

This is a breadth check, not a registry audit: it searches PubMed for records
typed ``Retracted Publication`` + ``Practice Guideline``, fetches their real
lifecycle XML, and reports what the parser recovers.  The output is a validation
artifact; no guideline is added to the registry.

Usage (from the repository root)::

    python3 tools/audit_retracted_guidelines.py
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.sources.guideline_lifecycle import GuidelineLifecycleResolver  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "指南生命周期撤回指南样本_2026-11-01.json"
DEFAULT_CACHE = REPO_ROOT / "data" / "guideline_lifecycle_cache.json"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
QUERY = '"Retracted Publication"[pt] AND "Practice Guideline"[pt]'


def search_pmids(*, retmax: int = 50) -> list[str]:
    params = urllib.parse.urlencode({
        "db": "pubmed", "term": QUERY, "retmax": retmax, "retmode": "json",
    })
    request = urllib.request.Request(f"{ESEARCH}?{params}", headers={"User-Agent": "ChinaLLM-research/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [str(item) for item in payload.get("esearchresult", {}).get("idlist", [])]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--retmax", type=int, default=50)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args(argv)

    pmids = search_pmids(retmax=args.retmax)
    resolver = GuidelineLifecycleResolver(mode="active", cache_path=str(args.cache), ttl_days=7.0)
    trace = resolver.resolve(pmids, as_of=args.as_of)
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    for pmid in pmids:
        record = trace["records"].get(pmid)
        status = trace["statuses"].get(pmid)
        if record is None or status is None:
            rows.append({"pmid": pmid, "status": "unresolved"})
            status_counts["unresolved"] = status_counts.get("unresolved", 0) + 1
            continue
        rows.append({
            "pmid": pmid,
            "title": record.get("title"),
            "published_at": record.get("published_at"),
            "publication_types": record.get("publication_types"),
            "status": status.get("status"),
            "applied_events": status.get("applied_events"),
            "future_events": status.get("future_events"),
            "unknown_date_events": status.get("unknown_date_events"),
        })
        status_counts[status["status"]] = status_counts.get(status["status"], 0) + 1
        for event in status.get("applied_events") or ():
            kind = str(event.get("kind") or "unknown")
            event_counts[kind] = event_counts.get(kind, 0) + 1
    artifact = {
        "tool": "tools/audit_retracted_guidelines.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "query": QUERY,
        "as_of": args.as_of,
        "source": "NCBI PubMed esearch + efetch lifecycle XML",
        "pmids": pmids,
        "summary": {
            "requested": len(pmids),
            "resolved": len(trace["resolved"]),
            "status_counts": status_counts,
            "applied_event_kinds": event_counts,
            "unresolved": trace["unresolved"],
            "warnings": trace["warnings"],
        },
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps(artifact["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
