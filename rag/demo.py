"""Neonatal demo profile: synthetic cases + a caller-style guideline library.

The demo assets live in ``rag/demo/`` as plain JSON so they can be reviewed,
diffed and replaced without touching code.  This module only validates and
loads them; it never registers anything by itself.  Registration is an
explicit caller action (``POST /api/demo/guidelines/load`` or
``CustomSourceLibrary.register_payload``), which keeps the v6.6.3 invariant
"the server holds no hidden corpus" intact.

Normative schema and wording rules: ``rag/demo/README.md``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .custom_sources import CustomSourceLibrary
from .his import snapshot_from_dict
from .models import RetrievalRequest

DEMO_PROFILE = "neonatal_demo_v1"
DEMO_DIR = Path(__file__).resolve().parent / "demo"
CASES_FILE = "neonatal_cases.json"
GUIDELINES_FILE = "neonatal_guidelines.json"  # 演示资产文件名；内容含指南元数据、合成指南块与合成文献
#: 默认演示档案会自动并入 30 例 HIS 风格批量病例；显式传入 directory 时不合并，
#: 这样 `load_demo_cases(".../his_generated_30")` 仍只读那一份档案。
GENERATED_CASES_FILE = Path("his_generated_30") / CASES_FILE

# Fields of ``RetrievalRequest`` that a demo preset may set.  ``request_id``
# and ``patient_snapshot`` are workflow outputs, never preset inputs.
PRESET_FIELDS = frozenset({
    "question", "task", "query_language", "clinical_intent", "as_of",
    "patient_terms", "term_mapping", "english_terms", "mesh_terms", "user_terms", "facets",
    "filters", "source_kinds", "source_mode", "retrieval_method", "rerank",
    "fidelity_gate", "top_k",
})


def demo_directory(directory: str | Path | None = None) -> Path:
    return Path(directory) if directory is not None else DEMO_DIR


def _validate_case_set(cases: list[Any], seen: set[str]) -> None:
    for index, case in enumerate(cases):
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"cases[{index}] requires case_id")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if case.get("synthetic") is not True:
            raise ValueError(f"case {case_id} must be marked synthetic=true")
        snapshot = case.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError(f"case {case_id} requires a snapshot object")
        snapshot_from_dict({"patient_snapshot": dict(snapshot)})
        preset = case.get("preset")
        if not isinstance(preset, Mapping):
            raise ValueError(f"case {case_id} requires a preset object")
        unknown = sorted(set(preset) - PRESET_FIELDS)
        if unknown:
            raise ValueError(f"case {case_id} preset has unknown RetrievalRequest fields: {', '.join(unknown)}")
        # 全字段走真实请求解析，保证 GUI 演示输入与 RAG 输入完全一致。
        RetrievalRequest.from_dict({**preset, "patient_snapshot": dict(snapshot)})


def load_demo_cases(directory: str | Path | None = None) -> dict[str, Any]:
    """Load and validate every demo case; raises ValueError with the case id.

    With the default directory (``None``), the 30-case HIS-style batch in
    ``demo/his_generated_30/neonatal_cases.json`` is merged into the curated
    cases, so ``GET /api/demo/cases`` and the workbench dropdown see both.
    Passing an explicit directory reads only that directory.
    """
    base_dir = demo_directory(directory)
    payload = json.loads((base_dir / CASES_FILE).read_text(encoding="utf-8"))
    if payload.get("profile") != DEMO_PROFILE:
        raise ValueError(f"demo profile must be {DEMO_PROFILE}")
    if payload.get("synthetic") is not True:
        raise ValueError("demo profile must be marked synthetic=true")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("demo profile requires a non-empty 'cases' array")
    seen: set[str] = set()
    _validate_case_set(cases, seen)
    curated_count = len(cases)

    if directory is None:
        generated_count = 0
        generated_path = base_dir / GENERATED_CASES_FILE
        if generated_path.is_file():
            generated = json.loads(generated_path.read_text(encoding="utf-8"))
            if generated.get("profile") != DEMO_PROFILE:
                raise ValueError(f"generated demo profile must be {DEMO_PROFILE}")
            if generated.get("synthetic") is not True:
                raise ValueError("generated demo profile must be marked synthetic=true")
            generated_cases = generated.get("cases")
            if not isinstance(generated_cases, list) or not generated_cases:
                raise ValueError("generated demo profile requires a non-empty 'cases' array")
            try:
                _validate_case_set(generated_cases, seen)
            except ValueError as exc:
                raise ValueError(f"generated: {exc}") from exc
            cases.extend(generated_cases)
            generated_count = len(generated_cases)
        payload["case_sets"] = {"curated": curated_count, "generated_his": generated_count}
    return payload


def load_demo_guidelines(directory: str | Path | None = None) -> dict[str, Any]:
    """Load the caller-style demo source payload ({"sources": [...]}: guidelines + literature)."""
    payload = json.loads((demo_directory(directory) / GUIDELINES_FILE).read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("demo guideline library requires a non-empty 'sources' array")
    library = CustomSourceLibrary()
    for index, source in enumerate(sources):
        try:
            library.register_payload(source)
        except ValueError as exc:
            raise ValueError(f"sources[{index}]: {exc}") from exc
    return payload


def register_demo_guidelines(library: CustomSourceLibrary, directory: str | Path | None = None) -> list[dict[str, Any]]:
    """Register every demo source explicitly; returns audit events."""
    payload = load_demo_guidelines(directory)
    return [library.register_payload(source) for source in payload["sources"]]


def case_summaries(payload: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Case list for the workbench (no implicit execution).

    The full snapshot/preset are returned to the local workbench only; they are
    synthetic and carry no patient identifiers by construction.
    """
    payload = payload or load_demo_cases()
    return [
        {
            "case_id": case["case_id"],
            "label": case.get("label", ""),
            "synthetic": True,
            "snapshot": case["snapshot"],
            "preset": case["preset"],
        }
        for case in payload["cases"]
    ]


def guideline_summary(payload: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = payload or load_demo_guidelines()
    return [
        {
            "source_id": source["source_id"],
            "kinds": source.get("kinds", []),
            "description": source.get("description", ""),
            "version": source.get("version"),
            "document_count": len(source.get("documents") or []),
            "synthetic": all(bool((doc.get("metadata") or {}).get("synthetic", False)) for doc in source.get("documents") or []),
        }
        for source in payload["sources"]
    ]
