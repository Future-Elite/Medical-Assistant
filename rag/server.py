"""Local read-only HTTP workbench for the RAG package.

Source model (exactly two modes):

- ``live``   — real-time PubMed (NCBI E-utilities) + guideline source
               (WHO official metadata registry; ``live`` requires both live
               switches enabled in ``config.py``);
- ``custom`` — caller-provided knowledge libraries registered at runtime via
               ``POST /api/sources/custom`` (payload schema in
               ``custom_sources.py`` and ``contracts/README.md``).

There is no local offline corpus: the server registers no sources of its own
and never falls back to synthetic data.
"""
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
from rag.connectors import ConnectorRegistry
from rag.custom_sources import CustomSourceLibrary, SOURCE_MODES, load_custom_library
from rag.guidelines import GuidelineMetadataConnector, who_guideline_metadata_registry
from rag.his import PatientSnapshot
from rag.models import RetrievalRequest
from rag.neural_adapters import MedCPTReranker, build_neural_registry, cache_dir_hint, neural_dependencies_state
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector

ROOT = Path(__file__).resolve().parent
MAX_BODY_BYTES = 1_000_000
# HIS demos receive an explicit snapshot payload with the request; the server
# holds no built-in patient data.
HIS_MODE = "caller_supplied_snapshot"


def _neural_parts():
    """构建神经适配器（懒加载）与 cross-encoder 重排器；构造本身不加载模型。"""
    neural = build_neural_registry(cache_dir=cache_dir_hint())
    reranker = neural.pop("cross_encoder", None) or MedCPTReranker(cache_dir=cache_dir_hint())
    return neural, reranker


def build_live_registry() -> ConnectorRegistry:
    config = get_config()
    if not (config.allow_live_pubmed and config.allow_live_guidelines):
        raise ValueError("live PubMed and guideline sources are not both enabled")
    registry = ConnectorRegistry()
    registry.register(PubMedConnector())
    registry.register(GuidelineMetadataConnector("who-guideline-live", who_guideline_metadata_registry()))
    return registry


def create_tool(source_mode: str = "live", *, custom_library: CustomSourceLibrary | None = None) -> EvidenceRAG:
    """按来源模式构建工具。

    ``live``   实时 PubMed + 官方指南元数据；
    ``custom`` 调用方通过 ``CustomSourceLibrary`` 注册的知识库（空库显式报错）。
    """
    if source_mode not in SOURCE_MODES:
        raise ValueError(f"unknown source_mode: {source_mode}（可用：{', '.join(SOURCE_MODES)}）")
    if source_mode == "live":
        registry = build_live_registry()
    else:
        library = custom_library or CustomSourceLibrary()
        registry, library_warnings = library.build_registry()
        # 构建期警告随每次检索结果返回（见 ConnectorRegistry.warnings），此处不重复打印。
    neural, reranker = _neural_parts()
    lexicon, policy, penalty = _fidelity_parts()
    return EvidenceRAG(EvidenceRetrievalPipeline(
        registry, neural_retrievers=neural, reranker=reranker,
        fidelity_lexicon=lexicon, fidelity_default_policy=policy, fidelity_penalty=penalty,
    ))


def _fidelity_parts():
    """查询保真层配置：词表路径缺失/为空时 lexicon=None（串换检测标不可检测），
    路径存在但加载失败时显式抛错（不静默忽略配置）。

    用绝对导入：本模块支持 `python -m rag.server` 与直接跑脚本两种启动方式，
    脚本方式下 `__package__` 为空，相对导入会在首次检索时抛 ImportError。
    """
    import os
    from rag.fidelity import FidelityLexicon
    config = get_config()
    path = str(os.environ.get("RAG_FIDELITY_LEXICON") or config.fidelity_lexicon_path or "").strip()
    lexicon = FidelityLexicon.from_json_file(path) if path else None
    policy = str(os.environ.get("RAG_FIDELITY_POLICY") or config.fidelity_default_policy or "off").strip().lower()
    if policy not in RetrievalRequest.FIDELITY_GATE_POLICIES:
        raise ValueError("unknown fidelity policy in config: " + policy)
    return lexicon, policy, float(config.fidelity_penalty)



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
        "m3": {"available": bool(deps["torch"] and deps["transformers"] and cached("BAAI/bge-m3")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("BAAI/bge-m3"), "note": "BGE-M3 多语言双塔（CLS 池化）；中文问题直接编码、跨语言检索英文语料；本包只使用其 dense 功能"},
        "translate": {"available": bool(deps["torch"] and deps["transformers"] and cached("Helsinki-NLP/opus-mt-zh-en")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("Helsinki-NLP/opus-mt-zh-en"), "note": "zh→en 机器翻译查询变体；译文不改写原问题，数字不一致会告警"},
        "splade": {"available": bool(deps["torch"] and deps["transformers"] and cached("naver/splade-cocondenser-ensembledistil")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("naver/splade-cocondenser-ensembledistil")},
        "colbert": {"available": bool(deps["torch"] and deps["transformers"] and cached("colbert-ir/colbertv2.0")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("colbert-ir/colbertv2.0")},
        "query2doc": {"available": bool(deps["torch"] and deps["transformers"] and cached("google/flan-t5-small")), "kind": "neural", "dependency": "torch+transformers", "model_cached": cached("google/flan-t5-small")},
        "cross_encoder": {"available": bool(deps["torch"] and deps["transformers"] and cached("ncbi/MedCPT-Cross-Encoder")), "kind": "reranker", "dependency": "torch+transformers", "model_cached": cached("ncbi/MedCPT-Cross-Encoder")},
    }

CUSTOM_LIBRARY = CustomSourceLibrary()


def _minicheck_configured() -> bool:
    """health 摘要：只读配置，不构建工具、不加载模型。"""
    return bool(str(get_config().minicheck_model or "").strip())


def load_startup_custom_library() -> int:
    """可选：从 config.custom_sources_path 预载自定义库；返回加载的来源数。"""
    path = str(get_config().custom_sources_path or "").strip()
    if not path:
        return 0
    library = load_custom_library(path)
    for source in library._sources.values():  # noqa: SLF001 - same module family
        CUSTOM_LIBRARY.register(source)
    return len(library)


class RAGRequestHandler(BaseHTTPRequestHandler):
    source_mode = "live"
    tools: dict[str, EvidenceRAG] = {}
    retrieval_tools: dict[str, EvidenceRAG] = {}

    @classmethod
    def _custom_tool(cls) -> EvidenceRAG:
        """自定义来源库变动后（注册/清空）重建工具，保证检索反映最新库内容。"""
        return create_tool("custom", custom_library=CUSTOM_LIBRARY)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(ROOT / "evidence_retrieval_workbench.html", "text/html; charset=utf-8")
            return
        if path == "/api/health":
            config = get_config()
            live_available = bool(config.allow_live_pubmed and config.allow_live_guidelines)
            self._send_json({
                "ok": True,
                "rag_version": __version__,
                "retrieval_mode": self.source_mode,
                "active_mode": self.source_mode,
                "available_modes": [mode for mode, enabled in (("live", live_available), ("custom", True)) if enabled],
                "his_mode": HIS_MODE,
                "sources": self._describe_sources(),
                "custom_sources": CUSTOM_LIBRARY.describe(),
                "semantic_trace": {"status": "not_configured", "retriever_id": None},
                "generation_performed": False,
                "clinical_decision_performed": False,
                "minicheck_configured": _minicheck_configured(),
                "retrieval_methods": _method_status(),
                "message": "来源仅两种：live（实时 PubMed + 官方指南元数据）与 custom（调用方注册的数据库）；服务不内置任何本地语料。",
            })
            return
        if path == "/api/tools":
            self._send_json({"tools": EvidenceRAG.tool_specifications()})
            return
        if path == "/api/sources":
            self._send_json({
                "source_modes": list(SOURCE_MODES),
                "live": {"enabled": bool(get_config().allow_live_pubmed and get_config().allow_live_guidelines), "sources": ["pubmed-live", "who-guideline-live"] if get_config().allow_live_pubmed and get_config().allow_live_guidelines else []},
                "custom": {"registered": CUSTOM_LIBRARY.describe(), "recent_events": CUSTOM_LIBRARY.events[-10:]},
            })
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该资源")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/his/snapshot":
                # HIS 由调用方提供快照：请求体中的 patient_snapshot 原样回显并做 as_of 过滤。
                # 服务不持有任何内置患者数据。
                self._send_json({"status": "ok", "data": self._echo_snapshot(payload)})
                return
            if path == "/api/sources/custom":
                event = CUSTOM_LIBRARY.register_payload(payload)
                self.tools.pop("custom", None)  # 库变动后失效，下次检索重建
                self._send_json({"status": "ok", "event": event, "registered": CUSTOM_LIBRARY.describe()})
                return
            if path == "/api/sources/custom/clear":
                removed = CUSTOM_LIBRARY.clear()
                self.tools.pop("custom", None)
                self._send_json({"status": "ok", "removed_sources": removed})
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
    def _describe_sources(cls) -> list[dict[str, object]]:
        if cls.source_mode == "live":
            try:
                return build_live_registry().describe()
            except ValueError:
                return []
        if cls.source_mode == "custom":
            return CUSTOM_LIBRARY.describe()
        return []

    @classmethod
    def _tool_for(cls, mode: str) -> EvidenceRAG:
        if mode not in SOURCE_MODES:
            raise ValueError(f"unknown source_mode: {mode}（可用：{', '.join(SOURCE_MODES)}）")
        if mode == "custom":
            return cls._custom_tool()
        if mode not in cls.tools:
            cls.tools[mode] = create_tool(mode)
        return cls.tools[mode]

    @classmethod
    def _tool_for_retrieval(cls, retrieval_id: str) -> EvidenceRAG:
        if retrieval_id not in cls.retrieval_tools:
            raise ValueError("unknown retrieval_id; retrieve evidence before opening a citation")
        return cls.retrieval_tools[retrieval_id]

    @staticmethod
    def _echo_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        """以调用方提供的 patient_snapshot 为准，按 as_of 过滤后回显（只读、无内置数据）。"""
        snapshot_raw = payload.get("patient_snapshot") or payload.get("data") or payload
        if not isinstance(snapshot_raw, dict):
            raise ValueError("his snapshot endpoint requires a JSON object payload")
        from rag.his import snapshot_from_dict
        snapshot: PatientSnapshot = snapshot_from_dict({"patient_snapshot": snapshot_raw})
        return asdict(snapshot)


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
    """严格校验 PID 是否**本包**（本目录下的 rag 包）的 workbench 服务进程。

    同一仓库的 Medical-Assistant 也用 `-m rag.server` 启动、包名相同，
    因此不能只看模块名——必须确认启动路径解析到的就是本目录的 server.py：
    ``<entry>/rag/server.py``（-m 启动）或 ``.../rag/server.py``（脚本启动）
    解析后等于本包的 server.py，才算可清退目标。无关进程一律不动。
    """
    import os

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    if not args or "python" not in Path(args[0]).name.lower():
        return False
    package_root = Path(__file__).resolve().parent
    target_server = package_root / "server.py"

    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""

    def _resolve(entry: str) -> Path | None:
        candidate = Path(entry)
        if not candidate.is_absolute():
            if not cwd:
                return None
            candidate = Path(cwd) / candidate
        try:
            return candidate.resolve()
        except OSError:
            return None

    def _is_this_package_entry(resolved: Path | None) -> bool:
        """entry 作为 sys.path 项时，rag 包是否解析到本目录。"""
        if resolved is None:
            return False
        try:
            return (resolved / "rag" / "server.py").resolve() == target_server
        except OSError:
            return False

    for index, arg in enumerate(args):
        if arg == "rag.server" and index > 0 and args[index - 1] == "-m":
            try:
                environ = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
            except OSError:
                environ = ""
            for part in environ.split("\0"):
                if part.startswith("PYTHONPATH="):
                    for entry in part.split("=", 1)[1].split(":"):
                        if entry and _is_this_package_entry(_resolve(entry)):
                            return True
            if cwd and _is_this_package_entry(_resolve(cwd)):
                return True
        elif arg.endswith("server.py"):
            resolved = _resolve(arg)
            try:
                if resolved is not None and resolved == target_server:
                    return True
            except OSError:
                continue
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

    loaded = load_startup_custom_library()
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
    live_ok = bool(config.allow_live_pubmed and config.allow_live_guidelines)
    print(f"RAG workbench: http://{host}:{port}/")
    print(f"  来源模式：live（{'可用' if live_ok else '未启用：config.allow_live_* 未同时为 True'}）；custom（已注册自定义来源 {len(CUSTOM_LIBRARY)} 个{('，启动预载 ' + str(loaded)) if loaded else ''}）")
    print("  HIS：由调用方随请求传入 patient_snapshot（服务不内置患者数据）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
