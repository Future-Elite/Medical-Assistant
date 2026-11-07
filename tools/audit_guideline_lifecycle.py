"""Audit the neonatal guideline registry against real PubMed lifecycle records.

This tool reads the same 11 guideline metadata records used by the demo
registry, adds two controls (a retracted guideline and its retraction notice),
and queries PubMed E-utilities for ``CommentsCorrectionsList`` /
``PublicationTypeList``.  It writes a validation artifact; it never changes the
registry.

Usage (from the repository root)::

    python3 tools/audit_guideline_lifecycle.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.demo import load_demo_guidelines  # noqa: E402
from rag.sources.guideline_lifecycle import GuidelineLifecycleResolver  # noqa: E402
from rag.guidelines import neonatal_guideline_metadata_registry  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "指南生命周期审计_2026-11-01.json"
DEFAULT_CACHE = REPO_ROOT / "data" / "guideline_lifecycle_cache.json"
CONTROL_PMIDS = ("41110921", "41422828")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args(argv)

    entries = []
    seen: set[str] = set()

    synthetic_excluded: list[str] = []

    def add(document, *, scope: str) -> None:
        if not getattr(document, "pmid", None):
            return
        metadata = dict(getattr(document, "metadata", {}) or {})
        if metadata.get("synthetic") is True:
            synthetic_excluded.append(str(document.pmid))
            return
        pmid = str(document.pmid)
        if pmid in seen:
            return
        seen.add(pmid)
        entries.append({
            "document_id": document.document_id,
            "pmid": pmid,
            "title": document.title,
            "published_at": document.published_at,
            "version_id": document.version_id,
            "registry_status": document.status,
            "registry_scope": scope,
        })

    for document in neonatal_guideline_metadata_registry():
        add(document, scope="live_registry")
    demo_payload = load_demo_guidelines()
    for source in demo_payload.get("sources") or ():
        for document in source.get("documents") or ():
            add(type("DemoDocument", (), {
                "document_id": document.get("document_id"),
                "pmid": document.get("pmid"),
                "title": document.get("title"),
                "published_at": document.get("published_at"),
                "version_id": document.get("version_id"),
                "status": document.get("status"),
                "metadata": document.get("metadata") or {},
            })(), scope="demo_library")
    known = {item["pmid"] for item in entries}
    for pmid in CONTROL_PMIDS:
        if pmid not in known:
            entries.append({
                "document_id": f"control-{pmid}",
                "pmid": pmid,
                "title": "control",
                "published_at": None,
                "version_id": None,
                "registry_status": None,
                "registry_scope": "control",
            })

    resolver = GuidelineLifecycleResolver(mode="active", cache_path=str(args.cache), ttl_days=7.0)
    trace = resolver.resolve([item["pmid"] for item in entries], as_of=args.as_of)
    for item in entries:
        item["lifecycle"] = trace["statuses"].get(item["pmid"])

    summary = {
        "guideline_count": len(entries) - len(CONTROL_PMIDS),
        "control_count": len(CONTROL_PMIDS),
        "statuses": {},
        "event_kinds": {},
        "unresolved": trace["unresolved"],
        "synthetic_excluded": synthetic_excluded,
        "warnings": trace["warnings"],
    }
    for item in entries:
        status = (item.get("lifecycle") or {}).get("status", "unresolved")
        summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        events = ((item.get("lifecycle") or {}).get("applied_events") or []) + ((item.get("lifecycle") or {}).get("future_events") or [])
        for event in events:
            kind = str(event.get("kind") or "unknown")
            summary["event_kinds"][kind] = summary["event_kinds"].get(kind, 0) + 1

    artifact = {
        "tool": "tools/audit_guideline_lifecycle.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of": args.as_of,
        "source": "NCBI PubMed efetch (CommentsCorrectionsList + PublicationTypeList)",
        "cache_path": str(args.cache),
        "summary": summary,
        "entries": entries,
        "trace": trace,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for item in entries:
        status = (item.get("lifecycle") or {}).get("status", "unresolved")
        kinds = ",".join(sorted({str(event.get("kind")) for event in ((item.get("lifecycle") or {}).get("applied_events") or [])})) or "-"
        print(f"{item['pmid']:>10} {status:<12} {kinds:<25} {item['title'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
