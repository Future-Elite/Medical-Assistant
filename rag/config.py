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
    server_port: int = 8788
    source_mode: str = "simulated"
    allow_live_pubmed: bool = True
    allow_live_guidelines: bool = True


CONFIG = RAGConfig()


def get_config() -> RAGConfig:
    return CONFIG
