"""Local configuration for the RAG retrieval package."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 默认使用仓库内的 C16 新生儿域 MeSH 快照；全量词表可通过
# RAG_MESH_LEXICON 指向调用方自备的外部文件。
_DEFAULT_MESH_LEXICON = str(Path(__file__).resolve().parent / "demo" / "mesh_lexicon_neonatal_2026.json")
# 项目数据统一放在与 rag/ 同级的 data/，不写到用户 home 目录。
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_LIFECYCLE_CACHE = str(_DATA_DIR / "guideline_lifecycle_cache.json")
_DEFAULT_GUIDELINE_DATA = str(_DATA_DIR / "guideline_parsing")
_DEFAULT_GUIDELINE_PDF_DIR = str(_DATA_DIR / "guideline_parsing" / "guidelines")


@dataclass(frozen=True)
class RAGConfig:
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""
    openai_api_mode: str = "auto"
    ncbi_email: str = ""
    ncbi_api_key: str = ""
    # 主张级语义校验：官方 minicheck 包 + 本地模型权重。默认启用
    # flan-t5-large；填 "" 可退回 structural_only。可选 canonical 名：
    # flan-t5-large / roberta-large / deberta-v3-large，也接受模型卡 HF id。
    # 环境变量 RAG_MINICHECK_MODEL / RAG_MINICHECK_CACHE_DIR 可覆盖。
    minicheck_model: str = "flan-t5-large"
    minicheck_cache_dir: str = ""
    # 中文主张先经本地 opus-mt-zh-en 翻译再校验；关闭时中文主张显式 not_checked。
    claim_translation: bool = True
    request_timeout_seconds: float = 60.0
    # PubMed ESearch 默认返回条数；请求的 top_k 会通过管线放大为
    # max(top_k*5, 50)，连接器再按此上限截断。默认 200 条，避免小样本。
    # 环境变量 RAG_PUBMED_MAX_RESULTS 可覆盖。
    pubmed_max_results: int = 200
    server_host: str = "127.0.0.1"
    server_port: int = 8798
    # 来源模式仅两种：live（实时 PubMed + 官方指南元数据）与 custom（调用方注册的数据库）。
    # 服务不内置任何本地语料，也没有 offline/simulated 模式。
    source_mode: str = "live"
    allow_live_pubmed: bool = True
    allow_live_guidelines: bool = True
    # 实时指南全文（Europe PMC JATS）。按现行来源策略，指南全文均可检索：
    # 优先抓取 JATS 全文；无全文时退到书目/摘要 + 链接，并显式写 warning。
    allow_live_guideline_fulltext: bool = True
    # 指南 PDF 目录：<dir>/guideline_pdfs.json + PDF。
    # 默认使用仓库内 data/guideline_parsing/guidelines；不存在时留空。
    guideline_pdf_dir: str = _DEFAULT_GUIDELINE_PDF_DIR if Path(_DEFAULT_GUIDELINE_PDF_DIR).is_dir() else ""
    # 指南书目元数据实时查询（Europe PMC 按 DOI/PMID）：失败时回退本地注册表。
    allow_live_guideline_metadata_lookup: bool = True
    # 可选：启动时预载自定义来源库文件（{"sources": [...]} 或 JSON 数组；
    # 单个来源的 schema 见 custom_sources.py 与 contracts/README.md）。
    # 留空表示不预载，调用方在运行时用 POST /api/sources/custom 注册。
    custom_sources_path: str = ""
    # 可选：指南解析产出的分块 JSONL。
    # 指向单个 *_chunks.jsonl、导出库 JSON 或递归搜索的目录；仅 custom 模式
    # 注册。环境变量 RAG_GUIDELINE_CHUNKS 可覆盖。见 sources/guideline_chunks.py。
    guideline_chunks_path: str = ""
    # 可选：指南解析数据目录（与 rag/ 同级的 guideline_parsing 输出）。
    # 布局：guidelines/（manifest + PDF/Markdown）、parsed_markdown/、
    # <document_id>_chunks.jsonl、applicability/。配置后 custom 模式的指南
    # 来源优先使用本地解析器。环境变量 RAG_GUIDELINE_DATA 可覆盖。
    # 默认使用仓库内 data/guideline_parsing；不存在则为空。
    guideline_data_dir: str = _DEFAULT_GUIDELINE_DATA if Path(_DEFAULT_GUIDELINE_DATA).is_dir() else ""
    # MinerU 可执行文件路径（独立环境时使用）；环境变量 RAG_MINERU_BIN 可覆盖。
    # MinerU 不随仓库分发；留空时按 PATH / 当前 Python 的 Scripts 目录查找 mineru-kit。
    guideline_mineru_bin: str = ""
    # true 时 create_tool("custom") 先调用解析器再加载；环境变量
    # RAG_GUIDELINE_AUTO_PARSE=1 可覆盖。GUI 的“解析”按钮逐次触发，
    # 不依赖该开关。
    guideline_auto_parse: bool = False
    # 新生儿演示档案（与 rag/ 同级的 data/）。默认不把演示指南库注册进服务：
    # 工作台显式调用 POST /api/demo/guidelines/load 才登记
    # data/neonatal_guidelines.json；demo_autoload_guidelines=True 时
    # 由运维在启动阶段显式预载（等价于一次加载动作，响应中会记录事件）。
    demo_profile: str = "neonatal_demo_v1"
    demo_autoload_guidelines: bool = False
    # 查询保真研究层：可选词表路径（FidelityLexicon JSON，见
    # contracts/examples/fidelity_lexicon_example.json；许可由调用方负责）与
    # 默认门控策略（off/audit/warn/downweight/reject；请求可逐次覆盖）。
    fidelity_lexicon_path: str = ""
    fidelity_default_policy: str = "off"
    fidelity_penalty: float = 0.5
    # 可选术语映射库（通用机制，v6.7.0）：概念—英文/MeSH 映射行的可复用文件
    # （{"library": name, "rows": [...]} 或行数组；构建工具 tools/build_term_mapping_library.py）。
    # 配置一次对所有请求生效（概念/别名命中才展开）；留空=不启用（legacy）。
    # 环境变量 RAG_TERM_MAPPING_LIBRARY 可覆盖；文件缺失/损坏显式报错。
    term_mapping_library_path: str = ""
    # 重排器选择：medcpt（默认，MedCPT cross-encoder）| qwen3（Qwen3-Reranker-0.6B，
    # 本地 HF 缓存权重；多语言，中文问题可见）。默认值保持 legacy 语义。
    reranker_model: str = "medcpt"
    # 重排候选池：0=legacy（重排只作用于联合选择后的 top_k）；>0 时按池大小收集并
    # 联合选择候选，重排后再截断到请求 top_k。仅当请求 rerank=true 时生效。
    rerank_pool: int = 0
    # 适用性试算预览：true 时给"未参与判定"的条件（未审核/缺出处）附加
    # preview 字段——用同一比对规则试算"若审过会判什么"。status 不变，
    # 预览不构成判定。环境变量 RAG_APPLICABILITY_PREVIEW=1 可覆盖。
    applicability_preview: bool = False
    # 规则路由 v0（研究特性）：off=legacy；shadow=只记录 trace.route_decision
    # 不改检索行为（实验性运行）；active=按决策收窄/重排来源。
    # 环境变量 RAG_ROUTING_MODE 可覆盖。
    routing_mode: str = "off"
    # MeSH 实体链接：本地 MeSH 词表快照（tools/build_mesh_lexicon.py 构建）。
    # 留空=未配置；mode=off 时为 legacy。shadow 只把链接/映射核验写入
    # trace.entity_linking，不改变查询；active 才把已核验的官方 MeSH 标签与
    # 少量 entry terms 加入查询。环境变量 RAG_MESH_LEXICON / RAG_ENTITY_LINKING_MODE
    # / RAG_ENTITY_LINKING_SEMANTIC 可覆盖。
    mesh_lexicon_path: str = _DEFAULT_MESH_LEXICON
    entity_linking_mode: str = "active"
    # true 时启用 BGE-M3 语义候选打分（真实本地权重；不可用显式 not_configured）。
    entity_linking_semantic: bool = True
    # 语义候选覆盖的 MeSH 表面形式：all=官方标签+入口词；label=仅 preferred label
    # （全量 MeSH 上 all 需要较大内存/编码时间，可按部署预算显式选择）。
    entity_linking_semantic_surfaces: str = "all"
    # 词面候选上限与每个描述符最多加入的官方 entry terms；可按精度/成本调整。
    entity_linking_max_token_candidates: int = 200
    entity_linking_max_entry_terms: int = 2
    # 指南生命周期治理：从 PubMed efetch 的 CommentsCorrectionsList /
    # PublicationTypeList 读取撤回、更新、勘误与关注声明。off=legacy；shadow 只写
    # trace.guideline_lifecycle；active 才把截止时间前已撤回/已更新的指南映射为
    # withdrawn/superseded，交给既有排序前状态过滤。cache_path 留空=不缓存。
    # 环境变量 RAG_GUIDELINE_LIFECYCLE_MODE / RAG_GUIDELINE_LIFECYCLE_CACHE /
    # RAG_GUIDELINE_LIFECYCLE_TTL_DAYS 可覆盖。
    guideline_lifecycle_mode: str = "active"
    guideline_lifecycle_cache_path: str = _DEFAULT_LIFECYCLE_CACHE
    guideline_lifecycle_ttl_days: float = 7.0


CONFIG = RAGConfig()


def get_config() -> RAGConfig:
    return CONFIG
