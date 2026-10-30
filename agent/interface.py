import ast
import asyncio
import base64
from collections import Counter
from html import escape
import json
import re
import gradio as gr
from datetime import datetime, timezone
from pathlib import Path
import time
import shutil
from uuid import uuid4
from queue import Queue, Empty
from typing import AsyncGenerator, List, Optional, Tuple
from gradio import ChatMessage

from medrax.answer_audit import AnswerAuditModule


MEDICAL_UI_CSS = r"""
:root {
    --med-primary: #087f8c;
    --med-primary-strong: #075f69;
    --med-primary-soft: #e9f7f6;
    --med-navy: #173746;
    --med-text: #203640;
    --med-muted: #617680;
    --med-bg: #f4f8f9;
    --med-surface: #ffffff;
    --med-border: #d7e4e7;
    --med-focus: #0e8391;
    --med-pubmed: #23689b;
    --med-guideline: #28784a;
    --med-his: #6f52a2;
    --med-audit: #8a6414;
    --med-error: #a43b3b;
    --med-shadow: 0 12px 32px rgba(25, 65, 78, 0.08);
}

html, body {
    width: 100%;
    height: 100%;
    margin: 0;
    overflow: hidden;
}

*, *::before, *::after { box-sizing: border-box; }

.gradio-container {
    width: 100%;
    height: 100dvh !important;
    min-height: 0 !important;
    max-height: 100dvh !important;
    max-width: none !important;
    padding: 0 !important;
    overflow: hidden !important;
    background: var(--med-bg) !important;
    color: var(--med-text);
    font-family: Inter, "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif;
}

#medical-sidebar {
    height: 100dvh !important;
    max-height: 100dvh !important;
    overflow: visible !important;
    background: #eef6f6 !important;
    border-right: 1px solid var(--med-border) !important;
    padding: 20px 16px !important;
}

#medical-sidebar .sidebar-content {
    height: 100%;
    overflow-y: auto;
    overscroll-behavior: contain;
}

.medical-brand {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 2px 2px 22px;
}

.medical-brand-mark {
    display: grid;
    width: 40px;
    height: 40px;
    place-items: center;
    border-radius: 12px;
    background: var(--med-primary);
    color: #ffffff;
    font-weight: 700;
    letter-spacing: 0.02em;
    box-shadow: 0 6px 16px rgba(8, 127, 140, 0.18);
}

.medical-brand-title {
    color: var(--med-navy);
    font-size: 17px;
    font-weight: 700;
    line-height: 1.25;
}

.medical-brand-subtitle,
.medical-sidebar-note {
    color: var(--med-muted);
    font-size: 12px;
    line-height: 1.55;
}

.medical-sidebar-section {
    margin: 18px 2px 8px;
    color: var(--med-muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.medical-session-item {
    margin-bottom: 16px;
    padding: 12px 13px;
    border: 1px solid #c9dedf;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.72);
    color: var(--med-navy);
    font-size: 14px;
    font-weight: 600;
}

.medical-session-item span {
    display: block;
    margin-top: 3px;
    color: var(--med-muted);
    font-size: 11px;
    font-weight: 400;
}

#medical-sidebar button,
#medical-composer button {
    min-height: 44px;
    cursor: pointer;
}

#medical-sidebar button.primary {
    border-color: var(--med-primary) !important;
    background: var(--med-primary) !important;
    color: #ffffff !important;
}

#medical-sidebar label,
#medical-sidebar .label-wrap {
    color: var(--med-navy) !important;
    font-weight: 600 !important;
}

#medical-sidebar input,
#medical-sidebar textarea {
    border-color: #c9dcdf !important;
    background: rgba(255, 255, 255, 0.92) !important;
}

#medical-main {
    display: flex !important;
    flex-direction: column !important;
    min-width: 0;
    height: 100dvh !important;
    min-height: 0;
    max-height: 100dvh;
    overflow: hidden;
    gap: 0 !important;
    background: var(--med-surface);
}

.medical-topbar {
    display: flex;
    flex: 0 0 auto;
    min-height: 72px;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 28px;
    border-bottom: 1px solid var(--med-border);
    background: rgba(255, 255, 255, 0.96);
}

.medical-topbar h1 {
    margin: 0;
    color: var(--med-navy);
    font-size: 18px;
    font-weight: 700;
}

.medical-topbar p {
    margin: 3px 0 0;
    color: var(--med-muted);
    font-size: 12px;
}

.medical-status {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    flex-shrink: 0;
    padding: 7px 10px;
    border: 1px solid #b9dbd8;
    border-radius: 999px;
    background: var(--med-primary-soft);
    color: var(--med-primary-strong);
    font-size: 12px;
    font-weight: 650;
}

.medical-status::before {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--med-primary);
    content: "";
}

#medical-chatbot {
    flex: 1 1 0;
    height: auto !important;
    min-height: 0 !important;
    overflow: hidden !important;
    border: 0 !important;
    border-radius: 0 !important;
    background: var(--med-surface) !important;
}

#medical-chatbot .message {
    max-width: min(860px, calc(100% - 40px));
    overflow-wrap: anywhere;
    line-height: 1.7;
}

#medical-chatbot .message.user {
    border: 1px solid #c6dddd;
    background: #eef7f6 !important;
    color: var(--med-text) !important;
}

#medical-composer {
    position: relative;
    z-index: 20;
    flex: 0 0 auto;
    margin: 0 !important;
    padding: 12px max(24px, calc((100% - 900px) / 2)) 14px;
    border: 0 !important;
    border-top: 1px solid rgba(215, 228, 231, 0.9) !important;
    border-radius: 0 !important;
    background: rgba(250, 252, 252, 0.97) !important;
    box-shadow: 0 -12px 28px rgba(33, 70, 82, 0.05);
}

#medical-input textarea {
    min-height: 52px !important;
    padding: 14px 16px !important;
    border-color: #bdd2d6 !important;
    border-radius: 16px !important;
    background: #ffffff !important;
    color: var(--med-text) !important;
    font-size: 16px !important;
    line-height: 1.5 !important;
    box-shadow: var(--med-shadow);
}

#medical-input textarea:focus,
#medical-sidebar input:focus,
#medical-sidebar textarea:focus,
#medical-sidebar button:focus-visible,
#medical-composer button:focus-visible {
    outline: 3px solid rgba(14, 131, 145, 0.3) !important;
    outline-offset: 2px !important;
}

.medical-disclaimer {
    margin: 8px 8px 0;
    color: var(--med-muted);
    font-size: 11px;
    line-height: 1.45;
    text-align: center;
}

.medical-event-card {
    margin: 4px 0;
    padding: 12px 14px;
    border: 1px solid var(--med-border);
    border-left: 4px solid var(--med-primary);
    border-radius: 10px;
    background: #f8fbfb;
    color: var(--med-text);
}

.medical-event-card--his { border-left-color: var(--med-his); }
.medical-event-card--error { border-left-color: var(--med-error); background: #fff7f7; }
.medical-event-card--citation { border-left-color: var(--med-guideline); }

.medical-event-summary { margin-bottom: 6px; font-weight: 650; }

.medical-event-meta {
    color: var(--med-muted);
    font-size: 12px;
    line-height: 1.55;
}

.medical-event-card details {
    margin-top: 9px;
    color: var(--med-muted);
    font-size: 12px;
}

.medical-event-card summary { cursor: pointer; font-weight: 600; }

.medical-event-card pre {
    max-height: 260px;
    margin-top: 8px;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

.source-badge {
    display: inline-block;
    margin: 5px 6px 0 0;
    padding: 3px 7px;
    border-radius: 999px;
    background: #e9eff1;
    color: var(--med-navy);
    font-size: 11px;
    font-weight: 700;
}

.source-badge--pubmed { background: #eaf3fb; color: var(--med-pubmed); }
.source-badge--guideline { background: #eaf6ee; color: var(--med-guideline); }
.source-badge--his { background: #f1ecf9; color: var(--med-his); }
.source-badge--audit { background: #fff5d9; color: var(--med-audit); }

@media (max-width: 768px) {
    .medical-topbar { min-height: 64px; padding: 12px 16px; }
    .medical-topbar p { display: none; }
    #medical-chatbot .message { max-width: calc(100% - 20px); }
    #medical-composer {
        padding: 10px 12px max(12px, env(safe-area-inset-bottom));
    }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
    }
}
"""


class ChatInterface:
    """
    A chat interface for interacting with a medical AI agent through Gradio.

    Handles file uploads, message processing, and chat history management.
    Supports both regular image files and DICOM medical imaging files.
    """

    def __init__(self, agent, tools_dict):
        """
        Initialize the chat interface.

        Args:
            agent: The medical AI agent to handle requests
            tools_dict (dict): Dictionary of available tools for image processing
        """
        self.agent = agent
        self.workflow = getattr(agent, "workflow", agent)
        self.tools_dict = tools_dict
        self.upload_dir = Path("temp")
        self.upload_dir.mkdir(exist_ok=True)
        self.audit_path = Path("logs/evidence_audit.jsonl")
        self.current_thread_id = self._new_id()
        self.thread_verified_ids = {}
        self.thread_retrieval_context = {}
        rag_tool = next(
            (
                tool for tool in tools_dict.values()
                if hasattr(tool, "audit_grounded_answer")
            ),
            None,
        )
        self.answer_auditor = (
            AnswerAuditModule(
                rag_tool.audit_grounded_answer,
                rag_tool.translate_claims,
            )
            if rag_tool and hasattr(rag_tool, "translate_claims")
            else None
        )
        # Separate storage for original and display paths
        self.original_file_path = None  # For LLM (.dcm or other)
        self.display_file_path = None  # For UI (always viewable format)

    @staticmethod
    def _new_id() -> str:
        return uuid4().hex

    def reset_conversation(self, thread_id: Optional[str] = None) -> str:
        old_thread_id = thread_id or self.current_thread_id
        self.thread_verified_ids.pop(old_thread_id, None)
        self.thread_retrieval_context.pop(old_thread_id, None)
        new_thread_id = self._new_id()
        self.current_thread_id = new_thread_id
        self.original_file_path = None
        self.display_file_path = None
        if hasattr(self.agent, "reset"):
            self.agent.reset(old_thread_id)
        return new_thread_id

    @staticmethod
    def _extract_citation_ids(text: str) -> set[str]:
        identifiers = set()
        pmid_groups = re.findall(
            r"\bPMIDs?\s*[:：#]?\s*(\d{6,9}(?:\s*(?:[,，、;/]|和|and)\s*\d{6,9})*)",
            text,
            re.I,
        )
        identifiers.update(
            f"PMID:{pmid}"
            for group in pmid_groups
            for pmid in re.findall(r"\d{6,9}", group)
        )
        identifiers.update(
            f"{prefix.upper().replace(' ', '_')}:{value.lower()}"
            for prefix, value in re.findall(
                r"\b(FDA_SET_ID|SPL_ID|SPL ID)\s*[:：#]?\s*([0-9a-f-]{8,64})",
                text,
                re.I,
            )
        )
        identifiers.update(match.upper() for match in re.findall(r"\bNCT\d{8}\b", text, re.I))
        identifiers.update(
            f"RAG_CITATION:{value.lower()}"
            for value in re.findall(
                r"\bRAG_CITATION\s*[:：#]\s*([A-Za-z0-9._:/-]{1,160})",
                text,
                re.I,
            )
        )
        return identifiers

    @staticmethod
    def _artifact_citation_ids(artifact: dict) -> set[str]:
        identifiers = set()
        for item in artifact.get("evidence", []):
            source_id = str(item.get("source_id") or "")
            if source_id:
                prefix, separator, value = source_id.partition(":")
                identifiers.add(
                    f"{prefix.upper()}:{value.lower()}" if separator else source_id.upper()
                )
            if spl_id := item.get("metadata", {}).get("spl_id"):
                identifiers.add(f"SPL_ID:{str(spl_id).lower()}")
        return identifiers

    @staticmethod
    def _tool_call_arguments(tool_call: dict) -> dict:
        args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
        if not isinstance(args, dict):
            return {}
        return json.loads(json.dumps(args, ensure_ascii=False, default=str))

    @staticmethod
    def _format_medical_event(kind: str, artifact: dict) -> tuple[str, str]:
        """Render tool output as a concise card while preserving inspectable raw data."""
        artifact = artifact if isinstance(artifact, dict) else {"result": artifact}
        raw = escape(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
        card_class = "medical-event-card"
        title = "工具执行"
        summary = "工具调用已完成。"
        meta_lines = []
        badges = []

        if kind == "evidence_search":
            title = "医学证据检索"
            evidence = artifact.get("evidence") or []
            source_counts = Counter(
                str(item.get("source_type") or "unknown").lower()
                for item in evidence
                if isinstance(item, dict)
            )
            summary = f"检索完成，共获得 {len(evidence)} 条候选证据。"
            query = artifact.get("query") or (artifact.get("tool_args") or {}).get("question")
            if query:
                meta_lines.append(f"检索问题：{escape(str(query))}")
            if artifact.get("retrieval_id"):
                meta_lines.append(f"检索批次：{escape(str(artifact['retrieval_id']))}")
            badge_labels = {
                "pubmed": ("PubMed", "pubmed"),
                "guideline": ("指南", "guideline"),
                "who": ("WHO 指南", "guideline"),
            }
            for source, count in sorted(source_counts.items()):
                label, badge_class = badge_labels.get(source, (source.upper(), "audit"))
                badges.append(
                    f'<span class="source-badge source-badge--{badge_class}">'
                    f"{escape(label)} {count}</span>"
                )
        elif kind == "citation_opened":
            title = "引用详情"
            card_class += " medical-event-card--citation"
            summary = "已加载引用原文或详细元数据。"
            citation_id = artifact.get("citation_id") or artifact.get("source_id")
            if citation_id:
                meta_lines.append(f"引用：{escape(str(citation_id))}")
            badges.append('<span class="source-badge source-badge--guideline">引用</span>')
        elif kind == "his_snapshot":
            title = "HIS 患者快照"
            card_class += " medical-event-card--his"
            summary = "已读取当前患者的结构化临床记录。"
            patient_ref = artifact.get("patient_ref") or artifact.get("patient_id")
            if patient_ref:
                meta_lines.append(f"患者标识：{escape(str(patient_ref))}")
            badges.append('<span class="source-badge source-badge--his">HIS</span>')
        elif kind == "his_error":
            title = "HIS 连接异常"
            card_class += " medical-event-card--error"
            summary = escape(str(
                artifact.get("error") or artifact.get("message") or "未能读取患者记录。"
            ))
            badges.append('<span class="source-badge source-badge--his">HIS</span>')

        metadata = "<br>".join(meta_lines)
        details = (
            "<details><summary>查看原始详情</summary>"
            f"<pre>{raw}</pre></details>"
        )
        content = (
            f'<div class="{card_class}">'
            f'<div class="medical-event-summary">{summary}</div>'
            f'<div class="medical-event-meta">{metadata}</div>'
            f'{"".join(badges)}{details}</div>'
        )
        return title, content

    def _append_audit(self, event: dict, thread_id: Optional[str] = None) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id or self.current_thread_id,
            **event,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _revise_draft(self, prompt: str, thread_id: str) -> Optional[str]:
        revised_draft = None
        called_tool = False
        for event in self.workflow.stream(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            if not isinstance(event, dict):
                continue
            model_node = "process" if "process" in event else "model"
            if model_node not in event:
                continue
            model_message = event[model_node]["messages"][-1]
            if getattr(model_message, "tool_calls", None):
                called_tool = True
                continue
            if model_message.content:
                revised_draft = str(model_message.content)
        return None if called_tool else revised_draft

    def handle_upload(self, file_path: str) -> str:
        """
        Handle new file upload and set appropriate paths.

        Args:
            file_path (str): Path to the uploaded file

        Returns:
            str: Display path for UI, or None if no file uploaded
        """
        if not file_path:
            return None

        source = Path(file_path)
        timestamp = int(time.time())

        # Save original file with proper suffix
        suffix = source.suffix.lower()
        saved_path = self.upload_dir / f"upload_{timestamp}{suffix}"
        shutil.copy2(file_path, saved_path)  # Use file_path directly instead of source
        self.original_file_path = str(saved_path)

        # Handle DICOM conversion for display only
        if suffix == ".dcm":
            dicom_tool = self.tools_dict.get("DicomProcessorTool")
            if dicom_tool is None:
                raise ValueError("DICOM processing is not available in the current agent mode")
            output, _ = dicom_tool._run(str(saved_path))
            self.display_file_path = output["image_path"]
        else:
            self.display_file_path = str(saved_path)

        return self.display_file_path

    def add_message(
        self, message: str, display_image: str, history: List[dict]
    ) -> Tuple[List[dict], gr.Textbox]:
        """
        Add a new message to the chat history.

        Args:
            message (str): Text message to add
            display_image (str): Path to image being displayed
            history (List[dict]): Current chat history

        Returns:
            Tuple[List[dict], gr.Textbox]: Updated history and textbox component
        """
        image_path = self.original_file_path or display_image
        if image_path is not None:
            history.append({"role": "user", "content": {"path": image_path}})
        if message is not None:
            history.append({"role": "user", "content": message})
        return history, gr.Textbox(value=message, interactive=False)

    async def process_message(
        self,
        message: str,
        display_image: Optional[str],
        chat_history: List[ChatMessage],
        thread_id: Optional[str] = None,
        patient_ref: str = "",
        as_of: Optional[str] = None,
    ) -> AsyncGenerator[Tuple[List[ChatMessage], Optional[str], str], None]:
        """
        Process a message and generate responses.

        Args:
            message (str): User message to process
            display_image (Optional[str]): Path to currently displayed image
            chat_history (List[ChatMessage]): Current chat history

        Yields:
            Tuple[List[ChatMessage], Optional[str], str]: Updated chat history, display path, and empty string
        """
        chat_history = chat_history or []
        if hasattr(self.agent, "run_turn"):
            async for item in self._process_medical_turn(message, chat_history, thread_id, patient_ref, as_of):
                yield item
            return
        thread_id = thread_id or self.current_thread_id

        turn_id = self._new_id()
        turn_tool_calls = {}
        turn_tool_args = {}
        verified_ids = self.thread_verified_ids.setdefault(thread_id, set())
        previous_context = self.thread_retrieval_context.get(thread_id, {})
        retrieval_id = previous_context.get("retrieval_id")
        citation_map = dict(previous_context.get("citation_map") or {})
        draft = None
        messages = []
        image_path = self.original_file_path or display_image

        if image_path is not None:
            # Send path for tools
            messages.append({"role": "user", "content": f"image_path: {image_path}"})

            # Load and encode image for multimodal
            with open(image_path, "rb") as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode("utf-8")

            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
                        }
                    ],
                }
            )

        if message is not None:
            messages.append({"role": "user", "content": [{"type": "text", "text": message}]})

        try:
            for event in self.workflow.stream(
                {"messages": messages},
                {"configurable": {"thread_id": thread_id}},
                stream_mode="updates",
            ):
                if isinstance(event, dict):
                    model_node = "process" if "process" in event else "model"
                    tool_node = "execute" if "execute" in event else "tools"

                    if model_node in event:
                        model_message = event[model_node]["messages"][-1]
                        content = model_message.content
                        # Tool-call messages are internal plans, not user-facing answers.
                        if getattr(model_message, "tool_calls", None):
                            for tool_call in model_message.tool_calls:
                                tool_call_id = str(tool_call.get("id") or "")
                                if tool_call_id:
                                    turn_tool_args[tool_call_id] = self._tool_call_arguments(
                                        tool_call
                                    )
                            continue
                        if content:
                            draft = re.sub(r"temp/[^\s]*", "", str(content))

                    elif tool_node in event:
                        for message in event[tool_node]["messages"]:
                            tool_name = message.name
                            registered_tool_names = {
                                getattr(tool, "name", "") for tool in self.tools_dict.values()
                            }
                            if registered_tool_names and tool_name not in registered_tool_names:
                                continue
                            artifact = getattr(message, "artifact", None)
                            tool_call_id = getattr(message, "tool_call_id", None)
                            tool_result = None
                            if artifact is None:
                                try:
                                    parsed = ast.literal_eval(message.content)
                                    tool_result = parsed[0] if isinstance(parsed, tuple) else parsed
                                except (ValueError, SyntaxError):
                                    tool_result = message.content

                            if message.content:
                                formatted_result = " ".join(
                                    line.strip() for line in str(message.content).splitlines()
                                ).strip()
                                if isinstance(artifact, dict) and "evidence" in artifact:
                                    evidence = artifact.get("evidence", [])
                                    source_type = (
                                        "medical evidence RAG"
                                        if tool_name == "retrieve_medical_evidence"
                                        else evidence[0].get("source_type") if evidence else {
                                            "search_pubmed_evidence": "pubmed",
                                            "search_openfda_drug_labels": "openfda",
                                            "search_clinical_trials": "clinicaltrials",
                                        }.get(tool_name, "evidence")
                                    )
                                    turn_tool_calls[tool_name] = turn_tool_calls.get(tool_name, 0) + 1
                                    query = artifact.get("query", "")
                                    model_tool_args = turn_tool_args.get(
                                        str(tool_call_id), {}
                                    )
                                    effective_tool_args = artifact.get(
                                        "tool_args", model_tool_args
                                    )
                                    trace = artifact.get("trace") or {}
                                    retrieval_id = artifact.get("retrieval_id") or retrieval_id
                                    for item in evidence:
                                        citation_id = str(
                                            (item.get("metadata") or {}).get("citation_id") or ""
                                        )
                                        source_id = str(item.get("source_id") or "")
                                        if citation_id and source_id:
                                            citation_map[citation_id] = source_id
                                    if retrieval_id:
                                        self.thread_retrieval_context[thread_id] = {
                                            "retrieval_id": retrieval_id,
                                            "citation_map": dict(citation_map),
                                        }
                                    source_ids = sorted(self._artifact_citation_ids(artifact))
                                    if not artifact.get("error"):
                                        verified_ids.update(source_ids)
                                    metadata = {
                                        "title": (
                                            f"🔎 {source_type} 检索 "
                                            f"#{turn_tool_calls[tool_name]}"
                                        ),
                                        "description": (
                                            f"Query: {query}\n"
                                            f"Source IDs: {', '.join(source_ids) or '无'}"
                                        ),
                                    }
                                    self._append_audit({
                                        "event": "evidence_search",
                                        "turn_id": turn_id,
                                        "tool_call_id": tool_call_id,
                                        "tool_name": tool_name,
                                        "source_type": source_type,
                                        "call_index": turn_tool_calls[tool_name],
                                        "query": query,
                                        "model_tool_args": model_tool_args,
                                        "tool_args": effective_tool_args,
                                        "retrieval_id": artifact.get("retrieval_id"),
                                        "candidate_count": trace.get("candidate_count"),
                                        "selected_count": trace.get("selected_count"),
                                        "connector_trace": trace.get("connector_trace", []),
                                        "returned_source_ids": source_ids,
                                        "warnings": artifact.get("warnings", []),
                                        "error": artifact.get("error"),
                                    }, thread_id)
                                else:
                                    metadata = {
                                        "title": f"🔧 Result from tool: {tool_name}",
                                        "description": formatted_result,
                                    }
                                chat_history.append(
                                    ChatMessage(
                                        role="assistant",
                                        content=formatted_result,
                                        metadata=metadata,
                                    )
                                )

                            # For image_visualizer, use display path
                            if tool_name == "image_visualizer" and isinstance(tool_result, dict):
                                self.display_file_path = tool_result["image_path"]
                                chat_history.append(
                                    ChatMessage(
                                        role="assistant",
                                        # content=gr.Image(value=self.display_file_path),
                                        content={"path": self.display_file_path},
                                    )
                                )

                            yield chat_history, self.display_file_path, ""

            if not draft:
                return

            if self.answer_auditor:
                draft = self.answer_auditor.normalize_citations(draft)

            self._append_audit({
                "event": "answer_draft",
                "turn_id": turn_id,
                "attempt": 0,
                "retrieval_id": retrieval_id,
                "draft": draft,
            }, thread_id)

            if self.answer_auditor:
                for attempt in range(self.answer_auditor.max_revisions + 1):
                    progress_events = Queue()
                    status_message = ChatMessage(
                        role="assistant",
                        content=f"正在准备证据审计（attempt={attempt}）…",
                        metadata={"title": "证据审计进度"},
                    )
                    chat_history.append(status_message)
                    yield chat_history, self.display_file_path, ""
                    def report_progress(stage):
                        self._append_audit({
                            "event": "audit_stage",
                            "turn_id": turn_id,
                            "attempt": attempt,
                            "stage": stage,
                        }, thread_id)
                        progress_events.put(stage)
                    audit_task = asyncio.create_task(asyncio.to_thread(
                        self.answer_auditor.audit, draft, retrieval_id, report_progress
                    ))
                    labels = {
                        "translation_started": "检查主张语言；仅中文主张需要翻译…",
                        "translation_completed": "主张语言处理完成…",
                        "verification_started": "MiniCheck 正在校验主张与证据…",
                        "verification_completed": "MiniCheck 校验完成…",
                    }
                    while not audit_task.done():
                        try:
                            stage = progress_events.get_nowait()
                        except Empty:
                            await asyncio.sleep(0.1)
                            continue
                        status_message.content = labels[stage]
                        yield chat_history, self.display_file_path, ""
                    audit_result = await audit_task
                    status_message.content = (
                        f"审计通过（attempt={attempt}）"
                        if audit_result.get("passed")
                        else f"审计未通过（attempt={attempt}），{audit_result.get('status')}"
                    )
                    self._append_audit({
                        "event": "answer_audit",
                        "turn_id": turn_id,
                        "attempt": attempt,
                        "retrieval_id": retrieval_id,
                        "passed": audit_result.get("passed", False),
                        "status": audit_result.get("status"),
                        "verifier": audit_result.get("verifier"),
                        "checked_claim_count": audit_result.get("checked_claim_count", 0),
                        "failure_reasons": audit_result.get("failure_reasons", []),
                        "claim_translations": audit_result.get("claim_translations", []),
                        "timings": audit_result.get("timings", {}),
                        "claims": audit_result.get("claims", []),
                        "warnings": audit_result.get("warnings", []),
                    }, thread_id)
                    if audit_result.get("passed"):
                        break
                    if audit_result.get("status") == "error":
                        chat_history.append(ChatMessage(
                            role="assistant",
                            content="⚠️ 翻译或审计执行异常，已拦截回答。请检查审计日志后重试；此次未修改原始草稿。",
                            metadata={"title": "审计执行异常"},
                        ))
                        yield chat_history, self.display_file_path, ""
                        return
                    if attempt >= self.answer_auditor.max_revisions:
                        chat_history.append(ChatMessage(
                            role="assistant",
                            content=(
                                "⚠️ 回答草稿经过两次修改后仍未通过证据支持性审计，"
                                "已拦截输出，请转人工复核或稍后重试。"
                            ),
                            metadata={"title": "回答审计未通过"},
                        ))
                        yield chat_history, self.display_file_path, ""
                        return
                    revised = self._revise_draft(
                        self.answer_auditor.revision_prompt(audit_result, attempt + 1),
                        thread_id,
                    )
                    if not revised:
                        chat_history.append(ChatMessage(
                            role="assistant",
                            content="⚠️ 回答草稿无法安全修改，已拦截输出，请转人工复核。",
                            metadata={"title": "回答审计未通过"},
                        ))
                        yield chat_history, self.display_file_path, ""
                        return
                    draft = self.answer_auditor.normalize_citations(revised)
                    self._append_audit({
                        "event": "answer_draft",
                        "turn_id": turn_id,
                        "attempt": attempt + 1,
                        "retrieval_id": retrieval_id,
                        "draft": draft,
                    }, thread_id)

            content = (
                self.answer_auditor.render_public_citations(draft, citation_map)
                if self.answer_auditor else draft
            )
            mentioned_ids = self._extract_citation_ids(content)
            invalid_ids = sorted(mentioned_ids - verified_ids)
            if invalid_ids:
                self._append_audit({
                    "event": "citation_validation_failed",
                    "turn_id": turn_id,
                    "tool_call_counts": turn_tool_calls,
                    "invalid_source_ids": invalid_ids,
                }, thread_id)
                chat_history.append(ChatMessage(
                    role="assistant",
                    content=(
                        "⚠️ 本次回答包含未经对应工具验证的引用标识，"
                        f"已拦截：{', '.join(invalid_ids)}。请重试。"
                    ),
                    metadata={"title": "引用校验未通过"},
                ))
                yield chat_history, self.display_file_path, ""
                return

            chat_history.append(ChatMessage(role="assistant", content=content))
            if turn_tool_calls or mentioned_ids:
                self._append_audit({
                    "event": "final_response",
                    "turn_id": turn_id,
                    "tool_call_counts": turn_tool_calls,
                    "adopted_source_ids": sorted(mentioned_ids),
                }, thread_id)
            yield chat_history, self.display_file_path, ""

        except Exception as e:
            chat_history.append(
                ChatMessage(
                    role="assistant", content=f"❌ Error: {str(e)}", metadata={"title": "Error"}
                )
            )
            yield chat_history, self.display_file_path

    async def _process_medical_turn(self, message, history, thread_id, patient_ref, as_of):
        events = Queue()
        self.agent.audit_path = self.audit_path
        task = asyncio.create_task(asyncio.to_thread(
            self.agent.run_turn, message, thread_id or self.current_thread_id,
            patient_ref=patient_ref, as_of=as_of or None, emit=events.put,
        ))
        progress = ChatMessage(role="assistant", content="正在分析问题与证据需求…", metadata={"title": "Medical Agent 编排"})
        history.append(progress)
        yield history, self.display_file_path, ""
        try:
            while not task.done() or not events.empty():
                try:
                    event = events.get_nowait()
                except Empty:
                    await asyncio.sleep(0.1)
                    continue
                artifact = event.get("artifact") or {}
                kind = event.get("event")
                if kind in {"evidence_search", "citation_opened", "his_snapshot", "his_error"}:
                    title, content = self._format_medical_event(kind, artifact)
                    history.append(ChatMessage(
                        role="assistant",
                        content=content,
                        metadata={"title": title},
                    ))
                elif kind == "audit_stage":
                    progress.content = {
                        "translation_started": "语言处理：英文直接审计，仅中文主张需要翻译…",
                        "translation_completed": "语言处理完成…",
                        "verification_started": "MiniCheck 正在核对主张与证据…",
                        "verification_completed": "证据校验完成…",
                    }.get(event.get("stage"), "正在审计…")
                elif kind == "evidence_gap":
                    progress.content = "检索证据不足，Agent 正在补检索或请求补充信息…"
                elif kind == "answer_audit":
                    progress.content = f"草稿审计 attempt={event.get('attempt')}：{event.get('status')}"
                yield history, self.display_file_path, ""
            result = await task
            progress.content = f"编排结束：{result['status']}"
            history.append(ChatMessage(role="assistant", content=result["answer"]))
        except Exception as exc:
            history.append(ChatMessage(role="assistant", content=f"⚠️ {exc}", metadata={"title": "运行异常"}))
        yield history, self.display_file_path, ""


def create_demo(agent, tools_dict):
    """
    Create a Gradio demo interface for the medical AI agent.

    Args:
        agent: The medical AI agent to handle requests
        tools_dict (dict): Dictionary of available tools for image processing

    Returns:
        gr.Blocks: Gradio Blocks interface
    """
    interface = ChatInterface(agent, tools_dict)
    medical_mode = hasattr(agent, "run_turn")

    with gr.Blocks(
        title="Medical Assistant",
        css=MEDICAL_UI_CSS,
        fill_height=True,
        fill_width=True,
    ) as demo:
        with gr.Sidebar(
            open=True,
            width=292,
            elem_id="medical-sidebar",
        ):
            gr.HTML(
                """
                <div class="medical-brand">
                    <div class="medical-brand-mark" aria-hidden="true">MA</div>
                    <div>
                        <div class="medical-brand-title">Medical Assistant</div>
                        <div class="medical-brand-subtitle">医学证据与患者信息协同助手</div>
                    </div>
                </div>
                """
            )
            new_thread_btn = gr.Button("新建对话", variant="primary")
            gr.HTML(
                """
                <div class="medical-sidebar-section">当前对话</div>
                <div class="medical-session-item">
                    临时会话
                    <span>会话历史数据库将在后续版本接入</span>
                </div>
                <div class="medical-sidebar-section">患者与证据范围</div>
                """
            )
            patient_ref = gr.Textbox(
                label="患者 ID（可留空）",
                placeholder="例如 2001",
                info="切换患者会隔离对话记忆与引用。",
            )
            as_of = gr.Textbox(
                label="信息截止日期（可留空）",
                placeholder="YYYY-MM-DD",
                info="默认使用今天。",
            )
            clear_btn = gr.Button("清空当前对话", variant="secondary")
            gr.HTML(
                """
                <p class="medical-sidebar-note">
                    数据来源：HIS 患者记录、PubMed 与医学指南 RAG。工具详情可在对话卡片中展开查看。
                </p>
                """
            )
            image_display = gr.Image(
                label="医学影像预览",
                type="filepath",
                height=260,
                container=True,
                visible=not medical_mode,
            )
            with gr.Row(visible=not medical_mode):
                upload_button = gr.UploadButton(
                    "上传 X-Ray",
                    file_types=["image"],
                )
                dicom_upload = gr.UploadButton(
                    "上传 DICOM",
                    file_types=["file"],
                )

        with gr.Column(elem_id="medical-main"):
            gr.HTML(
                """
                <header class="medical-topbar">
                    <div>
                        <h1>临床问题与证据对话</h1>
                        <p>Agent 自主编排 HIS 与医学证据检索，工具详情全程可追溯</p>
                    </div>
                    <div class="medical-status" role="status">系统就绪</div>
                </header>
                """
            )
            chatbot = gr.Chatbot(
                [],
                height="100%",
                min_height=0,
                container=False,
                show_label=False,
                elem_id="medical-chatbot",
                layout="bubble",
                buttons=["copy"],
                feedback_options=None,
                avatar_images=(None, "assets/medrax_logo.jpg"),
                placeholder=(
                    "### 您好，我是 Medical Assistant\n"
                    "请输入临床问题；如需结合患者信息，请先在左侧填写患者 ID。"
                ),
            )
            with gr.Group(elem_id="medical-composer"):
                txt = gr.Textbox(
                    show_label=False,
                    placeholder="输入医学问题，Enter 发送，Shift + Enter 换行",
                    container=False,
                    lines=1,
                    max_lines=8,
                    autofocus=True,
                    submit_btn="发送",
                    stop_btn="停止",
                    elem_id="medical-input",
                )
                gr.HTML(
                    '<p class="medical-disclaimer">'
                    "回答用于医学信息辅助，不替代临床医生判断；患者相关回答应结合完整病历核验。"
                    "</p>"
                )

        thread_state = gr.State(value=interface._new_id())

        # Event handlers
        def clear_chat(thread_id):
            new_thread_id = interface.reset_conversation(thread_id)
            return [], None, new_thread_id

        def new_thread(thread_id):
            new_thread_id = interface.reset_conversation(thread_id)
            return [], None, new_thread_id

        def handle_file_upload(file):
            return interface.handle_upload(file.name)

        chat_msg = txt.submit(
            interface.add_message, inputs=[txt, image_display, chatbot], outputs=[chatbot, txt]
        )
        bot_msg = chat_msg.then(
            interface.process_message,
            inputs=[txt, image_display, chatbot, thread_state, patient_ref, as_of],
            outputs=[chatbot, image_display, txt],
        )
        bot_msg.then(lambda: gr.Textbox(interactive=True), None, [txt])

        upload_button.upload(handle_file_upload, inputs=upload_button, outputs=image_display)

        dicom_upload.upload(handle_file_upload, inputs=dicom_upload, outputs=image_display)

        clear_btn.click(
            clear_chat,
            inputs=[thread_state],
            outputs=[chatbot, image_display, thread_state],
        )
        new_thread_btn.click(
            new_thread,
            inputs=[thread_state],
            outputs=[chatbot, image_display, thread_state],
        )

    return demo
