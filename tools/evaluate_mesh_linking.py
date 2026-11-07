"""Evaluate MeSH entity linking on real NLM data.

Two evaluations, both using official MeSH 2026 records (no synthetic labels):

1. **Held-out entry term**: for every descriptor with an entry term other than
   its preferred label, remove one entry term from the linker's index and use
   it as the query.  Gold is the descriptor UI.  This measures generalisation
   to an unseen official synonym, not memorisation of the index.
2. **Reviewed mapping audit**: for every row in the project's term-mapping
   library that has a grounded MeSH UI, verify that its English and MeSH
   products point to that same descriptor.

The optional semantic pass uses the real BGE-M3 encoder; without it the script
still runs the lexical pass and records ``semantic: not_configured``.

Usage (from the repository root)::

    python3 tools/evaluate_mesh_linking.py
    python3 tools/evaluate_mesh_linking.py --semantic   # needs optional dependencies + BGE-M3 cache
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.quality.mesh_linking import (  # noqa: E402
    BgeM3SemanticScorer,
    MeshDescriptor,
    MeshLexicon,
    MeshLinker,
    normalize_text,
)

DEFAULT_LEXICON = REPO_ROOT / "rag" / "demo" / "mesh_lexicon_neonatal_2026.json"
DEFAULT_LIBRARY = REPO_ROOT / "rag" / "contracts" / "examples" / "term_mapping_library_neonatal.json"
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "mesh_entity_linking_2026-11-01.json"


def held_out_benchmark(lexicon: MeshLexicon, *, sample_descriptors: int = 0, keep_full_index: bool = False) -> tuple[MeshLexicon, list[dict[str, str]]]:
    descriptors: list[MeshDescriptor] = []
    queries: list[dict[str, str]] = []
    all_descriptors = list(lexicon.descriptors)
    sampled = all_descriptors
    if sample_descriptors and sample_descriptors < len(all_descriptors):
        sampled = random.Random(20261101).sample(all_descriptors, sample_descriptors)
    sampled_uis = {descriptor.ui for descriptor in sampled}
    for descriptor in all_descriptors:
        if descriptor.ui not in sampled_uis:
            if keep_full_index:
                descriptors.append(descriptor)
            continue
        candidates = [
            term for term in descriptor.terms
            if term.text and normalize_text(term.text) != normalize_text(descriptor.label)
        ]
        if not candidates:
            descriptors.append(descriptor)
            continue
        held = candidates[sum(ord(char) for char in descriptor.ui) % len(candidates)]
        remaining = tuple(term for term in descriptor.terms if term.ui != held.ui)
        descriptors.append(replace(descriptor, terms=remaining))
        queries.append({"query": held.text, "gold_ui": descriptor.ui, "gold_label": descriptor.label})
    reduced = MeshLexicon(descriptors, source=lexicon.source, mesh_year=lexicon.mesh_year, retrieved_at=lexicon.retrieved_at)
    return reduced, queries


def run_benchmark(linker: MeshLinker, queries: Sequence[Mapping[str, str]], *, label: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    answered = correct = ambiguous = unlinked = wrong = skipped = 0
    for item in queries:
        result = linker.link(str(item["query"]))
        gold = str(item["gold_ui"])
        rank = next((index + 1 for index, candidate in enumerate(result.candidates) if candidate.ui == gold), None)
        if result.status == "linked":
            answered += 1
            if result.best and result.best.ui == gold:
                correct += 1
                reciprocal_ranks.append(1.0)
            else:
                wrong += 1
                reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        elif result.status == "ambiguous":
            ambiguous += 1
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        elif result.status == "skipped_no_english_surface":
            skipped += 1
            reciprocal_ranks.append(0.0)
        else:
            unlinked += 1
            reciprocal_ranks.append(0.0)
        rows.append({
            "query": item["query"],
            "gold_ui": gold,
            "gold_label": item["gold_label"],
            "status": result.status,
            "chosen_ui": result.best.ui if result.best else None,
            "chosen_label": result.best.label if result.best else None,
            "rank": rank,
            "candidates": [candidate.to_dict() for candidate in result.candidates[:5]],
            "reason": result.reason,
        })
    total = len(queries) or 1
    return {
        "config": label,
        "queries": len(queries),
        "top1_accuracy": round(correct / total, 4),
        "top1_accuracy_answered": round(correct / answered, 4) if answered else None,
        "coverage": round(answered / total, 4),
        "mrr_at_5": round(sum(1.0 / rank for row in rows if (rank := row.get("rank")) and rank <= 5) / total, 4),
        "ambiguous": ambiguous,
        "ambiguous_rate": round(ambiguous / total, 4),
        "unlinked": unlinked,
        "unlinked_rate": round(unlinked / total, 4),
        "wrong_descriptor": wrong,
        "wrong_descriptor_rate": round(wrong / total, 4),
        "skipped": skipped,
        "rows": rows,
    }


def mapping_audit(linker: MeshLinker, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        ground_ui = str(metadata.get("mesh_ui") or "").strip()
        if not ground_ui:
            continue
        result = linker.verify_mapping(
            str(row.get("concept") or ""),
            row.get("english") or (),
            row.get("mesh") or (),
        )
        status = str(result["status"])
        counts[status] = counts.get(status, 0) + 1
        results.append({
            "concept": result["concept"],
            "ground_ui": ground_ui,
            "status": status,
            "linked_uis": result["linked_uis"],
            "reason": result["reason"],
        })
    return {"rows": len(results), "status_counts": counts, "results": results}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--mapping-library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--semantic", action="store_true", help="Enable the real BGE-M3 semantic scorer")
    parser.add_argument("--max-queries", type=int, default=0, help="Optional cap for a quick run")
    parser.add_argument("--sample-descriptors", type=int, default=0, help="Deterministic descriptor sample (for a full-MeSH validation run)")
    parser.add_argument("--keep-full-index", action="store_true", help="Sample held-out queries but keep every descriptor in the linker index")
    args = parser.parse_args(argv)

    full = MeshLexicon.load(args.lexicon)
    reduced, queries = held_out_benchmark(
        full, sample_descriptors=args.sample_descriptors, keep_full_index=args.keep_full_index,
    )
    if args.max_queries:
        queries = queries[: args.max_queries]

    lexical = MeshLinker(reduced)
    benchmarks = [run_benchmark(lexical, queries, label="lexical")]
    semantic_status: dict[str, Any] = {"configured": False}
    if args.semantic:
        scorer = BgeM3SemanticScorer(reduced)
        semantic_status = {
            "configured": True,
            "name": scorer.name,
            "available": bool(scorer.available),
            "reason": None if scorer.available else scorer.unavailable_reason,
        }
        if scorer.available:
            benchmarks.append(run_benchmark(MeshLinker(reduced, semantic_scorer=scorer), queries, label="lexical+semantic"))

    audit = mapping_audit(MeshLinker(full), args.mapping_library)
    artifact = {
        "tool": "tools/evaluate_mesh_linking.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset": {
            "source": full.source,
            "mesh_year": full.mesh_year,
            "lexicon_descriptors": len(full.descriptors),
            "lexicon_terms": sum(len(descriptor.terms) for descriptor in full.descriptors),
            "sample_descriptors": args.sample_descriptors or len(full.descriptors),
            "sample_seed": 20261101,
            "index_scope": "full" if args.keep_full_index else "sampled",
            "benchmark": "held-out official entry term (one non-label term removed per descriptor)",
            "queries": len(queries),
        },
        "semantic": semantic_status,
        "benchmarks": benchmarks,
        "mapping_audit": audit,
        "limitations": [
            "The benchmark is English MeSH entry-term resolution, not Chinese clinical query linking.",
            "Queries are official NLM terms; held-out synonyms are removed from the index, but other official synonyms remain.",
            "The mapping audit checks whether English/MeSH products ground to the recorded MeSH UI; it does not prove clinical correctness of the Chinese concept.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    for benchmark in benchmarks:
        print(json.dumps({key: value for key, value in benchmark.items() if key != "rows"}, ensure_ascii=False, indent=2))
    print("mapping audit:", json.dumps(audit["status_counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
