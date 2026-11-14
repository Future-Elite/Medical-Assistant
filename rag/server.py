"""Local read-only HTTP workbench for the RAG package.

Source model (exactly two **retrieval** modes):

- ``live``   — real-time PubMed (NCBI E-utilities) + **neonatal** guideline
               metadata registry (``live`` requires both live switches enabled
               in ``config.py``);
- ``custom`` — caller-provided knowledge libraries registered at runtime via
               ``POST /api/sources/custom`` (payload schema in
               ``custom_sources.py`` and ``contracts/README.md``).

The neonatal demo profile (``data/``, next to the ``rag`` package) is **not** a
third mode: it is a caller-style library that the workbench registers into
``custom`` only after an explicit ``POST /api/demo/guidelines/load`` (or
``config.demo_autoload_guidelines=True`` at startup).  The server still holds
no hidden corpus and never falls back to synthetic data.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag import __version__
from rag.agent import EvidenceRAG
from rag.audit import GroundedAnswerAuditor
from rag.quality.claim_verification import build_claim_verifier
from rag.config import get_config
from rag.connectors import ConnectorRegistry
from rag.retrieval.controller import ControllerPolicy, RetrievalController
from rag.custom_sources import CustomSourceLibrary, SOURCE_MODES, load_custom_library
from rag.sources.guideline_chunks import load_guideline_chunk_library
from rag.sources.guideline_source import GuidelineParsingError, GuidelineParsingSource
from rag.demo import (
    load_applicability_cases,
    load_demo_cases,
    load_demo_guidelines,
    register_demo_guidelines,
    guideline_summary as demo_guideline_summary,
)
from rag.sources.guideline_lifecycle import GUIDELINE_LIFECYCLE_MODES, load_lifecycle_resolver
from rag.guidelines import (
    NEONATAL_GUIDELINE_SOURCE_ID,
    GuidelineMetadataConnector,
    describe_registry as describe_guideline_registry,
    neonatal_guideline_metadata_registry,
)
from rag.sources.guideline_fulltext import GuidelineFullTextError, LiveGuidelineFullTextConnector, archive_live_guideline
from rag.sources.guideline_pdf import GuidelinePdfError, LocalGuidelinePdfConnector
from rag.his import PatientSnapshot
from rag.quality.mesh_linking import MESH_LINKING_MODES, load_linker as load_mesh_linker
from rag.quality.metrics import METRICS
from rag.models import RetrievalRequest
from rag.neural_adapters import MedCPTReranker, Qwen3Reranker, build_neural_registry, cache_dir_hint, neural_dependencies_state
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector

ROOT = Path(__file__).resolve().parent
#: React/Vite 工作台构建产物（``web/dist``），由 ``/`` 与 ``/assets/*`` 直接托管。
WEB_DIST = ROOT / "web" / "dist"
MAX_BODY_BYTES = 1_000_000
# HIS demos receive an explicit snapshot payload with the request; the server
# holds no built-in patient data.
HIS_MODE = "caller_supplied_snapshot"
RERANKER_MODELS = ("medcpt", "qwen3")
#: Workbench 的即时重排配置（进程内覆盖 env/config；重启后回到 env/config）。
_RERANKER_RUNTIME: dict[str, object] = {"model": None, "rerank_pool": None}
_INDUSTRIAL_RUNTIME: dict[str, object] = {
    "entity_linking_mode": None,
    "guideline_lifecycle_mode": None,
    "claim_verifier_model": None,
    "entity_linking_semantic": None,
}


def _normalize_reranker_model(value: object) -> str:
    choice = str(value or "").strip().lower().replace("_", "-")
    if choice in {"qwen3-reranker", "qwen3-reranker-0.6b"}:
        choice = "qwen3"
    if choice not in RERANKER_MODELS:
        raise ValueError("reranker_model must be one of: " + ", ".join(RERANKER_MODELS))
    return choice


def _reranker_model() -> str:
    """当前生效的重排器：工作台运行时覆盖 > env > config > medcpt。"""
    raw = _RERANKER_RUNTIME["model"] or os.environ.get("RAG_RERANKER_MODEL") or get_config().reranker_model or "medcpt"
    return _normalize_reranker_model(raw)


def _set_reranker_settings(payload: dict[str, object]) -> dict[str, object]:
    """更新进程内重排配置；只校验，不加载模型。"""
    model = payload.get("model")
    pool = payload.get("rerank_pool")
    if model is None and pool is None:
        raise ValueError("reranker settings require 'model' and/or 'rerank_pool'")
    if model is not None:
        _RERANKER_RUNTIME["model"] = _normalize_reranker_model(model)
    if pool is not None:
        if isinstance(pool, bool):
            raise ValueError("rerank_pool must be an integer >= 0")
        try:
            value = int(pool)
        except (TypeError, ValueError):
            raise ValueError("rerank_pool must be an integer >= 0") from None
        if value < 0:
            raise ValueError("rerank_pool must be an integer >= 0")
        _RERANKER_RUNTIME["rerank_pool"] = value
    return _reranker_status()


def _reranker_status() -> dict[str, object]:
    return {
        "model": _reranker_model(),
        "rerank_pool": _rerank_pool(),
        "available_models": list(RERANKER_MODELS),
        "runtime_override": bool(_RERANKER_RUNTIME["model"] or _RERANKER_RUNTIME["rerank_pool"] is not None),
    }


def _neural_parts():
    """构建神经适配器（懒加载）与重排器；构造本身不加载模型。

    重排器按当前配置选择：medcpt（MedCPT cross-encoder）|
    qwen3（Qwen3-Reranker-0.6B，本地权重、多语言）。未知值显式报错。
    """
    neural = build_neural_registry(cache_dir=cache_dir_hint())
    neural.pop("qwen3_reranker", None)
    default_reranker = neural.pop("cross_encoder", None) or MedCPTReranker(cache_dir=cache_dir_hint())
    choice = _reranker_model()
    if choice == "medcpt":
        reranker = default_reranker
    else:
        reranker = Qwen3Reranker(cache_dir=cache_dir_hint())
    return neural, reranker


def _pubmed_max_results() -> int:
    """PubMed 默认返回条数：env > config，默认 200。"""
    raw = os.environ.get("RAG_PUBMED_MAX_RESULTS", "").strip()
    try:
        value = int(raw) if raw else int(get_config().pubmed_max_results)
    except ValueError:
        raise ValueError("RAG_PUBMED_MAX_RESULTS must be an integer between 1 and 10000") from None
    if not 1 <= value <= 10000:
        raise ValueError("RAG_PUBMED_MAX_RESULTS must be an integer between 1 and 10000")
    return value


def build_live_registry() -> ConnectorRegistry:
    config = get_config()
    if not (config.allow_live_pubmed and config.allow_live_guidelines):
        raise ValueError("live PubMed and guideline sources are not both enabled")
    registry = ConnectorRegistry()
    registry.register(PubMedConnector(max_results=_pubmed_max_results()))
    registry.register(GuidelineMetadataConnector(NEONATAL_GUIDELINE_SOURCE_ID, neonatal_guideline_metadata_registry()))
    if config.allow_live_guideline_fulltext:
        registry.register(LiveGuidelineFullTextConnector())
    pdf_connector = build_pdf_connector()
    if pdf_connector is not None:
        registry.register(pdf_connector)
    return registry


def build_pdf_connector() -> LocalGuidelinePdfConnector | None:
    """Caller-supplied guideline PDFs (outside the repo); absent when unset.

    A missing directory or manifest is not an error for ``live`` mode: the
    other guideline sources still work, and ``GET /api/sources`` reports why
    this one is absent.
    """
    config = get_config()
    directory = Path(str(config.guideline_pdf_dir or "")).expanduser()
    if not str(config.guideline_pdf_dir or "").strip():
        return None
    try:
        return LocalGuidelinePdfConnector(directory=directory)
    except (ValueError, OSError):
        return None


def _guideline_chunks_path() -> str:
    """外部指南工作台分块路径（环境变量优先于 config）。"""
    return str(
        os.environ.get("RAG_GUIDELINE_CHUNKS") or get_config().guideline_chunks_path or ""
    ).strip()


def _guideline_source() -> GuidelineParsingSource | None:
    """本地指南解析来源（RAG_GUIDELINE_DATA 或默认 data/guideline_parsing 启用）。"""
    # 显式 chunks 来源优先于默认数据目录；否则 data/guideline_parsing 会
    # 静默忽略调用方的 RAG_GUIDELINE_CHUNKS。
    if (os.environ.get("RAG_GUIDELINE_CHUNKS") or get_config().guideline_chunks_path) and not (
        os.environ.get("RAG_GUIDELINE_DATA") or ""
    ).strip():
        return None
    data_dir = str(os.environ.get("RAG_GUIDELINE_DATA") or get_config().guideline_data_dir or "").strip()
    if not data_dir:
        return None
    mineru_bin = str(
        os.environ.get("RAG_MINERU_BIN") or get_config().guideline_mineru_bin or ""
    ).strip()
    return GuidelineParsingSource(data_dir=data_dir, mineru_bin=mineru_bin)


def _guideline_auto_parse() -> bool:
    raw = os.environ.get("RAG_GUIDELINE_AUTO_PARSE")
    if raw is None:
        return bool(get_config().guideline_auto_parse)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _guideline_status() -> dict[str, object]:
    data = str(os.environ.get("RAG_GUIDELINE_DATA") or get_config().guideline_data_dir or "").strip()
    mineru_bin = str(
        os.environ.get("RAG_MINERU_BIN") or get_config().guideline_mineru_bin or ""
    ).strip()
    return {
        "configured": bool(data),
        "data": data,
        "auto_parse": _guideline_auto_parse(),
        "mineru_bin": mineru_bin,
    }


def _require_guideline_source() -> GuidelineParsingSource:
    source = _guideline_source()
    if source is None:
        raise ValueError(
            "未配置指南数据目录：设置 RAG_GUIDELINE_DATA 或 config.guideline_data_dir"
        )
    return source


def _live_archive_id(payload: Mapping[str, Any]) -> str:
    base = str(payload.get("pmid") or payload.get("pmcid") or payload.get("doi") or payload.get("title") or "unknown").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")[:80]
    if not safe:
        safe = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"live-{safe}"


def _save_live_guideline(source: GuidelineParsingSource, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Save one live clinical-practice guideline and parse it with the local parser."""
    evidence_type = str(payload.get("evidence_type") or "clinical_practice_guideline").strip()
    if evidence_type != "clinical_practice_guideline":
        raise ValueError("只保存临床实践指南；综述/替代证据不能写入指南库")
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("需要 title")
    archive = archive_live_guideline(payload)
    if archive["content_mode"] == "metadata_only":
        raise ValueError("没有可保存的全文或摘要；未写入指南库")
    document_id = _live_archive_id(payload)
    file_name = f"{document_id}.md"
    source.guidelines_dir.mkdir(parents=True, exist_ok=True)
    (source.guidelines_dir / file_name).write_text(archive["markdown"], encoding="utf-8")

    manifest_path = source.guidelines_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("guidelines/manifest.json 不是有效 JSON") from exc
    else:
        manifest = {"profile": "caller_supplied_guideline_pdfs_v1", "guidelines": []}
    if not isinstance(manifest, Mapping):
        raise ValueError("guidelines/manifest.json 必须是对象")
    rows = manifest.get("guidelines")
    if not isinstance(rows, list):
        raise ValueError("guidelines/manifest.json 缺少 guidelines 数组")

    pmid = str(payload.get("pmid") or "").strip()
    pmcid = str(payload.get("pmcid") or "").strip()
    rows = [
        row for row in rows
        if not isinstance(row, Mapping)
        or (
            str(row.get("document_id") or "") != document_id
            and (not pmid or str(row.get("pmid") or "") != pmid)
            and (not pmcid or str(row.get("pmcid") or "") != pmcid)
        )
    ]
    suffix = "（实时保存）" if archive["content_mode"] == "full_text" else "（实时保存·仅摘要）"
    rows.append({
        "document_id": document_id,
        "file": file_name,
        "title": f"{title}{suffix}",
        "version_id": str(payload.get("version_id") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "source_url": str(payload.get("source_url") or payload.get("url") or ""),
        "doi": str(payload.get("doi") or ""),
        "pmid": pmid,
        "pmcid": pmcid,
        "license": archive["license"],
        "rights_basis": "open_access" if archive["content_mode"] == "full_text" else "not_open_access_or_unverified",
        "content_mode": archive["content_mode"],
        "evidence_type": evidence_type,
        "specialty": "新生儿",
        "saved_from_live": True,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    manifest["guidelines"] = rows
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    parser = GuidelineParsingSource(data_dir=source.data_dir, mineru_bin=source.mineru_bin)
    results = parser.parse(document_ids=[document_id])
    return {
        "status": "ok",
        "document_id": document_id,
        "title": f"{title}{suffix}",
        "content_mode": archive["content_mode"],
        "license": archive["license"],
        "warnings": archive["warnings"],
        "parse": results,
        "guidelines": parser.list_guidelines(),
    }


def _guideline_pdf(source: GuidelineParsingSource, document_id: str) -> tuple[Path, dict[str, Any]]:
    row = next(
        (item for item in source.list_guidelines() if item["document_id"] == document_id),
        None,
    )
    if row is None:
        raise GuidelineParsingError(f"未知指南：{document_id}")
    root = source.guidelines_dir.resolve()
    candidate = Path(os.path.normpath(str(root / str(row.get("file") or ""))))
    if candidate != root and root not in candidate.parents:
        raise GuidelineParsingError(f"指南文件不在数据目录内：{document_id}")
    path = candidate.resolve()
    if not path.is_file():
        raise GuidelineParsingError(f"指南 PDF 不存在：{path}")
    if path.suffix.lower() != ".pdf":
        raise GuidelineParsingError(f"该指南不是 PDF（当前只支持 PDF 页预览）：{path.name}")
    return path, row


def _pdf_page_count(path: Path) -> int:
    try:
        completed = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuidelineParsingError(f"pdfinfo 不可用：{type(exc).__name__}") from exc
    if completed.returncode:
        raise GuidelineParsingError((completed.stderr or completed.stdout).strip()[-500:])
    match = re.search(r"^Pages:\s*(\d+)", completed.stdout, re.MULTILINE)
    if not match:
        raise GuidelineParsingError("无法读取 PDF 页数")
    return int(match.group(1))


def _render_pdf_page(path: Path, page: int, dpi: int = 110) -> bytes:
    with tempfile.TemporaryDirectory(prefix="chinallm_pdf_") as directory:
        prefix = Path(directory) / "page"
        command = [
            "pdftoppm", "-png", "-r", str(dpi),
            "-f", str(page), "-l", str(page), "-singlefile",
            str(path), str(prefix),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GuidelineParsingError(f"pdftoppm 不可用：{type(exc).__name__}") from exc
        if completed.returncode:
            raise GuidelineParsingError((completed.stderr or completed.stdout).strip()[-500:])
        output = prefix.with_suffix(".png")
        if not output.is_file():
            raise GuidelineParsingError("PDF 页面渲染失败：未生成 PNG")
        return output.read_bytes()


def _guideline_parse_options() -> dict[str, object]:
    return {
        "chunk_strategy": "recursive",
        "table_strategy": "markdown",
        "figure_strategy": "reference",
        "chunk_size": 800,
        "overlap": 120,
        "tier": "advanced",
    }


def _parse_options_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    options = _guideline_parse_options()
    for key in ("chunk_strategy", "table_strategy", "figure_strategy", "tier"):
        if payload.get(key) is not None:
            options[key] = str(payload[key]).strip()
    for key in ("chunk_size", "overlap"):
        if payload.get(key) is not None:
            options[key] = int(payload[key])
    return options


def create_tool(source_mode: str = "live", *, custom_library: CustomSourceLibrary | None = None) -> EvidenceRAG:
    """按来源模式构建工具。

    ``live``   实时 PubMed + 新生儿指南元数据 + 开放获取指南全文；
    ``custom`` 调用方通过 ``CustomSourceLibrary`` 注册的知识库；指南来源优先
               使用 ``RAG_GUIDELINE_DATA``（直接调用本地 guideline_parsing 解析器
               并加载其目录布局），否则使用 ``RAG_GUIDELINE_CHUNKS`` 预解析分块；
               三者都未配置时空库显式报错。
    """
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"unknown source_mode: {source_mode}（可用：{', '.join(SOURCE_MODES)}）")
    if source_mode == "live":
        registry = build_live_registry()
    else:
        library = custom_library or CustomSourceLibrary()
        guideline_source = _guideline_source()
        chunks_path = _guideline_chunks_path()
        registry = ConnectorRegistry()
        if len(library):
            registry.extend(library.build_registry()[0])
        elif guideline_source is None and not chunks_path:
            # Preserve the explicit “empty custom library” error.
            registry, _library_warnings = library.build_registry()
        if guideline_source is not None:
            if chunks_path:
                registry.warnings = (
                    *registry.warnings,
                    "RAG_GUIDELINE_DATA 已配置；忽略 RAG_GUIDELINE_CHUNKS（指南来源以本地解析器为准）。",
                )
            guideline_library = guideline_source.load_library(parse=_guideline_auto_parse())
            guideline_registry, _guideline_warnings = guideline_library.build_registry()
            registry.extend(guideline_registry)
        elif chunks_path:
            chunk_library = load_guideline_chunk_library(chunks_path)
            chunk_registry, _chunk_warnings = chunk_library.build_registry()
            registry.extend(chunk_registry)
        # 构建期警告随每次检索结果返回（见 ConnectorRegistry.warnings），此处不重复打印。
    neural, reranker = _neural_parts()
    lexicon, policy, penalty = _fidelity_parts()
    linking_mode, linker, linking_error = _entity_linking_parts()
    if linking_mode != "off" and linker is None:
        raise ValueError(f"实体链接已启用但未就绪：{linking_error}")
    lifecycle_mode, lifecycle_resolver, lifecycle_error = _guideline_lifecycle_parts()
    if lifecycle_mode != "off" and lifecycle_resolver is None:
        raise ValueError(f"指南生命周期治理已启用但未就绪：{lifecycle_error}")
    verifier = _claim_verifier_parts()
    pipeline = EvidenceRetrievalPipeline(
        registry, neural_retrievers=neural, reranker=reranker,
        fidelity_lexicon=lexicon, fidelity_default_policy=policy, fidelity_penalty=penalty,
        rerank_pool=_rerank_pool(),
        term_mapping_library=_term_mapping_library(),
        applicability_preview=_applicability_preview(),
        routing_mode=_routing_mode(),
        entity_linker=linker,
        entity_linking_mode=linking_mode,
        entity_linking_max_entry_terms=int(os.environ.get("RAG_ENTITY_LINKING_MAX_ENTRY_TERMS") or get_config().entity_linking_max_entry_terms),
        guideline_lifecycle=lifecycle_resolver,
        guideline_lifecycle_mode=lifecycle_mode,
    )
    return EvidenceRAG(pipeline, auditor=GroundedAnswerAuditor(verifier=verifier) if verifier is not None else None)


def _entity_linking_mode_setting() -> str:
    raw = str(_INDUSTRIAL_RUNTIME["entity_linking_mode"] or os.environ.get("RAG_ENTITY_LINKING_MODE") or get_config().entity_linking_mode or "active").strip().lower()
    if raw not in MESH_LINKING_MODES:
        raise ValueError("RAG_ENTITY_LINKING_MODE must be one of: " + ", ".join(MESH_LINKING_MODES))
    return raw


def _entity_linking_semantic_setting() -> bool:
    override = _INDUSTRIAL_RUNTIME["entity_linking_semantic"]
    if override is not None:
        return bool(override)
    raw = os.environ.get("RAG_ENTITY_LINKING_SEMANTIC")
    return bool(get_config().entity_linking_semantic) if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _guideline_lifecycle_mode_setting() -> str:
    raw = str(_INDUSTRIAL_RUNTIME["guideline_lifecycle_mode"] or os.environ.get("RAG_GUIDELINE_LIFECYCLE_MODE") or get_config().guideline_lifecycle_mode or "active").strip().lower()
    if raw not in GUIDELINE_LIFECYCLE_MODES:
        raise ValueError("RAG_GUIDELINE_LIFECYCLE_MODE must be one of: " + ", ".join(GUIDELINE_LIFECYCLE_MODES))
    return raw


def _claim_verifier_model_setting() -> str:
    raw = _INDUSTRIAL_RUNTIME["claim_verifier_model"]
    if raw is None:
        raw = os.environ.get("RAG_MINICHECK_MODEL")
    if raw is None:
        raw = get_config().minicheck_model
    model = str(raw or "").strip()
    if model.lower() in {"", "off", "none", "disabled"}:
        return ""
    canonical = {
        "flan-t5-large": "flan-t5-large",
        "roberta-large": "roberta-large",
        "deberta-v3-large": "deberta-v3-large",
        "lytang/MiniCheck-Flan-T5-Large": "flan-t5-large",
        "lytang/MiniCheck-RoBERTa-Large": "roberta-large",
        "lytang/MiniCheck-DeBERTa-v3-Large": "deberta-v3-large",
    }.get(model, model)
    if canonical not in {"flan-t5-large", "roberta-large", "deberta-v3-large", "Bespoke-MiniCheck-7B", "Granite-Guardian-3.3-8B"}:
        raise ValueError("claim_verifier must be off / flan-t5-large / roberta-large / deberta-v3-large")
    return canonical


def _set_industrial_settings(payload: dict[str, object]) -> dict[str, object]:
    """更新进程内实体链接/生命周期/主张校验配置；只校验，不加载模型。"""
    if not any(key in payload for key in ("entity_linking_mode", "guideline_lifecycle_mode", "claim_verifier", "entity_linking_semantic")):
        raise ValueError("industrial settings require entity_linking_mode / guideline_lifecycle_mode / claim_verifier / entity_linking_semantic")
    if "entity_linking_mode" in payload:
        mode = str(payload.get("entity_linking_mode") or "").strip().lower()
        if mode not in MESH_LINKING_MODES:
            raise ValueError("entity_linking_mode must be one of: " + ", ".join(MESH_LINKING_MODES))
        _INDUSTRIAL_RUNTIME["entity_linking_mode"] = mode
    if "guideline_lifecycle_mode" in payload:
        mode = str(payload.get("guideline_lifecycle_mode") or "").strip().lower()
        if mode not in GUIDELINE_LIFECYCLE_MODES:
            raise ValueError("guideline_lifecycle_mode must be one of: " + ", ".join(GUIDELINE_LIFECYCLE_MODES))
        _INDUSTRIAL_RUNTIME["guideline_lifecycle_mode"] = mode
    if "claim_verifier" in payload:
        value = str(payload.get("claim_verifier") or "").strip()
        if value.lower() in {"", "off", "none", "disabled"}:
            _INDUSTRIAL_RUNTIME["claim_verifier_model"] = ""
        else:
            _INDUSTRIAL_RUNTIME["claim_verifier_model"] = _claim_verifier_model_setting_for_value(value)
    if "entity_linking_semantic" in payload:
        value = payload.get("entity_linking_semantic")
        if isinstance(value, bool):
            _INDUSTRIAL_RUNTIME["entity_linking_semantic"] = value
        else:
            _INDUSTRIAL_RUNTIME["entity_linking_semantic"] = str(value or "").strip().lower() in {"1", "true", "yes", "on"}
    return _industrial_status()


def _claim_verifier_model_setting_for_value(value: object) -> str:
    model = str(value or "").strip()
    canonical = {
        "flan-t5-large": "flan-t5-large",
        "roberta-large": "roberta-large",
        "deberta-v3-large": "deberta-v3-large",
        "lytang/MiniCheck-Flan-T5-Large": "flan-t5-large",
        "lytang/MiniCheck-RoBERTa-Large": "roberta-large",
        "lytang/MiniCheck-DeBERTa-v3-Large": "deberta-v3-large",
    }.get(model, model)
    if canonical not in {"flan-t5-large", "roberta-large", "deberta-v3-large", "Bespoke-MiniCheck-7B", "Granite-Guardian-3.3-8B"}:
        raise ValueError("claim_verifier must be off / flan-t5-large / roberta-large / deberta-v3-large")
    return canonical


def _industrial_status() -> dict[str, object]:
    return {
        "entity_linking": _entity_linking_status(),
        "guideline_lifecycle": _guideline_lifecycle_status(),
        "claim_verifier": _claim_verifier_status(),
        "runtime_override": any(value is not None for value in _INDUSTRIAL_RUNTIME.values()),
    }


_ENTITY_LINKER_CACHE: dict[str, object] = {"key": None, "linker": None, "error": None}


def _entity_linking_parts() -> tuple[str, object | None, str | None]:
    """Resolve mode + local MeSH linker from config/env.

    ``off`` never loads a lexicon.  A non-off mode without a lexicon is an
    explicit configuration error (no silent fallback to lexical heuristics).
    """
    config = get_config()
    mode = _entity_linking_mode_setting()
    path = str(os.environ.get("RAG_MESH_LEXICON") or config.mesh_lexicon_path or "").strip()
    semantic = _entity_linking_semantic_setting()
    surfaces = str(os.environ.get("RAG_ENTITY_LINKING_SURFACES") or config.entity_linking_semantic_surfaces or "all").strip().lower()
    max_token_candidates = int(os.environ.get("RAG_ENTITY_LINKING_MAX_TOKEN_CANDIDATES") or config.entity_linking_max_token_candidates)
    if surfaces not in {"all", "label"}:
        raise ValueError("RAG_ENTITY_LINKING_SURFACES must be all or label")
    if mode == "off":
        return mode, None, None
    if not path:
        return mode, None, "entity_linking_mode 非 off，但 mesh_lexicon_path 为空。"
    key = f"{path}|{semantic}|{surfaces}|{max_token_candidates}"
    if _ENTITY_LINKER_CACHE.get("key") == key:
        return mode, _ENTITY_LINKER_CACHE.get("linker"), _ENTITY_LINKER_CACHE.get("error")
    try:
        linker = load_mesh_linker(
            path, semantic=semantic, semantic_surface_mode=surfaces,
            max_token_candidates=max_token_candidates,
        )
    except Exception as exc:
        _ENTITY_LINKER_CACHE.update({"key": key, "linker": None, "error": f"{type(exc).__name__}: {exc}"})
        return mode, None, _ENTITY_LINKER_CACHE["error"]
    _ENTITY_LINKER_CACHE.update({"key": key, "linker": linker, "error": None})
    return mode, linker, None


def _entity_linking_status() -> dict[str, object]:
    config = get_config()
    mode = _entity_linking_mode_setting()
    path = str(os.environ.get("RAG_MESH_LEXICON") or config.mesh_lexicon_path or "").strip()
    resolved_mode, linker, error = _entity_linking_parts() if mode != "off" else (mode, None, None)
    status: dict[str, object] = {
        "mode": resolved_mode,
        "configured": bool(path),
        "lexicon_path": path,
        "semantic_requested": _entity_linking_semantic_setting(),
        "semantic_surfaces": str(os.environ.get("RAG_ENTITY_LINKING_SURFACES") or config.entity_linking_semantic_surfaces or "all"),
        "max_token_candidates": int(os.environ.get("RAG_ENTITY_LINKING_MAX_TOKEN_CANDIDATES") or config.entity_linking_max_token_candidates),
        "available": linker is not None,
        "error": error,
    }
    if linker is not None:
        status.update(linker.describe())
    return status


def _claim_verifier_parts() -> object | None:
    config = get_config()
    model = _claim_verifier_model_setting()
    cache_dir = str(os.environ.get("RAG_MINICHECK_CACHE_DIR") or config.minicheck_cache_dir or "").strip()
    translation_raw = os.environ.get("RAG_CLAIM_TRANSLATION")
    translation = bool(config.claim_translation) if translation_raw is None else translation_raw.strip().lower() in {"1", "true", "yes", "on"}
    return build_claim_verifier(model_name=model, cache_dir=cache_dir, translation=translation)


def _claim_verifier_status() -> dict[str, object]:
    verifier = _claim_verifier_parts()
    if verifier is None:
        return {
            "configured": False,
            "name": "none",
            "mode": "structural_only",
            "message": "未配置 minicheck_model；主张级语义校验未启用。",
        }
    status = verifier.describe()
    status["configured"] = True
    status["mode"] = "claim_verification"
    return status


def _guideline_lifecycle_parts() -> tuple[str, object | None, str | None]:
    config = get_config()
    mode = _guideline_lifecycle_mode_setting()
    if mode == "off":
        return mode, None, None
    cache_path = str(os.environ.get("RAG_GUIDELINE_LIFECYCLE_CACHE") or config.guideline_lifecycle_cache_path or "").strip()
    ttl_raw = os.environ.get("RAG_GUIDELINE_LIFECYCLE_TTL_DAYS")
    try:
        ttl_days = float(ttl_raw) if ttl_raw else float(config.guideline_lifecycle_ttl_days)
    except ValueError:
        raise ValueError("RAG_GUIDELINE_LIFECYCLE_TTL_DAYS must be a number") from None
    try:
        resolver = load_lifecycle_resolver(mode=mode, cache_path=cache_path, ttl_days=ttl_days)
    except Exception as exc:
        return mode, None, f"{type(exc).__name__}: {exc}"
    return mode, resolver, None


def _guideline_lifecycle_status() -> dict[str, object]:
    config = get_config()
    mode = _guideline_lifecycle_mode_setting()
    cache_path = str(os.environ.get("RAG_GUIDELINE_LIFECYCLE_CACHE") or config.guideline_lifecycle_cache_path or "").strip()
    resolved_mode, resolver, error = _guideline_lifecycle_parts() if mode != "off" else (mode, None, None)
    ttl_raw = os.environ.get("RAG_GUIDELINE_LIFECYCLE_TTL_DAYS")
    try:
        ttl_days = float(ttl_raw) if ttl_raw else float(config.guideline_lifecycle_ttl_days)
    except ValueError:
        ttl_days = config.guideline_lifecycle_ttl_days
    status: dict[str, object] = {
        "mode": resolved_mode,
        "configured": mode != "off",
        "cache_path": cache_path,
        "ttl_days": ttl_days,
        "available": resolver is not None,
        "error": error,
        "source": "pubmed_efetch",
    }
    if resolver is not None:
        status.update(resolver.describe())
    return status


def _pdf_sources() -> list[dict[str, object]]:
    """Describe caller-supplied guideline PDFs without parsing their text."""
    connector = build_pdf_connector()
    if connector is None:
        return []
    return connector.describe_entries()


def _routing_mode() -> str:
    """规则路由模式：env RAG_ROUTING_MODE 覆盖 config（off|shadow|active，默认 off）。"""
    raw = str(os.environ.get("RAG_ROUTING_MODE") or get_config().routing_mode or "off").strip().lower()
    if raw not in ("off", "shadow", "active"):
        raise ValueError("RAG_ROUTING_MODE must be one of: off, shadow, active")
    return raw


def _applicability_preview() -> bool:
    """适用性试算预览开关：env RAG_APPLICABILITY_PREVIEW 覆盖 config（默认关）。"""
    raw = os.environ.get("RAG_APPLICABILITY_PREVIEW", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return bool(get_config().applicability_preview)


def _rerank_pool() -> int:
    """重排候选池：工作台运行时覆盖 > env > config（默认 0=标准模式）。"""
    override = _RERANKER_RUNTIME["rerank_pool"]
    if override is not None:
        return int(override)
    raw = os.environ.get("RAG_RERANK_POOL", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"RAG_RERANK_POOL must be an integer >= 0, got {raw!r}") from None
        if value < 0:
            raise ValueError(f"RAG_RERANK_POOL must be an integer >= 0, got {raw!r}")
        return value
    return int(get_config().rerank_pool)


def _term_mapping_library():
    """可选术语映射库（通用机制）：路径缺失→None（legacy，请求级 term_mapping 之外不加行）。

    文件缺失或损坏显式抛错——配置了库就不得静默当没配；环境变量
    RAG_TERM_MAPPING_LIBRARY 覆盖 config.term_mapping_library_path。
    """
    import os
    from rag.quality.term_mapping_library import TermMappingLibrary
    config = get_config()
    path = str(os.environ.get("RAG_TERM_MAPPING_LIBRARY") or config.term_mapping_library_path or "").strip()
    if not path:
        return None
    return TermMappingLibrary.from_json_file(path)


def _term_mapping_library_status() -> dict:
    """健康检查用的映射库摘要（配置状态 + 概念清单）；库文件坏了也不拖垮 health。"""
    try:
        library = _term_mapping_library()
    except ValueError as exc:
        return {"configured": True, "error": str(exc)[:200]}
    if library is None:
        return {"configured": False}
    info = library.describe()
    return {"configured": True, "source": info["source"], "rows": info["rows"],
            "concepts": info["concepts"], "mesh_status": info.get("mesh_status", {})}


def _fidelity_parts():
    """查询保真层配置：词表路径缺失/为空时 lexicon=None（串换检测标不可检测），
    路径存在但加载失败时显式抛错（不静默忽略配置）。

    用绝对导入：本模块支持 `python -m rag.server` 与直接跑脚本两种启动方式，
    脚本方式下 `__package__` 为空，相对导入会在首次检索时抛 ImportError。
    """
    import os
    from rag.quality.fidelity import FidelityLexicon
    config = get_config()
    path = str(os.environ.get("RAG_FIDELITY_LEXICON") or config.fidelity_lexicon_path or "").strip()
    lexicon = FidelityLexicon.from_json_file(path) if path else None
    policy = str(os.environ.get("RAG_FIDELITY_POLICY") or config.fidelity_default_policy or "off").strip().lower()
    if policy not in RetrievalRequest.FIDELITY_GATE_POLICIES:
        raise ValueError("unknown fidelity policy in config: " + policy)
    return lexicon, policy, float(config.fidelity_penalty)



def _method_status() -> dict[str, dict[str, object]]:
    """报告各检索方法的可用性：依赖是否就绪、模型缓存是否存在。

    只检查本地文件与 import，不加载模型、不下载、不联网。
    """
    deps = neural_dependencies_state()
    cache = cache_dir_hint()
    def cached(repo: str) -> bool:
        if not cache:
            return False
        import pathlib
        folder = "models--" + repo.replace("/", "--")
        return (pathlib.Path(cache) / folder).is_dir()
    return {
        "bm25": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "ql": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "rm3": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "dense": {"available": bool(deps["torch"] and deps["transformers"] and cached("ncbi/MedCPT-Query-Encoder")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("ncbi/MedCPT-Query-Encoder"), "note": "英文域 MedCPT 双塔；中文问题经受控英文术语检索"},
        "xlingual": {"available": bool(deps["torch"] and deps["transformers"] and cached("intfloat/multilingual-e5-base")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("intfloat/multilingual-e5-base"), "note": "多语言 e5 双塔；中文问题直接编码、跨语言检索英文语料"},
        "m3": {"available": bool(deps["torch"] and deps["transformers"] and cached("BAAI/bge-m3")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("BAAI/bge-m3"), "note": "BGE-M3 多语言双塔（CLS 池化）；中文问题直接编码、跨语言检索英文语料；本包只使用其 dense 功能"},
        "translate": {"available": bool(deps["torch"] and deps["transformers"] and cached("Helsinki-NLP/opus-mt-zh-en")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("Helsinki-NLP/opus-mt-zh-en"), "note": "zh→en 机器翻译查询变体；译文不改写原问题，数字不一致会告警"},
        "splade": {"available": bool(deps["torch"] and deps["transformers"] and cached("naver/splade-cocondenser-ensembledistil")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("naver/splade-cocondenser-ensembledistil")},
        "colbert": {"available": bool(deps["torch"] and deps["transformers"] and cached("colbert-ir/colbertv2.0")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("colbert-ir/colbertv2.0")},
        "query2doc": {"available": bool(deps["torch"] and deps["transformers"] and cached("google/flan-t5-small")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("google/flan-t5-small")},
        "cross_encoder": {"available": bool(deps["torch"] and deps["transformers"] and cached("ncbi/MedCPT-Cross-Encoder")), "kind": "reranker", "dependency": "torch+transformers", "model_cached": cached("ncbi/MedCPT-Cross-Encoder")},
        "qwen3_reranker": {"available": bool(deps["torch"] and deps["transformers"] and cached("Qwen/Qwen3-Reranker-0.6B")), "kind": "reranker", "dependency": "torch+transformers>=4.51", "model_cached": cached("Qwen/Qwen3-Reranker-0.6B"), "note": "Qwen3-Reranker-0.6B 多语言 pointwise 重排（中文问题可见）；由 config.reranker_model 选择"},
    }

CUSTOM_LIBRARY = CustomSourceLibrary()


def _audit_mode() -> str:
    """health 摘要：默认审计只做引用结构检查，语义主张校验需调用方注入 verifier。"""
    return "structural_only"


def load_startup_demo_library() -> int:
    """可选：config.demo_autoload_guidelines=True 时，在启动阶段显式登记演示指南库。

    这只是把"调用方显式加载"提前到启动阶段，并不改变"服务不内置语料"：
    返回登记的来源数，未开启时返回 0。
    """
    if not get_config().demo_autoload_guidelines:
        return 0
    events = register_demo_guidelines(CUSTOM_LIBRARY)
    return len(events)


def load_startup_custom_library() -> int:
    """可选：从 config.custom_sources_path 预载自定义库；返回加载的来源数。"""
    path = str(get_config().custom_sources_path or "").strip()
    if not path:
        return 0
    library = load_custom_library(path)
    for source in library._sources.values():  # noqa: SLF001 - same module family
        CUSTOM_LIBRARY.register(source)
    return len(library)


class RAGRequestHandler(BaseHTTPRequestHandler):
    source_mode = "live"
    tools: dict[str, EvidenceRAG] = {}
    retrieval_tools: dict[str, EvidenceRAG] = {}

    @classmethod
    def _custom_tool(cls) -> EvidenceRAG:
        """自定义来源库变动后（注册/清空）重建工具，保证检索反映最新库内容。"""
        return create_tool("custom", custom_library=CUSTOM_LIBRARY)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in {"/", "/index.html"}:
            index = WEB_DIST / "index.html"
            if not index.is_file():
                self._send_error_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "工作台构建产物缺失：请在 rag/web 执行 npm run build",
                )
                return
            self._send_file(index, "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            target = (WEB_DIST / path.lstrip("/")).resolve()
            dist_root = WEB_DIST.resolve()
            if target != dist_root and dist_root not in target.parents:
                self._send_error_json(HTTPStatus.NOT_FOUND, "资源不存在")
                return
            self._send_file(
                target,
                cache_control="public, max-age=31536000, immutable",
            )
            return
        if path == "/api/health":
            config = get_config()
            live_available = bool(config.allow_live_pubmed and config.allow_live_guidelines)
            self._send_json({
                "ok": True,
                "rag_version": __version__,
                "demo_profile": get_config().demo_profile,
                "demo_cases_available": True,
                "applicability_cases_available": True,
                "demo_guidelines_loaded": any(
                    item["source_id"].startswith("neonatal-guideline-") for item in CUSTOM_LIBRARY.describe()
                ),
                "retrieval_mode": self.source_mode,
                "active_mode": self.source_mode,
                "available_modes": [mode for mode, enabled in (("live", live_available), ("custom", True)) if enabled],
                "his_mode": HIS_MODE,
                "sources": self._describe_sources(),
                "custom_sources": CUSTOM_LIBRARY.describe(),
                "guideline_parsing": _guideline_status(),
                "semantic_trace": {"status": "not_configured", "retriever_id": None},
                "generation_performed": False,
                "clinical_decision_performed": False,
                "audit_mode": _audit_mode(),
                "retrieval_methods": _method_status(),
                "term_mapping_library": _term_mapping_library_status(),
                "reranker": _reranker_status(),
                "routing": {"mode": _routing_mode()},
                "controller": {"version": "controller-v1-trial", "default_enabled": False, "endpoint": "/api/controller/retrieve"},
                "entity_linking": _entity_linking_status(),
                "guideline_lifecycle": _guideline_lifecycle_status(),
                "claim_verifier": _claim_verifier_status(),
                "pubmed": {"max_results": _pubmed_max_results()},
                "gui": {
                    "framework": "react",
                    "entry": "/",
                    "build": "rag/web/dist",
                    "built": (WEB_DIST / "index.html").is_file(),
                },
                "message": "检索来源仅两种：live（实时 PubMed + 指南元数据 + Europe PMC 指南全文 + 调用方 PDF 指南全文）与 custom（调用方注册的数据库）；服务不内置任何本地语料；新生儿演示指南库需显式加载。",
            })
            return
        if path == "/api/metrics":
            if str(query.get("format", ["json"])[0]).lower() == "prometheus":
                self._send_text(METRICS.prometheus(), "text/plain; version=0.0.4; charset=utf-8")
            else:
                self._send_json(METRICS.snapshot())
            return
        if path == "/api/tools":
            self._send_json({"tools": EvidenceRAG.tool_specifications()})
            return
        if path == "/api/sources":
            live_enabled = bool(get_config().allow_live_pubmed and get_config().allow_live_guidelines)
            self._send_json({
                "source_modes": list(SOURCE_MODES),
                "live": {
                    "enabled": live_enabled,
                    "sources": (
                        ["pubmed-live", NEONATAL_GUIDELINE_SOURCE_ID]
                        + (["guideline-fulltext-live"] if get_config().allow_live_guideline_fulltext else [])
                        + (["guideline-pdf-local"] if _pdf_sources() else [])
                    ) if live_enabled else [],
                    "guideline_fulltext_enabled": bool(get_config().allow_live_guideline_fulltext),
                    "guideline_registry": describe_guideline_registry(neonatal_guideline_metadata_registry()),
                    "guideline_pdf_dir": str(get_config().guideline_pdf_dir or ""),
                    "guideline_pdf_sources": _pdf_sources(),
                },
                "custom": {"registered": CUSTOM_LIBRARY.describe(), "recent_events": CUSTOM_LIBRARY.events[-10:]},
                "guideline_parsing": _guideline_status(),
            })
            return
        if path == "/api/guidelines":
            source = _guideline_source()
            if source is None:
                self._send_json({
                    "configured": False,
                    "data_dir": "",
                    "guidelines": [],
                    "options": _guideline_parse_options(),
                    "note": "未配置 RAG_GUIDELINE_DATA；设置后按 guidelines/manifest.json 列出指南。",
                })
                return
            try:
                guidelines = source.list_guidelines()
                error = None
            except GuidelineParsingError as exc:
                guidelines, error = [], str(exc)
            self._send_json({
                "configured": True,
                "data_dir": str(source.data_dir),
                "guidelines": guidelines,
                "options": _guideline_parse_options(),
                "error": error,
                "note": "解析产物写入 <data_dir>/<document_id>_chunks.jsonl；custom 模式检索会重新加载。",
            })
            return
        if path == "/api/guidelines/chunks":
            document_id = str(query.get("document_id", [""])[0]).strip()
            if not document_id:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "缺少 document_id")
                return
            try:
                chunks = _require_guideline_source().chunks(document_id)
            except (ValueError, GuidelineParsingError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({"document_id": document_id, "count": len(chunks), "chunks": chunks})
            return
        if path == "/api/guidelines/pdf":
            document_id = str(query.get("document_id", [""])[0]).strip()
            try:
                pdf, _row = _guideline_pdf(_require_guideline_source(), document_id)
            except (ValueError, GuidelineParsingError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_file(pdf, "application/pdf")
            return
        if path == "/api/guidelines/pdf/meta":
            document_id = str(query.get("document_id", [""])[0]).strip()
            try:
                pdf, row = _guideline_pdf(_require_guideline_source(), document_id)
                pages = _pdf_page_count(pdf)
            except (ValueError, GuidelineParsingError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json({
                "document_id": document_id,
                "file": pdf.name,
                "title": row.get("title") or document_id,
                "version_id": row.get("version_id") or "",
                "pages": pages,
            })
            return
        if path == "/api/guidelines/pdf/page":
            document_id = str(query.get("document_id", [""])[0]).strip()
            page = str(query.get("page", ["1"])[0]).strip()
            dpi = str(query.get("dpi", ["110"])[0]).strip()
            try:
                page_number = int(page)
                dpi_value = min(max(int(dpi), 60), 200)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "page/dpi 必须是整数")
                return
            if page_number < 1:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "page 必须 >= 1")
                return
            try:
                pdf, _row = _guideline_pdf(_require_guideline_source(), document_id)
                body = _render_pdf_page(pdf, page_number, dpi_value)
            except (ValueError, GuidelineParsingError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_bytes(body, "image/png")
            return
        if path == "/api/demo/cases":
            demo = load_demo_cases()
            self._send_json({
                "profile": get_config().demo_profile,
                "synthetic": True,
                "cases": demo["cases"],
                "case_sets": demo.get("case_sets", {}),
                "age_rules": demo.get("age_rules", {}),
                "note": "全部病例为合成数据，仅用于界面与流程测试；不是真实患者，也不构成诊疗建议。",
            })
            return
        if path == "/api/demo/applicability-cases":
            demo = load_applicability_cases()
            self._send_json({
                "profile": demo["profile"],
                "synthetic": True,
                "cases": demo["cases"],
                "case_sets": demo.get("case_sets", {}),
                "note": demo.get("note", ""),
            })
            return
        if path == "/api/demo/guidelines":
            self._send_json({
                "profile": get_config().demo_profile,
                "guidelines": demo_guideline_summary(load_demo_guidelines()),
                "registered": [item for item in CUSTOM_LIBRARY.describe() if item["source_id"].startswith("neonatal-")],
                "note": "演示来源（指南元数据/合成指南/合成文献）需显式加载；元数据条目只含书目信息，合成条目 synthetic=true。",
            })
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该资源")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/his/snapshot":
                # HIS 由调用方提供快照：请求体中的 patient_snapshot 原样回显并做 as_of 过滤。
                # 服务不持有任何内置患者数据。
                self._send_json({"status": "ok", "data": self._echo_snapshot(payload)})
                return
            if path == "/api/guidelines/parse":
                base = _require_guideline_source()
                options = _parse_options_from_payload(payload)
                document_ids = payload.get("document_ids")
                if document_ids is None:
                    single = str(payload.get("document_id") or "").strip()
                    document_ids = [single] if single else None
                if document_ids is not None and not isinstance(document_ids, list):
                    raise ValueError("document_ids 必须是数组")
                force = payload.get("force") in (True, 1, "1", "true", "yes", "on")
                parser = GuidelineParsingSource(
                    data_dir=base.data_dir,
                    mineru_bin=base.mineru_bin,
                    chunk_strategy=options["chunk_strategy"],
                    table_strategy=options["table_strategy"],
                    figure_strategy=options["figure_strategy"],
                    chunk_size=options["chunk_size"],
                    overlap=options["overlap"],
                    tier=options["tier"],
                )
                results = parser.parse(document_ids=document_ids, force=force)
                self.tools.pop("custom", None)
                self._send_json({
                    "status": "ok",
                    "results": results,
                    "guidelines": parser.list_guidelines(),
                    "options": options,
                })
                return
            if path == "/api/guidelines/save-live":
                source = _require_guideline_source()
                try:
                    result = _save_live_guideline(source, payload)
                except GuidelineFullTextError as exc:
                    self._send_error_json(HTTPStatus.BAD_GATEWAY, str(exc))
                    return
                self.tools.pop("custom", None)
                self._send_json(result)
                return
            if path == "/api/guidelines/applicability":
                document_id = str(payload.get("document_id") or "").strip()
                chunk_id = str(payload.get("chunk_id") or "").strip()
                snapshot_raw = payload.get("patient_snapshot") or payload.get("snapshot")
                if not document_id or not isinstance(snapshot_raw, Mapping):
                    raise ValueError("需要 document_id 和 patient_snapshot 对象")
                library = _require_guideline_source().load_library()
                documents = library.sources.get(document_id)
                if not documents:
                    raise GuidelineParsingError(f"未知指南：{document_id}")
                document = next(
                    (item for item in documents if item.document_id == chunk_id),
                    documents[0],
                )
                from rag.quality.applicability import assess_applicability
                from rag.his import snapshot_from_dict

                snapshot = snapshot_from_dict({"patient_snapshot": dict(snapshot_raw)})
                assessment = assess_applicability(document, snapshot, ())
                self._send_json({
                    "document_id": document_id,
                    "chunk_id": document.document_id,
                    "title": document.title,
                    "locator": document.locator,
                    "assessment": asdict(assessment),
                    "patient": {
                        "patient_ref": snapshot.patient_ref,
                        "as_of": snapshot.as_of,
                        "visible_fact_count": len(snapshot.visible_facts()),
                    },
                })
                return
            if path == "/api/demo/guidelines/load":
                events = register_demo_guidelines(CUSTOM_LIBRARY)
                self.tools.pop("custom", None)  # 库变动后失效，下次检索重建
                self._send_json({"status": "ok", "profile": get_config().demo_profile, "events": events, "registered": CUSTOM_LIBRARY.describe()})
                return
            if path == "/api/sources/custom":
                event = CUSTOM_LIBRARY.register_payload(payload)
                self.tools.pop("custom", None)  # 库变动后失效，下次检索重建
                self._send_json({"status": "ok", "event": event, "registered": CUSTOM_LIBRARY.describe()})
                return
            if path == "/api/sources/custom/clear":
                removed = CUSTOM_LIBRARY.clear()
                self.tools.pop("custom", None)
                self._send_json({"status": "ok", "removed_sources": removed})
                return
            if path == "/api/settings/industrial":
                settings = _set_industrial_settings(payload)
                type(self).tools.clear()  # 下一次检索按新配置重建；旧 retrieval_id 仍由 retrieval_tools 持有
                self._send_json({
                    "status": "ok",
                    "industrial": settings,
                    "message": "新检索将使用新的实体链接/生命周期/主张校验配置；已打开的旧引用仍绑定原检索工具。",
                })
                return
            if path == "/api/settings/reranker":
                settings = _set_reranker_settings(payload)
                type(self).tools.clear()  # 下一次检索按新配置重建；旧 retrieval_id 仍由 retrieval_tools 持有
                self._send_json({
                    "status": "ok",
                    "reranker": settings,
                    "message": "新检索将使用新配置；已打开的旧引用仍绑定原检索工具。",
                })
                return
            if path == "/api/controller/retrieve":
                mode = str(payload.get("source_mode") or self.source_mode)
                tool = self._tool_for(mode)
                raw = dict(payload)
                policy_raw = raw.pop("controller_policy", None)
                policy = None
                if policy_raw is not None:
                    if not isinstance(policy_raw, dict):
                        raise ValueError("controller_policy must be an object")
                    policy = ControllerPolicy(**policy_raw)
                result = RetrievalController(tool.pipeline, policy=policy).run(RetrievalRequest.from_dict(raw))
                self._send_json(result)
                return
            if path == "/api/retrieve":
                mode = str(payload.get("source_mode") or self.source_mode)
                tool = self._tool_for(mode)
                result = tool.retrieve_evidence(payload)
                self.retrieval_tools[result["retrieval_id"]] = tool
                self._send_json(result)
                return
            if path == "/api/applicability":
                mode = str(payload.get("source_mode") or self.source_mode)
                tool = self._tool_for(mode)
                self._send_json(tool.resolve_applicability_workflow(payload))
                return
            if path == "/api/citation":
                self._send_json(self._tool_for_retrieval(str(payload.get("retrieval_id") or "")).open_citation(payload))
                return
            if path == "/api/audit":
                self._send_json(self._tool_for_retrieval(str(payload.get("retrieval_id") or "")).audit_grounded_answer(payload))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该接口")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, GuidelineParsingError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc) or type(exc).__name__)
        except Exception as exc:  # pragma: no cover - defensive HTTP boundary
            error_id = uuid.uuid4().hex[:12]
            message = _safe_error_message(exc)
            print(f"[{error_id}] {type(exc).__name__}: {message}", file=sys.stderr)
            traceback.print_exc()
            self._send_json({
                "error": message, "exception_type": type(exc).__name__,
                "stage": "request", "error_id": error_id,
            }, HTTPStatus.BAD_GATEWAY)

    def log_message(self, format: str, *args: Any) -> None:
        return

    @classmethod
    def _describe_sources(cls) -> list[dict[str, object]]:
        if cls.source_mode == "live":
            try:
                return build_live_registry().describe()
            except ValueError:
                return []
        if cls.source_mode == "custom":
            return CUSTOM_LIBRARY.describe()
        return []

    @classmethod
    def _tool_for(cls, mode: str) -> EvidenceRAG:
        if mode not in SOURCE_MODES:
            raise ValueError(f"unknown source_mode: {mode}（可用：{', '.join(SOURCE_MODES)}）")
        if mode == "custom":
            return cls._custom_tool()
        if mode not in cls.tools:
            cls.tools[mode] = create_tool(mode)
        return cls.tools[mode]

    @classmethod
    def _tool_for_retrieval(cls, retrieval_id: str) -> EvidenceRAG:
        if retrieval_id not in cls.retrieval_tools:
            raise ValueError("unknown retrieval_id; retrieve evidence before opening a citation")
        return cls.retrieval_tools[retrieval_id]

    @staticmethod
    def _echo_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        """以调用方提供的 patient_snapshot 为准，按 as_of 过滤后回显（只读、无内置数据）。"""
        snapshot_raw = payload.get("patient_snapshot") or payload.get("data") or payload
        if not isinstance(snapshot_raw, dict):
            raise ValueError("his snapshot endpoint requires a JSON object payload")
        from rag.his import snapshot_from_dict
        snapshot: PatientSnapshot = snapshot_from_dict({"patient_snapshot": snapshot_raw})
        return asdict(snapshot)


    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length > MAX_BODY_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return parsed

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send_file(
        self,
        path: Path,
        content_type: str | None = None,
        cache_control: str = "no-store",
    ) -> None:
        if not path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "页面文件不存在")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(body)


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    for secret_name in ("openai_api_key", "ncbi_api_key"):
        secret = str(getattr(get_config(), secret_name, "") or "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def _find_port_pids(port: int) -> list[int]:
    """返回监听指定 TCP 端口的进程 PID 列表（Linux：ss，回退 lsof）。

    仅用于陈旧实例清退；不会终止任何进程。
    """
    import re
    import subprocess

    pids: list[int] = []
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]
            if not local.endswith(f":{port}"):
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.append(int(match.group(1)))
    except Exception:
        pass
    if not pids:
        try:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5).stdout
            pids.extend(int(line.strip()) for line in out.splitlines() if line.strip().isdigit())
        except Exception:
            pass
    return sorted(set(pids))


def _is_rag_server_process(pid: int) -> bool:
    """严格校验 PID 是否**本包**（本目录下的 rag 包）的 workbench 服务进程。

    同一仓库的 Medical-Assistant 也用 `-m rag.server` 启动、包名相同，
    因此不能只看模块名——必须确认启动路径解析到的就是本目录的 server.py：
    ``<entry>/rag/server.py``（-m 启动）或 ``.../rag/server.py``（脚本启动）
    解析后等于本包的 server.py，才算可清退目标。无关进程一律不动。
    """
    import os

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    if not args or "python" not in Path(args[0]).name.lower():
        return False
    package_root = Path(__file__).resolve().parent
    target_server = package_root / "server.py"

    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""

    def _resolve(entry: str) -> Path | None:
        candidate = Path(entry)
        if not candidate.is_absolute():
            if not cwd:
                return None
            candidate = Path(cwd) / candidate
        try:
            return candidate.resolve()
        except OSError:
            return None

    def _is_this_package_entry(resolved: Path | None) -> bool:
        """entry 作为 sys.path 项时，rag 包是否解析到本目录。"""
        if resolved is None:
            return False
        try:
            return (resolved / "rag" / "server.py").resolve() == target_server
        except OSError:
            return False

    for index, arg in enumerate(args):
        if arg == "rag.server" and index > 0 and args[index - 1] == "-m":
            try:
                environ = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
            except OSError:
                environ = ""
            for part in environ.split("\0"):
                if part.startswith("PYTHONPATH="):
                    for entry in part.split("=", 1)[1].split(":"):
                        if entry and _is_this_package_entry(_resolve(entry)):
                            return True
            if cwd and _is_this_package_entry(_resolve(cwd)):
                return True
        elif arg.endswith("server.py"):
            resolved = _resolve(arg)
            try:
                if resolved is not None and resolved == target_server:
                    return True
            except OSError:
                continue
    return False


def _process_alive(pid: int) -> bool:
    """True 表示进程仍存活（僵尸进程视为已退出，其占用的端口已释放）。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        state = stat.rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return False


def _release_port(port: int, *, wait_seconds: float = 5.0) -> list[int]:
    """若端口被旧的 RAG 服务实例占用，将其清退（SIGTERM，拒绝退出则 SIGKILL）。

    返回被清退的 PID 列表。非 RAG 服务进程一律不动，交由调用方报错提示。
    """
    import os
    import signal
    import time

    victims = [pid for pid in _find_port_pids(port) if _is_rag_server_process(pid)]
    if not victims:
        return []
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not any(_process_alive(pid) for pid in victims):
            return victims
        time.sleep(0.1)
    for pid in victims:
        if _process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return victims


def serve(host: str | None = None, port: int | None = None, *, cleanup_stale: bool = True) -> None:
    import errno

    loaded = load_startup_custom_library()
    demo_loaded = load_startup_demo_library()
    config = get_config()
    host = config.server_host if host is None else host
    if port is None:
        env_port = os.environ.get("RAG_SERVER_PORT", "").strip()
        port = int(env_port) if env_port.isdigit() else config.server_port
    if cleanup_stale and port > 0:
        released = _release_port(port)
        if released:
            print(f"已清退占用端口 {port} 的旧 RAG 实例：{', '.join(str(pid) for pid in released)}")
    try:
        server = ThreadingHTTPServer((host, port), RAGRequestHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            holders = _find_port_pids(port)
            hint = f"（端口持有者 PID: {', '.join(str(pid) for pid in holders)}，非 RAG 服务进程，未自动清退）" if holders else ""
            print(f"端口 {port} 被占用{hint}；请手动释放后重试。")
            raise SystemExit(1) from exc
        raise
    live_ok = bool(config.allow_live_pubmed and config.allow_live_guidelines)
    print(f"RAG workbench: http://{host}:{port}/")
    fulltext = "Europe PMC 指南全文" if config.allow_live_guideline_fulltext else "仅指南元数据（全文开关关闭）"
    pdf_sources = _pdf_sources()
    print(f"  检索来源：live（{'可用' if live_ok else '未启用：config.allow_live_* 未同时为 True'}；{fulltext}）；custom（已注册来源 {len(CUSTOM_LIBRARY)} 个{('，启动预载 ' + str(loaded)) if loaded else ''}{('，演示指南库 ' + str(demo_loaded)) if demo_loaded else ''}）")
    print("  HIS：由调用方随请求传入 patient_snapshot（服务不内置患者数据）")
    print(f"  演示档案：{config.demo_profile}（病例 GET /api/demo/cases；演示来源需 POST /api/demo/guidelines/load 显式加载）")
    print(f"  指南 PDF：{config.guideline_pdf_dir}（已登记 {len(pdf_sources)} 篇；检索时实时解析，不落盘）" if pdf_sources else f"  指南 PDF：{config.guideline_pdf_dir}（未登记，跳过 guideline-pdf-local）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
