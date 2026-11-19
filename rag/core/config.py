"""Configuration loader for the RAG retrieval package.

配置值放在与 ``server.py`` 同级的 ``.env``（``rag/.env``）里，本模块负责
读取、按类型校验并暴露为 ``RAGConfig``。解析优先级：

    进程环境变量  >  rag/.env  >  代码内默认值

空字符串按"未设置"处理（回退到 .env / 默认），与 ``os.environ.get(x) or default``
语义一致。凭据（openai / ncbi / openfda）只从环境或 .env 注入，不写进代码、
文档或 git；``.env`` 不进版本控制（见 ``rag/.env.example`` 模板）。
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import os

#: .env 与 server.py 同级（rag/.env）。
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

# 结构性路径默认值：由代码位置推导（data/ 与 rag/ 同级在版本根），
# .env 可覆盖。data/guidelines/ 放指南 PDF 与登记表；data/catalog/ 放人工维护
# 资产；data/generated/ 放解析、缓存、评估与演示生成物。不写用户 home。
_DEFAULT_MESH_LEXICON = str(Path(__file__).resolve().parents[1] / "demo" / "mesh_lexicon_neonatal_2026.json")
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_GENERATED_DIR = _DATA_DIR / "generated"
_DEFAULT_LIFECYCLE_CACHE = str(_GENERATED_DIR / "guideline_lifecycle_cache.json")
_DEFAULT_GUIDELINE_DATA = str(_DATA_DIR)
_DEFAULT_GUIDELINE_PDF_DIR = str(_DATA_DIR / "guidelines")
_DEFAULT_GUIDELINE_WORK_DIR = str(_GENERATED_DIR / "parsing")


@dataclass(frozen=True)
class RAGConfig:
    # --- 服务 ---
    server_host: str = "127.0.0.1"
    server_port: int = 8798
    request_timeout_seconds: float = 60.0
    # 来源模式仅两种：live（PubMed + 本地指南 + 在线药品）与 custom（调用方注册库）。
    allow_live_pubmed: bool = True

    # --- 凭据 / 外部服务（只从环境或 .env 注入，默认空） ---
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""
    ncbi_email: str = ""
    ncbi_api_key: str = ""
    openfda_api_key: str = ""

    # --- 指南来源与解析 ---
    guideline_data_dir: str = _DEFAULT_GUIDELINE_DATA if Path(_DEFAULT_GUIDELINE_DATA).is_dir() else ""
    guideline_work_dir: str = _DEFAULT_GUIDELINE_WORK_DIR
    guideline_pdf_dir: str = _DEFAULT_GUIDELINE_PDF_DIR if Path(_DEFAULT_GUIDELINE_PDF_DIR).is_dir() else ""
    guideline_chunks_path: str = ""
    guideline_mineru_bin: str = ""
    guideline_auto_parse: bool = False
    guideline_max_chunks: int = 3
    custom_sources_path: str = ""

    # --- 演示 ---
    demo_profile: str = "neonatal_demo_v1"
    demo_autoload_guidelines: bool = False

    # --- 检索方法 ---
    pubmed_max_results: int = 200
    reranker_model: str = "medcpt"
    rerank_pool: int = 0
    fidelity_lexicon_path: str = ""
    fidelity_default_policy: str = "off"
    fidelity_penalty: float = 0.5
    term_mapping_library_path: str = ""
    routing_mode: str = "off"
    applicability_preview: bool = False

    # --- 实体链接 / MeSH ---
    mesh_lexicon_path: str = _DEFAULT_MESH_LEXICON
    entity_linking_mode: str = "active"
    entity_linking_semantic: bool = True
    entity_linking_semantic_surfaces: str = "all"
    entity_linking_max_token_candidates: int = 200
    entity_linking_max_entry_terms: int = 2
    allow_model_download: bool = False

    # --- 主张校验 / 生命周期 ---
    minicheck_model: str = "flan-t5-large"
    minicheck_cache_dir: str = ""
    claim_translation: bool = True
    guideline_lifecycle_mode: str = "active"
    guideline_lifecycle_cache_path: str = _DEFAULT_LIFECYCLE_CACHE
    guideline_lifecycle_ttl_days: float = 7.0

    # --- 患者图谱 ---
    patient_graph_max_hops: int = 2
    patient_graph_max_nodes: int = 240
    patient_graph_lexicon_path: str = ""

    #: 被进程环境变量或 rag/.env 显式设置的字段名集合（不含代码默认）。
    #: 用于区分"调用方覆盖"与"默认值"，例如 guidelines 来源优先级判断。
    explicit: frozenset[str] = frozenset()


# RAGConfig 字段 → .env / 环境变量名（.env 用同样的键）。
ENV_MAP: dict[str, str] = {
    "server_host": "RAG_SERVER_HOST",
    "server_port": "RAG_SERVER_PORT",
    "request_timeout_seconds": "RAG_REQUEST_TIMEOUT_SECONDS",
    "allow_live_pubmed": "RAG_ALLOW_LIVE_PUBMED",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_model": "OPENAI_MODEL",
    "openai_base_url": "OPENAI_BASE_URL",
    "ncbi_email": "RAG_NCBI_EMAIL",
    "ncbi_api_key": "RAG_NCBI_API_KEY",
    "openfda_api_key": "OPENFDA_API_KEY",
    "guideline_data_dir": "RAG_GUIDELINE_DATA",
    "guideline_work_dir": "RAG_GUIDELINE_WORK",
    "guideline_pdf_dir": "RAG_GUIDELINE_PDF_DIR",
    "guideline_chunks_path": "RAG_GUIDELINE_CHUNKS",
    "guideline_mineru_bin": "RAG_MINERU_BIN",
    "guideline_auto_parse": "RAG_GUIDELINE_AUTO_PARSE",
    "guideline_max_chunks": "RAG_GUIDELINE_MAX_CHUNKS",
    "custom_sources_path": "RAG_CUSTOM_SOURCES",
    "demo_profile": "RAG_DEMO_PROFILE",
    "demo_autoload_guidelines": "RAG_DEMO_AUTOLOAD_GUIDELINES",
    "pubmed_max_results": "RAG_PUBMED_MAX_RESULTS",
    "reranker_model": "RAG_RERANKER_MODEL",
    "rerank_pool": "RAG_RERANK_POOL",
    "fidelity_lexicon_path": "RAG_FIDELITY_LEXICON",
    "fidelity_default_policy": "RAG_FIDELITY_POLICY",
    "fidelity_penalty": "RAG_FIDELITY_PENALTY",
    "term_mapping_library_path": "RAG_TERM_MAPPING_LIBRARY",
    "routing_mode": "RAG_ROUTING_MODE",
    "applicability_preview": "RAG_APPLICABILITY_PREVIEW",
    "mesh_lexicon_path": "RAG_MESH_LEXICON",
    "entity_linking_mode": "RAG_ENTITY_LINKING_MODE",
    "entity_linking_semantic": "RAG_ENTITY_LINKING_SEMANTIC",
    "entity_linking_semantic_surfaces": "RAG_ENTITY_LINKING_SURFACES",
    "entity_linking_max_token_candidates": "RAG_ENTITY_LINKING_MAX_TOKEN_CANDIDATES",
    "entity_linking_max_entry_terms": "RAG_ENTITY_LINKING_MAX_ENTRY_TERMS",
    "allow_model_download": "RAG_ALLOW_MODEL_DOWNLOAD",
    "minicheck_model": "RAG_MINICHECK_MODEL",
    "minicheck_cache_dir": "RAG_MINICHECK_CACHE_DIR",
    "claim_translation": "RAG_CLAIM_TRANSLATION",
    "guideline_lifecycle_mode": "RAG_GUIDELINE_LIFECYCLE_MODE",
    "guideline_lifecycle_cache_path": "RAG_GUIDELINE_LIFECYCLE_CACHE",
    "guideline_lifecycle_ttl_days": "RAG_GUIDELINE_LIFECYCLE_TTL_DAYS",
    "patient_graph_max_hops": "RAG_PATIENT_GRAPH_MAX_HOPS",
    "patient_graph_max_nodes": "RAG_PATIENT_GRAPH_MAX_NODES",
    "patient_graph_lexicon_path": "RAG_PATIENT_GRAPH_LEXICON",
}


def _load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env 解析（KEY=VALUE、# 注释、可选引号）；无第三方依赖。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


_ENV_VALUES = _load_env_file(ENV_FILE)


def _raw(env_name: str) -> str | None:
    """进程环境 > .env；空字符串视为未设置。"""
    value = os.environ.get(env_name)
    if value is None or value == "":
        value = _ENV_VALUES.get(env_name)
    if value is None or value == "":
        return None
    return value


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build() -> RAGConfig:
    defaults = RAGConfig()
    overrides: dict[str, object] = {}
    explicit: set[str] = set()
    for f in fields(defaults):
        if f.name == "explicit":
            continue
        env_name = ENV_MAP.get(f.name)
        raw = _raw(env_name) if env_name else None
        if raw is None:
            continue
        explicit.add(f.name)
        current = getattr(defaults, f.name)
        if isinstance(current, bool):
            overrides[f.name] = _as_bool(raw)
        elif isinstance(current, int) and not isinstance(current, bool):
            try:
                overrides[f.name] = int(float(raw))
            except ValueError:
                pass
        elif isinstance(current, float):
            try:
                overrides[f.name] = float(raw)
            except ValueError:
                pass
        else:
            overrides[f.name] = raw
    overrides["explicit"] = frozenset(explicit)
    return RAGConfig(**overrides)


def get_config() -> RAGConfig:
    """返回解析后的配置（进程环境 > rag/.env > 代码默认）。

    每次调用都重建，保证运行时对环境变量的修改（如测试里的 mock.patch.dict）
    能立刻生效；.env 解析结果缓存在模块级，重建开销可忽略。
    """
    return _build()


def reload_config() -> RAGConfig:
    """强制重新读取 rag/.env（测试/运维改了 .env 后调用）。"""
    global _ENV_VALUES
    _ENV_VALUES = _load_env_file(ENV_FILE)
    return get_config()
