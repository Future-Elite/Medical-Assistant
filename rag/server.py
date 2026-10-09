"""Local read-only HTTP workbench for the RAG package (synthetic fixtures by default)."""
from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import asdict
import sys
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag import __version__
from rag.agent import EvidenceRAG
from rag.config import get_config
from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS, SYNTHETIC_PUBMED
from rag.guidelines import GuidelineMetadataConnector, who_guideline_metadata_registry
from rag.his import InMemoryHISAdapter
from rag.neural_adapters import MedCPTReranker, build_neural_registry, cache_dir_hint, neural_dependencies_state
from rag.pubmed import PubMedConnector
from rag.pipeline import EvidenceRetrievalPipeline

ROOT = Path(__file__).resolve().parent
MAX_BODY_BYTES = 1_000_000


def _neural_parts():
    """构建神经适配器（懒加载）与 cross-encoder 重排器；构造本身不加载模型。"""
    neural = build_neural_registry(cache_dir=cache_dir_hint())
    reranker = neural.pop("cross_encoder", None) or MedCPTReranker(cache_dir=cache_dir_hint())
    return neural, reranker


def create_offline_tool() -> EvidenceRAG:
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    neural, reranker = _neural_parts()
    return EvidenceRAG(EvidenceRetrievalPipeline(registry, neural_retrievers=neural, reranker=reranker))


def create_tool(source_mode: str = "simulated") -> EvidenceRAG:
    config = get_config()
    registry = ConnectorRegistry()
    if source_mode == "simulated":
        registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
        registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    elif source_mode == "live_pubmed":
        if not config.allow_live_pubmed:
            raise ValueError("live PubMed is disabled by local configuration")
        registry.register(PubMedConnector())
        registry.register(GuidelineMetadataConnector("who-guideline-live", who_guideline_metadata_registry()))
    elif source_mode == "live":
        if not (config.allow_live_pubmed and config.allow_live_guidelines):
            raise ValueError("live PubMed and guideline sources are not both enabled")
        registry.register(PubMedConnector())
        registry.register(GuidelineMetadataConnector("who-guideline-live", who_guideline_metadata_registry()))
    else:
        raise ValueError(f"unknown source_mode: {source_mode}")
    neural, reranker = _neural_parts()
    return EvidenceRAG(EvidenceRetrievalPipeline(registry, neural_retrievers=neural, reranker=reranker))



def _method_status() -> dict[str, dict[str, object]]:
    """报告各检索方法的可用性：依赖是否就绪、模型缓存是否存在。

    只检查本地文件与 import，不加载模型、不下载、不联网。
    """
    deps = neural_dependencies_state()
    cache = cache_dir_hint()
    def cached(repo: str) -> bool:
        if not cache:
            return False
        import pathlib
        folder = "models--" + repo.replace("/", "--")
        return (pathlib.Path(cache) / folder).is_dir()
    return {
        "bm25": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "ql": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "rm3": {"available": True, "kind": "lexical", "dependency": "stdlib"},
        "dense": {"available": bool(deps["torch"] and deps["transformers"] and cached("ncbi/MedCPT-Query-Encoder")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("ncbi/MedCPT-Query-Encoder"), "note": "英文域 MedCPT 双塔；中文问题经受控英文术语检索"},
        "xlingual": {"available": bool(deps["torch"] and deps["transformers"] and cached("intfloat/multilingual-e5-base")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("intfloat/multilingual-e5-base"), "note": "多语言 e5 双塔；中文问题直接编码、跨语言检索英文语料"},
        "translate": {"available": bool(deps["torch"] and deps["transformers"] and cached("Helsinki-NLP/opus-mt-zh-en")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("Helsinki-NLP/opus-mt-zh-en"), "note": "zh→en 机器翻译查询变体；译文不改写原问题，数字不一致会告警"},
        "splade": {"available": bool(deps["torch"] and deps["transformers"] and cached("naver/splade-cocondenser-ensembledistil")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("naver/splade-cocondenser-ensembledistil")},
        "colbert": {"available": bool(deps["torch"] and deps["transformers"] and cached("colbert-ir/colbertv2.0")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("colbert-ir/colbertv2.0")},
        "query2doc": {"available": bool(deps["torch"] and deps["transformers"] and cached("google/flan-t5-small")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("google/flan-t5-small")},
        "cross_encoder": {"available": bool(deps["torch"] and deps["transformers"] and cached("ncbi/MedCPT-Cross-Encoder")), "kind": "reranker", "dependency": "torch+transformers", "model_cached": cached("ncbi/MedCPT-Cross-Encoder")},
    }

OFFLINE_HIS = InMemoryHISAdapter(SYNTHETIC_HIS_SNAPSHOTS)


class RAGRequestHandler(BaseHTTPRequestHandler):
    tool = create_offline_tool()
    source_mode = "simulated"
    tools: dict[str, EvidenceRAG] = {"simulated": tool}
    retrieval_tools: dict[str, EvidenceRAG] = {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(ROOT / "evidence_retrieval_workbench.html", "text/html; charset=utf-8")
            return
        if path == "/api/health":
            config = get_config()
            self._send_json({
                "ok": True,
                "rag_version": __version__,
                "retrieval_mode": "offline_synthetic_fixture" if self.source_mode == "simulated" else self.source_mode,
                "active_mode": self.source_mode,
                "available_modes": ["simulated"] + (["live_pubmed"] if config.allow_live_pubmed else []) + (["live"] if config.allow_live_pubmed and config.allow_live_guidelines else []),
                "his_mode": "offline_synthetic",
                "sources": self.tool.pipeline.registry.describe(),
                "semantic_trace": {"status": "not_configured", "retriever_id": None},
                "generation_performed": False,
                "clinical_decision_performed": False,
                "minicheck_configured": bool(getattr(self.tool.auditor.verifier, "model_name", None)),
                "retrieval_methods": _method_status(),
                "message": "当前工作台使用合成 PubMed/指南数据，仅用于界面与契约测试；不代表实时医学证据。",
            })
            return
        if path == "/api/tools":
            self._send_json({"tools": EvidenceRAG.tool_specifications()})
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该资源")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/his/snapshot":
                snapshot = OFFLINE_HIS.get_snapshot(str(payload.get("patient_ref") or ""), str(payload.get("as_of") or ""))
                self._send_json({"status": "ok", "data": asdict(snapshot)})
                return
            if path == "/api/retrieve":
                mode = str(payload.get("source_mode") or self.source_mode)
                tool = self._tool_for(mode)
                result = tool.retrieve_evidence(payload)
                self.retrieval_tools[result["retrieval_id"]] = tool
                self._send_json(result)
                return
            if path == "/api/citation":
                self._send_json(self._tool_for_retrieval(str(payload.get("retrieval_id") or "")).open_citation(payload))
                return
            if path == "/api/audit":
                self._send_json(self._tool_for_retrieval(str(payload.get("retrieval_id") or "")).audit_grounded_answer(payload))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该接口")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc) or type(exc).__name__)
        except Exception as exc:  # pragma: no cover - defensive HTTP boundary
            error_id = uuid.uuid4().hex[:12]
            message = _safe_error_message(exc)
            print(f"[{error_id}] {type(exc).__name__}: {message}", file=sys.stderr)
            traceback.print_exc()
            self._send_json({
                "error": message, "exception_type": type(exc).__name__,
                "stage": "request", "error_id": error_id,
            }, HTTPStatus.BAD_GATEWAY)

    def log_message(self, format: str, *args: Any) -> None:
        return

    @classmethod
    def _tool_for(cls, mode: str) -> EvidenceRAG:
        if mode not in {"simulated", "live_pubmed", "live"}:
            raise ValueError(f"unknown source_mode: {mode}")
        if mode not in cls.tools:
            cls.tools[mode] = create_tool(mode)
        return cls.tools[mode]

    @classmethod
    def _tool_for_retrieval(cls, retrieval_id: str) -> EvidenceRAG:
        if retrieval_id not in cls.retrieval_tools:
            raise ValueError("unknown retrieval_id; retrieve evidence before opening a citation")
        return cls.retrieval_tools[retrieval_id]


    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length > MAX_BODY_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return parsed

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "页面文件不存在")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    for secret_name in ("openai_api_key", "ncbi_api_key"):
        secret = str(getattr(get_config(), secret_name, "") or "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def _find_port_pids(port: int) -> list[int]:
    """返回监听指定 TCP 端口的进程 PID 列表（Linux：ss，回退 lsof）。

    仅用于陈旧实例清退；不会终止任何进程。
    """
    import re
    import subprocess

    pids: list[int] = []
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]
            if not local.endswith(f":{port}"):
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.append(int(match.group(1)))
    except Exception:
        pass
    if not pids:
        try:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5).stdout
            pids.extend(int(line.strip()) for line in out.splitlines() if line.strip().isdigit())
        except Exception:
            pass
    return sorted(set(pids))


def _is_rag_server_process(pid: int) -> bool:
    """严格校验 PID 是否是 RAG workbench 服务进程（绝不误伤无关进程）。

    识别两种启动方式：`python -m rag.server` 与 `python .../rag/server.py`。
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    if not args or "python" not in Path(args[0]).name.lower():
        return False
    for index, arg in enumerate(args):
        if arg == "rag.server" and index > 0 and args[index - 1] == "-m":
            return True
        if arg.endswith("rag/server.py"):
            return True
    return False


def _process_alive(pid: int) -> bool:
    """True 表示进程仍存活（僵尸进程视为已退出，其占用的端口已释放）。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        state = stat.rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return False


def _release_port(port: int, *, wait_seconds: float = 5.0) -> list[int]:
    """若端口被旧的 RAG 服务实例占用，将其清退（SIGTERM，拒绝退出则 SIGKILL）。

    返回被清退的 PID 列表。非 RAG 服务进程一律不动，交由调用方报错提示。
    """
    import os
    import signal
    import time

    victims = [pid for pid in _find_port_pids(port) if _is_rag_server_process(pid)]
    if not victims:
        return []
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not any(_process_alive(pid) for pid in victims):
            return victims
        time.sleep(0.1)
    for pid in victims:
        if _process_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return victims


def serve(host: str | None = None, port: int | None = None, *, cleanup_stale: bool = True) -> None:
    import errno

    config = get_config()
    host = config.server_host if host is None else host
    if port is None:
        env_port = os.environ.get("RAG_SERVER_PORT", "").strip()
        port = int(env_port) if env_port.isdigit() else config.server_port
    if cleanup_stale and port > 0:
        released = _release_port(port)
        if released:
            print(f"已清退占用端口 {port} 的旧 RAG 实例：{', '.join(str(pid) for pid in released)}")
    try:
        server = ThreadingHTTPServer((host, port), RAGRequestHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            holders = _find_port_pids(port)
            hint = f"（端口持有者 PID: {', '.join(str(pid) for pid in holders)}，非 RAG 服务进程，未自动清退）" if holders else ""
            print(f"端口 {port} 被占用{hint}；请手动释放后重试。")
            raise SystemExit(1) from exc
        raise
    print(f"RAG workbench: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
