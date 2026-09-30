"""Local GUI server using live PubMed retrieval and an optional real LLM.

Required for live model summaries (keep credentials outside the repository):

    Edit code\\src\\Medical-Assistant\\rag\\config.py: openai_api_key
    Edit code\\src\\Medical-Assistant\\rag\\config.py: openai_model

Optional for PubMed etiquette:

    Edit code\\src\\Medical-Assistant\\rag\\config.py: ncbi_email
"""

from __future__ import annotations

import json
import mimetypes
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

from rag.agent import EvidenceRAG
from rag.config import get_config
from rag.connectors import ConnectorRegistry
from rag.llm import LLMConfigurationError, LLMRequestError, OpenAIResponsesLLM
from rag.pipeline import EvidenceRetrievalPipeline
from rag.pubmed import PubMedConnector


ROOT = Path(__file__).resolve().parent


def create_live_tool() -> EvidenceRAG:
    """Build the only retrieval path used by this GUI: real PubMed E-utilities."""
    registry = ConnectorRegistry()
    registry.register(PubMedConnector())
    return EvidenceRAG(EvidenceRetrievalPipeline(registry))


class RAGRequestHandler(BaseHTTPRequestHandler):
    tool = create_live_tool()
    llm = OpenAIResponsesLLM()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(ROOT / "demo.html", "text/html; charset=utf-8")
            return
        if path == "/api/health":
            verifier = self.tool.auditor.verifier
            minicheck_configured = bool(getattr(verifier, "model_name", None))
            self._send_json({
                "ok": True, "retrieval_source": "live_pubmed_eutilities",
                "llm_configured": self.llm.configured, "llm_message": self.llm.configuration_message(),
                "llm_config": self.llm.safe_config,
                "config_file": str(Path(__file__).resolve().with_name("config.py")),
                "minicheck_configured": minicheck_configured,
                "minicheck_message": None if minicheck_configured else "未在 config.py 中配置 minicheck_model；主张级校验不会执行。",
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
            if path == "/api/retrieve":
                result = self.tool.retrieve_evidence(payload)
                if bool(payload.get("generate_summary", True)):
                    self._attach_llm_summary(result, str(payload.get("question") or ""))
                self._send_json(result)
                return
            if path == "/api/citation":
                self._send_json(self.tool.open_citation(payload))
                return
            if path == "/api/audit":
                self._send_json(self.tool.audit_grounded_answer(payload))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该接口")
        except (ValueError, TypeError, KeyError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            message = _safe_error_message(exc)
            print(f"[{error_id}] {type(exc).__name__}: {message}", file=sys.stderr)
            traceback.print_exc()
            self._send_json({
                "error": message,
                "exception_type": type(exc).__name__,
                "stage": "request",
                "error_id": error_id,
                "http_status": getattr(exc, "status_code", None),
            }, HTTPStatus.BAD_GATEWAY)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _attach_llm_summary(self, result: dict[str, Any], question: str) -> None:
        try:
            result["llm_summary"] = self.llm.summarize(question=question, evidence_package=result)
            result["llm_summary"]["audit"] = self.tool.audit_grounded_answer({
                "retrieval_id": result["retrieval_id"], "answer": result["llm_summary"]["text"],
            })
        except LLMConfigurationError as exc:
            result["llm_summary"] = {"status": "not_configured", "message": str(exc)}
        except LLMRequestError as exc:
            result["llm_summary"] = {"status": "failed", "message": str(exc)}
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            message = _safe_error_message(exc)
            print(f"[{error_id}] llm_summary {type(exc).__name__}: {message}", file=sys.stderr)
            traceback.print_exc()
            result["llm_summary"] = {
                "status": "failed",
                "stage": "llm_summary",
                "exception_type": type(exc).__name__,
                "message": message,
                "error_id": error_id,
            }
    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
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
    secret = str(getattr(get_config(), "openai_api_key", "") or "")
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def serve(host: str | None = None, port: int | None = None) -> None:
    config = get_config()
    host = config.server_host if host is None else host
    port = config.server_port if port is None else port
    server = ThreadingHTTPServer((host, port), RAGRequestHandler)
    print(f"RAG GUI: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()





