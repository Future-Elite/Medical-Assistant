"""Small desktop UI for the MedLLM data preparation workflow."""

from __future__ import annotations

import threading
import hashlib
import os
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dotenv import load_dotenv

import guideline_index
import pubmed_fetcher
from guideline_processing.medical_assistant_export import export_custom_source
from guideline_processing.medical_agent import MedicalAgentSession
from guideline_processing.evaluation import evaluate_systems, load_queries, save_report
from guideline_processing.run_chunking_experiments import run_experiments
from guideline_processing.run_medical_assistant import build_rag

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


class MedLLMApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MedLLM 医学知识库工具")
        self.geometry("1120x780")
        self.minsize(940, 660)
        self._build_style()
        self._build_ui()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="MedLLM 医学知识库工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="PubMed 文献采集与中文医学指南结构化", style="Hint.TLabel").pack(anchor="w", pady=(2, 12))
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True)
        self._pubmed_tab(tabs)
        self._guideline_tab(tabs)
        self._rag_tab(tabs)
        self._evaluation_tab(tabs)
        self.tabs = tabs
        self.status = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status, style="Hint.TLabel").pack(anchor="w", pady=(8, 0))

    def _row(self, parent, label, variable, browse=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        if browse:
            ttk.Button(row, text="选择", command=browse).pack(side="left", padx=(7, 0))

    def _pubmed_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=16)
        tabs.add(frame, text="PubMed 文献采集")
        self.query = tk.StringVar(value="(lung cancer) AND (immunotherapy)")
        self.min_year = tk.StringVar(value="2019")
        self.max_year = tk.StringVar(value="2025")
        self.max_results = tk.StringVar(value="100")
        self.pubmed_out = tk.StringVar(value="data/pubmed_records")
        self._row(frame, "PubMed 检索式", self.query)
        self._row(frame, "起始年份", self.min_year)
        self._row(frame, "结束年份", self.max_year)
        self._row(frame, "最大结果数", self.max_results)
        self._row(frame, "输出文件前缀", self.pubmed_out, self._choose_pubmed_out)
        ttk.Label(frame, text="输出为同名 JSON 和 CSV。网络请求可能需要几十秒。", style="Hint.TLabel").pack(anchor="w", pady=(3, 12))
        self.pubmed_button = ttk.Button(frame, text="开始抓取", command=self._run_pubmed)
        self.pubmed_button.pack(anchor="w")
        self.pubmed_log = self._log_box(frame)

    def _guideline_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=16)
        tabs.add(frame, text="中文指南结构化")
        self.guideline_input = tk.StringVar(value="data/guidelines/基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.pdf")
        self.guideline_output = tk.StringVar(value="data/newborn_pneumonia_chunks.jsonl")
        self.rag_output = tk.StringVar(value="data/medical_assistant_guidelines.json")
        self.specialty = tk.StringVar(value="新生儿")
        self.guideline_version = tk.StringVar(value="2025")
        self.guideline_url = tk.StringVar(value="https://www.zgddek.com/CN/10.7499/j.issn.1008-8830.2412164")
        self.chunk_size = tk.StringVar(value="800")
        self.overlap = tk.StringVar(value="120")
        self._row(frame, "指南文件/目录", self.guideline_input, self._choose_guideline_input)
        self._row(frame, "输出 JSONL", self.guideline_output, self._choose_guideline_output)
        self._row(frame, "RAG 导出 JSON", self.rag_output, self._choose_rag_output)
        self._row(frame, "专业方向", self.specialty)
        self._row(frame, "指南版本", self.guideline_version)
        self._row(frame, "来源链接", self.guideline_url)
        self._row(frame, "块大小（字符）", self.chunk_size)
        self._row(frame, "重叠（字符）", self.overlap)
        ttk.Label(frame, text="PDF 由 MinerU 转为 Markdown；正文按标题切分，表格独立保留，图片保留引用。", style="Hint.TLabel").pack(anchor="w", pady=(3, 12))
        self.guideline_button = ttk.Button(frame, text="解析 PDF 并生成知识块", command=self._run_guideline)
        self.guideline_button.pack(anchor="w")
        self.guideline_log = self._log_box(frame)

    def _rag_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=16)
        tabs.add(frame, text="指南 RAG 演示")
        self.rag_library = tk.StringVar(value=str(ROOT / "data" / "medical_assistant_guidelines.json"))
        self.agent_api_key = tk.StringVar(value=os.environ.get("DASHSCOPE_API_KEY", ""))
        self.agent_base_url = tk.StringVar(value="https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.agent_model = tk.StringVar(value="qwen3-vl-plus")
        self.generate_answer = tk.BooleanVar(value=False)
        self._row(frame, "指南知识库", self.rag_library, self._choose_rag_library)
        model_row = ttk.Frame(frame)
        model_row.pack(fill="x", pady=5)
        ttk.Label(model_row, text="DashScope Key", width=16).pack(side="left")
        ttk.Entry(model_row, textvariable=self.agent_api_key, show="*").pack(side="left", fill="x", expand=True)
        ttk.Label(model_row, text="模型").pack(side="left", padx=(8, 4))
        ttk.Entry(model_row, textvariable=self.agent_model, width=23).pack(side="left")
        endpoint_row = ttk.Frame(frame)
        endpoint_row.pack(fill="x", pady=5)
        ttk.Label(endpoint_row, text="模型接口", width=16).pack(side="left")
        ttk.Entry(endpoint_row, textvariable=self.agent_base_url).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(
            frame,
            text="使用 Medical-Assistant Agent 生成带引用回答",
            variable=self.generate_answer,
            command=self._update_rag_mode,
        ).pack(anchor="w", pady=(4, 8))

        query_frame = ttk.Frame(frame)
        query_frame.pack(fill="x", pady=(8, 12))
        ttk.Label(query_frame, text="问题", width=16).pack(side="left", anchor="n", pady=5)
        self.rag_question = tk.Text(query_frame, height=3, wrap="word")
        self.rag_question.pack(side="left", fill="x", expand=True)
        self.rag_question.insert("1.0", "新生儿感染性肺炎的早发型和晚发型如何分类？")
        self.rag_button = ttk.Button(query_frame, text="检索指南", command=self._run_rag_query)
        self.rag_button.pack(side="left", padx=(8, 0), anchor="n")

        ttk.Label(frame, text="检索证据", style="Hint.TLabel").pack(anchor="w")
        result_frame = ttk.Frame(frame)
        result_frame.pack(fill="both", expand=True, pady=(5, 8))
        self.rag_results = tk.Listbox(result_frame, height=11, exportselection=False)
        self.rag_results.pack(side="left", fill="both", expand=True)
        self.rag_results.bind("<<ListboxSelect>>", self._open_selected_citation)
        result_scroll = ttk.Scrollbar(result_frame, command=self.rag_results.yview)
        result_scroll.pack(side="right", fill="y")
        self.rag_results.configure(yscrollcommand=result_scroll.set)

        detail = ttk.LabelFrame(frame, text="引用原文", padding=8)
        detail.pack(fill="both", expand=True)
        self.rag_detail = tk.Text(detail, height=10, wrap="word", state="disabled", background="#fafafa")
        self.rag_detail.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(detail, command=self.rag_detail.yview)
        detail_scroll.pack(side="right", fill="y")
        self.rag_detail.configure(yscrollcommand=detail_scroll.set)
        self.rag_open_url = ttk.Button(frame, text="打开指南来源", command=self._open_source_url, state="disabled")
        self.rag_open_url.pack(anchor="e", pady=(8, 0))
        self.rag_status = tk.StringVar(value="知识库生成后即可检索指南。")
        ttk.Label(frame, textvariable=self.rag_status, style="Hint.TLabel").pack(anchor="w", pady=(6, 0))
        self._rag_citations = []
        self._rag_instance = None
        self._medical_agent = None
        self._medical_agent_config = None
        self._rag_current_url = ""

    def _evaluation_tab(self, tabs):
        frame = ttk.Frame(tabs, padding=16)
        tabs.add(frame, text="检索评估")
        self.eval_dataset = tk.StringVar(value=str(ROOT / "data" / "guideline_eval_pilot.jsonl"))
        self.eval_system_name = tk.StringVar(value="structure_aware")
        self.eval_baseline_name = tk.StringVar(value="baseline")
        self.eval_baseline_library = tk.StringVar()
        self._row(frame, "人工标注集", self.eval_dataset, self._choose_eval_dataset)
        self._row(frame, "当前方案名称", self.eval_system_name)
        self._row(frame, "对照方案库", self.eval_baseline_library, self._choose_eval_baseline)
        self._row(frame, "对照方案名称", self.eval_baseline_name)
        ttk.Label(
            frame,
            text="默认集使用指南标题、章节线索和原文证据短语作为锚点，可在不同切分方案间复用。请检查锚点覆盖率及未匹配项；演示标注尚待人工复核。",
            style="Hint.TLabel",
            wraplength=850,
        ).pack(anchor="w", pady=(4, 8))
        self.eval_button = ttk.Button(frame, text="运行检索评估", command=self._run_evaluation)
        self.eval_button.pack(anchor="w", pady=(0, 8))
        self.chunking_experiment_button = ttk.Button(
            frame, text="比较切分与表格方案", command=self._run_chunking_experiments
        )
        self.chunking_experiment_button.pack(anchor="w", pady=(0, 8))
        self.eval_status = tk.StringVar(value="默认基准 2.0.0：12 条新生儿指南 pilot 题，尚待人工复核。")
        ttk.Label(frame, textvariable=self.eval_status, style="Hint.TLabel").pack(anchor="w", pady=(0, 6))
        result_frame = ttk.Frame(frame)
        result_frame.pack(fill="both", expand=True)
        self.eval_output = tk.Text(result_frame, wrap="none", state="disabled", background="#fafafa")
        self.eval_output.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(result_frame, command=self.eval_output.yview)
        scroll.pack(side="right", fill="y")
        self.eval_output.configure(yscrollcommand=scroll.set)

    def _log_box(self, parent):
        box = ttk.LabelFrame(parent, text="运行日志", padding=8)
        box.pack(fill="both", expand=True, pady=(16, 0))
        text = tk.Text(box, height=10, state="disabled", wrap="word", background="#fafafa")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(box, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        return text

    def _log(self, message: str, target=None):
        text = target or self.guideline_log
        text.configure(state="normal")
        text.insert("end", message + "\n")
        text.see("end")
        text.configure(state="disabled")
        self.status.set(message)

    def _choose_pubmed_out(self):
        value = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if value: self.pubmed_out.set(str(Path(value).with_suffix("")))

    def _choose_guideline_input(self):
        value = filedialog.askopenfilename(filetypes=[("指南文件", "*.pdf *.txt *.md *.json"), ("所有文件", "*.*")])
        if not value:
            value = filedialog.askdirectory()
        if value: self.guideline_input.set(value)

    def _choose_guideline_output(self):
        value = filedialog.asksaveasfilename(defaultextension=".jsonl", filetypes=[("JSONL", "*.jsonl")])
        if value: self.guideline_output.set(value)

    def _choose_rag_output(self):
        value = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if value: self.rag_output.set(value)

    def _choose_rag_library(self):
        value = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if value:
            self.rag_library.set(value)

    def _choose_eval_dataset(self):
        value = filedialog.askopenfilename(filetypes=[("JSONL", "*.jsonl"), ("所有文件", "*.*")])
        if value:
            self.eval_dataset.set(value)

    def _choose_eval_baseline(self):
        value = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if value:
            self.eval_baseline_library.set(value)

    def _open_source_url(self):
        if self._rag_current_url:
            webbrowser.open(self._rag_current_url)

    def _update_rag_mode(self):
        self.rag_button.configure(text="生成回答" if self.generate_answer.get() else "检索指南")

    def _run_pubmed(self):
        try:
            max_results = int(self.max_results.get())
            min_year, max_year = int(self.min_year.get()), int(self.max_year.get())
        except ValueError:
            messagebox.showerror("输入错误", "年份和最大结果数必须是数字")
            return
        self.pubmed_button.configure(state="disabled")
        self._log("正在搜索 PubMed...", self.pubmed_log)
        query = self.query.get()
        output = Path(self.pubmed_out.get())
        def task():
            try:
                ids = pubmed_fetcher.search(query, max_results, min_year, max_year, None, None)
                records = pubmed_fetcher.fetch(ids, None, None)
                pubmed_fetcher.save(records, output)
                self.after(0, lambda: self._log(f"完成：{len(records)} 篇，已写入 {output}.json 和 {output}.csv", self.pubmed_log))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: messagebox.showerror("抓取失败", error))
                self.after(0, lambda: self._log("抓取失败，请检查网络和检索式。", self.pubmed_log))
            finally:
                self.after(0, lambda: self.pubmed_button.configure(state="normal"))
        threading.Thread(target=task, daemon=True).start()

    def _run_guideline(self):
        try:
            size, overlap = int(self.chunk_size.get()), int(self.overlap.get())
            if size <= overlap or size < 100: raise ValueError
        except ValueError:
            messagebox.showerror("输入错误", "块大小必须大于重叠长度，且至少为 100")
            return
        input_path = _project_path(self.guideline_input.get())
        output = _project_path(self.guideline_output.get())
        rag_output = _project_path(self.rag_output.get())
        specialty = self.specialty.get()
        version = self.guideline_version.get()
        source_url = self.guideline_url.get()
        source_id = f"medllm-guideline-{input_path.stem}" if input_path.is_file() else "medllm-guideline-library"
        self.guideline_button.configure(state="disabled")
        self._log("MinerU 正在解析 PDF 并生成 Markdown，完成后继续切分和导出……", self.guideline_log)

        def task():
            try:
                count = guideline_index.build(input_path, output, specialty, size, overlap,
                                              version=version, source_url=source_url)
                rag_count = export_custom_source(output, rag_output, source_id)
                message = (f"完成：{count} 个知识块；Markdown 在 {output.parent / 'parsed_markdown'}；"
                           f"RAG 导出 {rag_count} 条到 {rag_output}")
                def finish_processing():
                    self.rag_library.set(str(rag_output))
                    self._rag_instance = None
                    self._medical_agent = None
                    self._medical_agent_config = None
                    self._log(message, self.guideline_log)
                    self.rag_status.set("知识库已更新，可以检索或生成回答。")
                self.after(0, finish_processing)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: messagebox.showerror("处理失败", error))
                self.after(0, lambda: self._log("处理失败，请查看错误提示。", self.guideline_log))
            finally:
                self.after(0, lambda: self.guideline_button.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def _run_rag_query(self):
        library_path = _project_path(self.rag_library.get())
        question = self.rag_question.get("1.0", "end").strip()
        if not question:
            messagebox.showerror("输入错误", "请输入要检索的问题。")
            return
        if not library_path.is_file():
            messagebox.showerror("知识库不存在", "请先解析并导出指南，或选择已有的 Medical-Assistant 知识库 JSON。")
            return
        use_agent = self.generate_answer.get()
        api_key = self.agent_api_key.get()
        base_url = self.agent_base_url.get().strip()
        model_name = self.agent_model.get().strip()
        if use_agent and not api_key.strip():
            messagebox.showerror("缺少 API Key", "请先在 DashScope Key 输入框中填写密钥。密钥只保存在本次程序运行内存中。")
            return
        self.rag_button.configure(state="disabled")
        self.rag_status.set("Medical-Assistant Agent 正在生成回答……" if use_agent else "Medical-Assistant 正在检索指南……")
        self.rag_results.delete(0, "end")
        self._rag_citations = []
        library_mtime = library_path.stat().st_mtime_ns

        def task():
            try:
                if use_agent:
                    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
                    config = (str(library_path.resolve()), library_mtime, base_url, model_name, key_fingerprint)
                    if self._medical_agent is None or self._medical_agent_config != config:
                        self._medical_agent = MedicalAgentSession(
                            library_path, api_key, base_url=base_url, model=model_name,
                        )
                        self._medical_agent_config = config
                        self._rag_instance = None
                    response = self._medical_agent.ask(question)

                    def show_answer():
                        evidence_lines = []
                        for event in response.get("events", []):
                            artifact = event.get("artifact") or {}
                            if event.get("event") != "evidence_search":
                                continue
                            for item in artifact.get("evidence", []):
                                metadata = item.get("metadata") or {}
                                evidence_lines.append(
                                    f"- {item.get('title', '指南证据')} | "
                                    f"{metadata.get('locator') or '未标注章节'}\n"
                                    f"  {(item.get('content') or '')[:500]}"
                                )
                        evidence_text = "\n\n检索证据：\n" + "\n".join(evidence_lines) if evidence_lines else ""
                        self.rag_detail.configure(state="normal")
                        self.rag_detail.delete("1.0", "end")
                        self.rag_detail.insert(
                            "1.0",
                            f"回答状态：{response.get('status', 'unknown')}\n"
                            f"本轮：{response.get('turn_id', '')}\n\n{response.get('answer', '')}"
                            f"{evidence_text}",
                        )
                        self.rag_detail.configure(state="disabled")
                        self.rag_status.set("Agent 回答已返回；引用由 Medical-Assistant 校验流程处理。")

                    self.after(0, show_answer)
                    return
                rag = self._rag_instance
                if rag is None or self._rag_library_mtime != library_mtime:
                    rag = build_rag(library_path)
                result = rag.retrieve_evidence({
                    "request_id": "medllm-gui",
                    "question": question,
                    "source_mode": "custom",
                    "source_kinds": ["guideline"],
                    "top_k": 8,
                })
                citations = [
                    {**citation, "retrieval_id": result["retrieval_id"]}
                    for citation in result["citations"]
                ]
                if not citations:
                    self.after(0, lambda: self.rag_status.set("没有检索到相关证据，请调整问题或检查知识库。"))
                    return
                self._rag_instance = rag
                self._rag_library_mtime = library_mtime
                self._rag_citations = citations

                def show_results():
                    for index, item in enumerate(result["ranked_documents"]):
                        document = item["document"]
                        self.rag_results.insert(
                            "end",
                            f"{index + 1}. {document['metadata'].get('guide_title', document['title'])} | "
                            f"{document.get('locator') or '未标注章节'} | 分数 {item['score']:.3f}",
                        )
                    self.rag_status.set(f"检索完成：返回 {len(citations)} 条证据。选择一条查看引用原文。")
                    if result["ranked_documents"]:
                        self.rag_results.selection_set(0)
                        self._open_selected_citation()

                self.after(0, show_results)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self.rag_status.set(f"检索失败：{error}"))
            finally:
                self.after(0, lambda: self.rag_button.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def _run_evaluation(self):
        current_library = _project_path(self.rag_library.get())
        dataset_path = _project_path(self.eval_dataset.get())
        if not current_library.is_file() or not dataset_path.is_file():
            messagebox.showerror("评测输入缺失", "请检查当前方案知识库和人工标注集路径。")
            return
        current_name = self.eval_system_name.get().strip() or "current"
        baseline_path_text = self.eval_baseline_library.get().strip()
        baseline_name = self.eval_baseline_name.get().strip() or "baseline"
        systems = [{"name": current_name, "library": str(current_library)}]
        if baseline_path_text:
            baseline_path = _project_path(baseline_path_text)
            if not baseline_path.is_file():
                messagebox.showerror("对照方案不存在", str(baseline_path))
                return
            if baseline_name == current_name:
                messagebox.showerror("方案名称重复", "当前方案和对照方案名称必须不同。")
                return
            systems.append({"name": baseline_name, "library": str(baseline_path)})

        self.eval_button.configure(state="disabled")
        self.eval_status.set("正在运行离线 BM25 检索评估……")

        def task():
            try:
                queries = load_queries(dataset_path)
                report = evaluate_systems(queries, systems, benchmark=queries[0].get("benchmark"))
                output_path = ROOT / "data" / "evaluation_reports" / (
                    f"guideline_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                )
                save_report(report, output_path)

                lines = [
                    f"评测集：{dataset_path}",
                    f"基准：{report.get('benchmark', {}).get('name', '未命名')} "
                    f"v{report.get('benchmark', {}).get('version', 'unknown')} | "
                    f"标注状态：{report.get('benchmark', {}).get('annotation_status', '未声明')}",
                    f"问题数：{len(queries)}",
                    f"报告：{output_path}",
                    "注意：context_chars 是字符数近似，不是模型 tokenizer token 数。",
                ]
                for system in report["systems"]:
                    lines.extend([
                        "",
                        f"方案：{system['system']} ({system['evaluated_query_count']}/{system['query_count']} 条有效，"
                        f"覆盖率 {system['query_coverage']:.1%}；锚点覆盖率 "
                        f"{system['gold_anchor_coverage']:.1%}" if system["gold_anchor_coverage"] is not None
                        else f"方案：{system['system']} ({system['evaluated_query_count']}/{system['query_count']} 条有效，"
                             f"覆盖率 {system['query_coverage']:.1%})",
                        f"平均检索耗时：{system['average_latency_ms']:.1f} ms"
                        if system['average_latency_ms'] is not None else "平均检索耗时：无有效测量",
                        f"未匹配证据锚点：{len(system['unmatched_anchors'])}",
                    ])
                    for anchor in system["unmatched_anchors"]:
                        lines.append(f"  未匹配 {anchor['query_id']}: {' / '.join(anchor['anchor_terms'])}")
                    for k, metrics in system["summary"].items():
                        lines.append(
                            f"K={k}: Recall={metrics['recall']:.3f} | Hit={metrics['hit']:.3f} | "
                            f"MRR={metrics['mrr']:.3f} | nDCG={metrics['ndcg']:.3f} | "
                            f"Context chars={metrics['context_chars']:.0f}"
                            + (f" | Element Hit={metrics['element_hit']:.3f}"
                               if "element_hit" in metrics else "")
                        )
                    for row in system["queries"]:
                        if row.get("excluded_from_metrics"):
                            lines.append(f"  {row['query_id']}: 未计分（金标准锚点无法映射到该库）")
                        else:
                            lines.append(f"  {row['query_id']}: 首个正确证据排名={row['first_hit_rank'] or '未命中'}")

                def finish():
                    self.eval_output.configure(state="normal")
                    self.eval_output.delete("1.0", "end")
                    self.eval_output.insert("1.0", "\n".join(lines))
                    self.eval_output.configure(state="disabled")
                    self.eval_status.set("评估完成。指标用于检索比较，不代表医学正确性或临床安全性。")

                self.after(0, finish)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self.eval_status.set(f"评估失败：{error}"))
            finally:
                self.after(0, lambda: self.eval_button.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def _run_chunking_experiments(self):
        markdown_dir = ROOT / "data" / "parsed_markdown"
        benchmark_path = _project_path(self.eval_dataset.get())
        if not markdown_dir.is_dir() or not benchmark_path.is_file():
            messagebox.showerror("实验输入缺失", "请检查 data/parsed_markdown 和评测集路径。")
            return
        self.eval_button.configure(state="disabled")
        self.chunking_experiment_button.configure(state="disabled")
        self.eval_status.set("正在生成 11 个切分/结构方案并逐一评估，可能需要一两分钟……")

        def task():
            try:
                output_dir = ROOT / "data" / "chunking_experiments"
                report = run_experiments(markdown_dir, benchmark_path, output_dir)
                lines = [
                    f"方案数：{len(report['systems'])}",
                    f"评测集：{report['benchmark'].get('name')} v{report['benchmark'].get('version')}",
                    f"汇总 CSV：{output_dir / 'summary.csv'}",
                    f"详细报告：{output_dir / 'latest.json'}",
                    "注意：当前两份 Markdown 没有图片引用，图示策略对照无法代表图表效果。",
                    "",
                    "方案 | 块数 | 平均字符 | 锚点覆盖 | Recall@3 | MRR@3 | nDCG@3 | Element Hit@3 | 上下文字符@3",
                ]
                for system in report["systems"]:
                    stats = report["experiment"]["variant_stats"][system["system"]]
                    metrics = system["summary"]["3"]
                    lines.append(
                        f"{system['system']} | {stats['chunk_count']} | {stats['mean_chunk_chars']:.0f} | "
                        f"{system['gold_anchor_coverage']:.0%} | {metrics['recall']:.3f} | "
                        f"{metrics['mrr']:.3f} | {metrics['ndcg']:.3f} | "
                        f"{metrics.get('element_hit', 0):.3f} | {metrics['context_chars']:.0f}"
                    )

                def finish():
                    self.eval_output.configure(state="normal")
                    self.eval_output.delete("1.0", "end")
                    self.eval_output.insert("1.0", "\n".join(lines))
                    self.eval_output.configure(state="disabled")
                    self.eval_status.set("切分方案比较完成。该 pilot 需人工复核，图示结论还需含图指南验证。")

                self.after(0, finish)
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self.eval_status.set(f"切分方案比较失败：{error}"))
            finally:
                self.after(0, lambda: self.eval_button.configure(state="normal"))
                self.after(0, lambda: self.chunking_experiment_button.configure(state="normal"))

        threading.Thread(target=task, daemon=True).start()

    def _open_selected_citation(self, _event=None):
        selection = self.rag_results.curselection()
        if not selection or not self._rag_citations or not self._rag_instance:
            return
        citation_ref = self._rag_citations[selection[0]]
        try:
            opened = self._rag_instance.open_citation({
                "retrieval_id": citation_ref["retrieval_id"],
                "citation_id": citation_ref["citation_id"],
            })["citation"]
        except Exception as exc:
            self.rag_status.set(f"引用打开失败：{exc}")
            return
        self._rag_current_url = opened.get("url") or ""
        self.rag_open_url.configure(state="normal" if self._rag_current_url else "disabled")
        document = citation_ref.get("document") or {}
        title = document.get("metadata", {}).get("guide_title") or opened["title"]
        detail = (
            f"指南：{title}\n"
            f"章节：{opened.get('locator') or '未标注'}\n"
            f"版本：{opened.get('version_id') or '未标注'}\n"
            f"引用：{opened.get('citation_id')}\n\n"
            f"{opened.get('quote') or '没有可展示的引用原文。'}"
        )
        self.rag_detail.configure(state="normal")
        self.rag_detail.delete("1.0", "end")
        self.rag_detail.insert("1.0", detail)
        self.rag_detail.configure(state="disabled")


if __name__ == "__main__":
    MedLLMApp().mainloop()
