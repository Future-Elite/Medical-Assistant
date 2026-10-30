"""Local configuration for the RAG retrieval package."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RAGConfig:
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""
    openai_api_mode: str = "auto"
    ncbi_email: str = ""
    ncbi_api_key: str = ""
    # 兼容字段（agent 侧仍读取）：MiniCheck 模型名与缓存目录；留空=不启用。
    minicheck_model: str = ""
    minicheck_cache_dir: str = ""
    request_timeout_seconds: float = 60.0
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
    # 指南 PDF 目录（仓库外，调用方自备）：<dir>/guideline_pdfs.json + PDF。
    # 命中时作为 guideline-pdf-local 连接器注册；缺失时该来源自动缺席。
    guideline_pdf_dir: str = "~/.chinallm/guideline_pdfs"
    # 指南书目元数据实时查询（Europe PMC 按 DOI/PMID）：失败时回退本地注册表。
    allow_live_guideline_metadata_lookup: bool = True
    # 可选：启动时预载自定义来源库文件（{"sources": [...]} 或 JSON 数组；
    # 单个来源的 schema 见 custom_sources.py 与 contracts/README.md）。
    # 留空表示不预载，调用方在运行时用 POST /api/sources/custom 注册。
    custom_sources_path: str = ""
    # 新生儿演示档案（rag/demo/）。默认不把演示指南库注册进服务：
    # 工作台显式调用 POST /api/demo/guidelines/load 才登记
    # rag/demo/neonatal_guidelines.json；demo_autoload_guidelines=True 时
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
    # （{"library": name, "rows": [...]} 或行数组；构建工具不随本分支发布）。
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


CONFIG = RAGConfig()


def get_config() -> RAGConfig:
    return CONFIG
