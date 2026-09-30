"""Local configuration for RAG.

Edit the values in this file before starting the server. Keep this file local
and never commit real API keys or other credentials.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RAGConfig:
    # OpenAI-compatible Responses API
    openai_api_key: str = ""
    openai_model: str = ""
    # Enter the provider API root. If the URL has no path, /v1 is added.
    openai_base_url: str = ""
    # auto = Responses API first; set chat_completions for relays that expose only /chat/completions.
    openai_api_mode: str = "auto"

    # NCBI E-utilities etiquette and optional access key
    ncbi_email: str = ""
    ncbi_api_key: str = ""

    # Optional MiniCheck verifier
    minicheck_model: str = ""
    minicheck_cache_dir: str = ""

    # Local HTTP service
    server_host: str = "127.0.0.1"
    server_port: int = 8788
    request_timeout_seconds: float = 60.0


# Put local values directly above. This is the single configuration source for the service.
CONFIG = RAGConfig()


def get_config() -> RAGConfig:
    return CONFIG
