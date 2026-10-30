"""Thin adapter to Medical-Assistant's existing Qwen-backed medical agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
AGENT_ROOT = REPO_ROOT / "agent"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-vl-plus"


class MedicalAgentSession:
    def __init__(
        self,
        library_path: Path,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        if not api_key.strip():
            raise ValueError("请输入 DashScope API Key。")
        if not library_path.is_file():
            raise FileNotFoundError(f"指南知识库不存在：{library_path}")

        os.environ.update({
            "OPENAI_API_KEY": api_key.strip(),
            "OPENAI_BASE_URL": base_url.strip() or DEFAULT_BASE_URL,
            "OPENAI_MODEL": model.strip() or DEFAULT_MODEL,
            "RAG_SOURCE_MODE": "custom",
            "RAG_CUSTOM_SOURCES_FILE": str(library_path.resolve()),
            "MEDRAX_AGENT_MODE": "medical_evidence",
            "ANSWER_AUDIT_MODE": "observe",
            "HIS_BASE_URL": "http://127.0.0.1:18080",
        })
        for import_root in (REPO_ROOT, AGENT_ROOT):
            if str(import_root) not in sys.path:
                sys.path.insert(0, str(import_root))

        from medrax.tools.rag_tool import _get_rag

        _get_rag.cache_clear()
        import main as medical_assistant_main

        prompt_path = AGENT_ROOT / "medrax" / "docs" / "system_prompts.txt"
        self.agent, self.tools = medical_assistant_main.initialize_medical_agent(prompt_path)
        self.thread_id = uuid4().hex
        self.library_path = library_path.resolve()

    def ask(self, question: str) -> dict:
        if not question.strip():
            raise ValueError("请输入问题。")
        events = []
        response = self.agent.run_turn(question.strip(), self.thread_id, emit=events.append)
        return {**response, "events": events}
