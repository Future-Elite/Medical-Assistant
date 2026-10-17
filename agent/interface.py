import ast
import asyncio
import base64
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
                    content = json.dumps(artifact, ensure_ascii=False, default=str)
                    history.append(ChatMessage(role="assistant", content=content, metadata={"title": f"🔎 {event.get('tool_name', kind)}"}))
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

    with gr.Blocks() as demo:
        with gr.Column():
            gr.Markdown(
                """
            # 🏥 Medical Assistant
            A Medical Reasoning Agent with unified medical evidence RAG
            """
            )

            with gr.Row():
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        [],
                        height=800,
                        container=True,
                        show_label=True,
                        elem_classes="chat-box",
                        label="Agent",
                        avatar_images=(
                            None,
                            "assets/medrax_logo.jpg",
                        ),
                    )
                    with gr.Row():
                        with gr.Column(scale=3):
                            txt = gr.Textbox(
                                show_label=False,
                                placeholder="Ask a medical question...",
                                container=False,
                            )

                with gr.Column(scale=3):
                    image_display = gr.Image(
                        label="Image", type="filepath", height=700, container=True,
                        visible=not medical_mode,
                    )
                    with gr.Row():
                        upload_button = gr.UploadButton(
                            "📎 Upload X-Ray",
                            file_types=["image"],
                            visible=not medical_mode,
                        )
                        dicom_upload = gr.UploadButton(
                            "📄 Upload DICOM",
                            file_types=["file"],
                            visible=not medical_mode,
                        )
                    with gr.Row():
                        clear_btn = gr.Button("Clear Chat")
                        new_thread_btn = gr.Button("New Thread")
                    patient_ref = gr.Textbox(label="合成 HIS 患者 ID（可留空）", placeholder="例如 2001；切换患者会隔离对话记忆和引用")
                    as_of = gr.Textbox(label="事实和证据截止日期（可留空，默认今天）", placeholder="YYYY-MM-DD")

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
