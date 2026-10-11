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
    minicheck_model: str = ""
    minicheck_cache_dir: str = ""
    request_timeout_seconds: float = 60.0
    server_host: str = "127.0.0.1"
    server_port: int = 8797
    # 来源模式仅两种：live（实时 PubMed + 官方指南元数据）与 custom（调用方注册的数据库）。
    # 服务不内置任何本地语料，也没有 offline/simulated 模式。
    source_mode: str = "live"
    allow_live_pubmed: bool = True
    allow_live_guidelines: bool = True
    # 可选：启动时预载自定义来源库文件（{"sources": [...]} 或 JSON 数组；
    # 单个来源的 schema 见 custom_sources.py 与 contracts/README.md）。
    # 留空表示不预载，调用方在运行时用 POST /api/sources/custom 注册。
    custom_sources_path: str = ""
    # 查询保真研究层：可选词表路径（FidelityLexicon JSON，见
    # contracts/examples/fidelity_lexicon_example.json；许可由调用方负责）与
    # 默认门控策略（off/audit/warn/downweight/reject；请求可逐次覆盖）。
    fidelity_lexicon_path: str = ""
    fidelity_default_policy: str = "off"
    fidelity_penalty: float = 0.5


CONFIG = RAGConfig()


def get_config() -> RAGConfig:
    return CONFIG
