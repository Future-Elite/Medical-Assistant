"""Freeze-check the default retrieval path between two module trees.

The new mechanisms in v6.7.1 are opt-in.  This tool runs the same offline
fixture scenarios against v6.7.0 and v6.7.1 with default configuration, removes
run-specific identifiers/timestamps, and compares the complete observable
output (query plan, ranked documents, warnings, trace).

Usage (from the repository root)::

    python3 tools/run_compatibility_diff.py --baseline <v6.7.0 tree> --current <v6.7.1 tree>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "validation" / "兼容性差分_v6.7.0_v6.7.1_2026-11-01.json"

PROBE = r'''
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from rag.models import RetrievalRequest
from rag.pipeline import EvidenceRetrievalPipeline
from rag.tests.helpers import fixture_pipeline

SCENARIOS = [
    {"question": "新生儿呼吸窘迫综合征治疗指南推荐什么？", "as_of": "2026-04-15", "user_terms": {"新生儿呼吸窘迫综合征": ["respiratory distress syndrome"]}},
    {"question": "新生儿感染性肺炎如何诊断？", "as_of": "2026-09-12", "method": "bm25"},
    {"question": "早产儿高胆红素血症光疗指征", "as_of": "2026-07-18", "method": "ql"},
    {"question": "NRDS 患儿使用肺表面活性物质的证据", "as_of": "2026-04-15", "method": "bm25"},
    {"question": "新生儿低血糖筛查", "as_of": "2026-09-12", "method": "rm3"},
    {"question": "支气管肺发育不良定义", "as_of": "2026-08-20", "method": "bm25"},
]


def normalize(value):
    if isinstance(value, str):
        return re.sub(r"retrieval-[0-9a-f]{32}", "retrieval-ID", value)
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items() if key not in {"rag_version", "retrieved_at"}}
    return value


def main(path):
    rows = []
    for scenario in SCENARIOS:
        request = RetrievalRequest(
            request_id="diff",
            question=scenario["question"],
            as_of=scenario["as_of"],
            user_terms=scenario.get("user_terms") or {},
            retrieval_method=scenario.get("method"),
        )
        package = EvidencePipeline().retrieve(request)
        payload = {
            "question": scenario["question"],
            "query_plan": asdict(package.query_plan),
            "ranked": [
                {
                    "document": asdict(item.document),
                    "score": item.score,
                    "lexical_score": item.lexical_score,
                    "fusion_score": item.fusion_score,
                    "evidence_status": item.evidence_status.value,
                    "rank_explanation": list(item.rank_explanation),
                    "retriever_id": item.retriever_id,
                }
                for item in package.ranked_documents
            ],
            "warnings": list(package.warnings),
            "trace": package.trace,
        }
        rows.append(normalize(payload))
    Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def EvidencePipeline():
    import rag
    kwargs = {}
    if getattr(rag, "__version__", "") >= "6.7.1":
        kwargs.update(entity_linker=None, entity_linking_mode="off", guideline_lifecycle=None, guideline_lifecycle_mode="off")
    return fixture_pipeline(**kwargs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
'''


def _run_probe(tree: Path, output: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(PROBE)
        probe_path = handle.name
    try:
        subprocess.run(
            [sys.executable, probe_path, str(output)],
            cwd=str(tree),
            check=True,
            env={"PYTHONPATH": str(tree), "PATH": "/usr/bin:/bin"},
        )
    finally:
        Path(probe_path).unlink(missing_ok=True)


def _diff_paths(left, right, prefix="") -> list[str]:
    differences: list[str] = []
    if type(left) is not type(right):
        return [f"{prefix}: type {type(left).__name__} != {type(right).__name__}"]
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                differences.append(f"{prefix}.{key}: missing in baseline")
            elif key not in right:
                differences.append(f"{prefix}.{key}: missing in current")
            else:
                differences.extend(_diff_paths(left[key], right[key], f"{prefix}.{key}"))
        return differences
    if isinstance(left, list):
        if len(left) != len(right):
            differences.append(f"{prefix}: length {len(left)} != {len(right)}")
        for index, (a, b) in enumerate(zip(left, right)):
            differences.extend(_diff_paths(a, b, f"{prefix}[{index}]"))
        return differences
    if left != right:
        differences.append(f"{prefix}: {left!r} != {right!r}")
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="baseline module tree")
    parser.add_argument("--current", type=Path, required=True, help="current module tree")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as directory:
        baseline_path = Path(directory) / "baseline.json"
        current_path = Path(directory) / "current.json"
        _run_probe(args.baseline, baseline_path)
        _run_probe(args.current, current_path)
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        current = json.loads(current_path.read_text(encoding="utf-8"))
    differences = _diff_paths(baseline, current)
    artifact = {
        "tool": "tools/run_compatibility_diff.py",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "baseline": str(args.baseline),
        "current": str(args.current),
        "scenarios": len(baseline),
        "configuration": "legacy path: entity_linking_mode=off, guideline_lifecycle_mode=off, no claim verifier injected (v6.7.1 server defaults are active; use /api/settings/industrial)",
        "compared_fields": "query_plan, ranked documents (including document payload and scores), warnings, trace (excluding rag_version/retrieved_at; retrieval IDs normalized)",
        "identical": not differences,
        "difference_count": len(differences),
        "differences": differences[:200],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(f"identical={artifact['identical']} differences={artifact['difference_count']}")
    for difference in differences[:20]:
        print("  " + difference)
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
