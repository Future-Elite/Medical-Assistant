import ast
import base64
import json
import re
import gradio as gr
from datetime import datetime, timezone
from pathlib import Path
import time
import shutil
from uuid import uuid4
from typing import AsyncGenerator, List, Optional, Tuple
from gradio import ChatMessage


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
        self.audit_path = Path("logs/pubmed_audit.jsonl")
        self.current_thread_id = self._new_id()
        self.pubmed_call_count = 0
        self.verified_pmids = set()
        # Separate storage for original and display paths
        self.original_file_path = None  # For LLM (.dcm or other)
        self.display_file_path = None  # For UI (always viewable format)

    @staticmethod
    def _new_id() -> str:
        return uuid4().hex

    def reset_conversation(self) -> None:
        self.current_thread_id = self._new_id()
        self.pubmed_call_count = 0
        self.verified_pmids.clear()
        self.original_file_path = None
        self.display_file_path = None

    @staticmethod
    def _extract_pmids(text: str) -> set[str]:
        groups = re.findall(
            r"\bPMIDs?\s*[:：#]?\s*(\d{6,9}(?:\s*(?:[,，、;/]|和|and)\s*\d{6,9})*)",
            text,
            re.I,
        )
        return {pmid for group in groups for pmid in re.findall(r"\d{6,9}", group)}

    def _unverified_pmids(self, text: str) -> list[str]:
        return sorted(self._extract_pmids(text) - self.verified_pmids)

    def _append_audit(self, event: dict) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thread_id": self.current_thread_id,
            **event,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

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
        self, message: str, display_image: Optional[str], chat_history: List[ChatMessage]
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

        turn_id = self._new_id()
        turn_pubmed_calls = 0
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
                {"configurable": {"thread_id": self.current_thread_id}},
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
                            continue
                        if content:
                            content = re.sub(r"temp/[^\s]*", "", content)
                            mentioned_pmids = self._extract_pmids(content)
                            invalid_pmids = self._unverified_pmids(content)
                            if invalid_pmids:
                                self._append_audit({
                                    "event": "citation_validation_failed",
                                    "turn_id": turn_id,
                                    "pubmed_call_count": turn_pubmed_calls,
                                    "invalid_pmids": invalid_pmids,
                                })
                                chat_history.append(ChatMessage(
                                    role="assistant",
                                    content=(
                                        "⚠️ 本次回答包含未经 PubMed 工具验证的 PMID，"
                                        f"已拦截：{', '.join(invalid_pmids)}。请重试。"
                                    ),
                                    metadata={"title": "引用校验未通过"},
                                ))
                                yield chat_history, self.display_file_path, ""
                                continue

                            chat_history.append(ChatMessage(role="assistant", content=content))
                            if turn_pubmed_calls or mentioned_pmids:
                                self._append_audit({
                                    "event": "final_response",
                                    "turn_id": turn_id,
                                    "pubmed_call_count": turn_pubmed_calls,
                                    "adopted_pmids": sorted(mentioned_pmids),
                                })
                            yield chat_history, self.display_file_path, ""

                    elif tool_node in event:
                        for message in event[tool_node]["messages"]:
                            tool_name = message.name
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
                                if tool_name == "search_pubmed_evidence" and isinstance(artifact, dict):
                                    turn_pubmed_calls += 1
                                    self.pubmed_call_count += 1
                                    query = artifact.get("query", "")
                                    pmids = [
                                        str(record["pmid"])
                                        for record in artifact.get("records", [])
                                        if record.get("pmid")
                                    ]
                                    self.verified_pmids.update(pmids)
                                    metadata = {
                                        "title": f"🔎 PubMed检索 #{turn_pubmed_calls}",
                                        "description": f"Query: {query}\nPMIDs: {', '.join(pmids) or '无'}",
                                    }
                                    self._append_audit({
                                        "event": "pubmed_search",
                                        "turn_id": turn_id,
                                        "tool_call_id": tool_call_id,
                                        "call_index": turn_pubmed_calls,
                                        "thread_call_index": self.pubmed_call_count,
                                        "query": query,
                                        "returned_pmids": pmids,
                                    })
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

        except Exception as e:
            chat_history.append(
                ChatMessage(
                    role="assistant", content=f"❌ Error: {str(e)}", metadata={"title": "Error"}
                )
            )
            yield chat_history, self.display_file_path


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

    with gr.Blocks(theme=gr.themes.Soft()) as demo:
        with gr.Column():
            gr.Markdown(
                """
            # 🏥 MedRAX
            Medical Reasoning Agent with imaging and PubMed tools
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
                        type="messages",
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
                        label="Image", type="filepath", height=700, container=True
                    )
                    with gr.Row():
                        upload_button = gr.UploadButton(
                            "📎 Upload X-Ray",
                            file_types=["image"],
                        )
                        dicom_upload = gr.UploadButton(
                            "📄 Upload DICOM",
                            file_types=["file"],
                        )
                    with gr.Row():
                        clear_btn = gr.Button("Clear Chat")
                        new_thread_btn = gr.Button("New Thread")

        # Event handlers
        def clear_chat():
            interface.reset_conversation()
            return [], None

        def new_thread():
            interface.reset_conversation()
            return [], None

        def handle_file_upload(file):
            return interface.handle_upload(file.name)

        chat_msg = txt.submit(
            interface.add_message, inputs=[txt, image_display, chatbot], outputs=[chatbot, txt]
        )
        bot_msg = chat_msg.then(
            interface.process_message,
            inputs=[txt, image_display, chatbot],
            outputs=[chatbot, image_display, txt],
        )
        bot_msg.then(lambda: gr.Textbox(interactive=True), None, [txt])

        upload_button.upload(handle_file_upload, inputs=upload_button, outputs=image_display)

        dicom_upload.upload(handle_file_upload, inputs=dicom_upload, outputs=image_display)

        clear_btn.click(clear_chat, outputs=[chatbot, image_display])
        new_thread_btn.click(new_thread, outputs=[chatbot, image_display])

    return demo
