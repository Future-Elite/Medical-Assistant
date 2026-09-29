"""OpenAI Responses API adapter for evidence-bounded, non-diagnostic summaries."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .config import get_config


def _clean_config_value(value: str | None) -> str | None:
    """Normalize values copied into config.py without ever logging secrets."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


class LLMConfigurationError(RuntimeError):
    """A real model call was requested but credentials/model are not configured."""


class LLMRequestError(RuntimeError):
    """A configured provider could not complete the model call."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class OpenAIResponsesLLM:
    """Minimal standard-library client for an OpenAI-compatible Responses API.

    The model is intentionally required through ``config.py``.  Model choice
    is deployment policy and must not be silently invented by this prototype.
    """

    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_mode: str | None = None
    timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
        config = get_config()
        if self.api_key is None:
            self.api_key = config.openai_api_key or None
        if self.model is None:
            self.model = config.openai_model or None
        if self.base_url is None:
            self.base_url = config.openai_base_url or "https://api.openai.com/v1"
        if self.api_mode is None:
            self.api_mode = getattr(config, "openai_api_mode", "auto") or "auto"
        self.api_key = _clean_config_value(self.api_key)
        self.model = _clean_config_value(self.model)
        self.base_url = _normalize_base_url(_clean_config_value(self.base_url) or "https://api.openai.com/v1")
        self.api_mode = _clean_config_value(self.api_mode) or "auto"
        if self.timeout_seconds == 60.0:
            self.timeout_seconds = config.request_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model and self._is_real_value(self.api_key) and self._is_real_value(self.model))

    @staticmethod
    def _is_real_value(value: str) -> bool:
        placeholders = {"your-api-key", "your_api_key", "你的 api key", "你的模型 id", "<your-api-key>"}
        return value.strip().lower() not in placeholders

    @property
    def safe_config(self) -> dict[str, Any]:
        """Configuration diagnostics that never include the API key."""
        return {
            "api_key_present": bool(self.api_key),
            "model": self.model,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }

    def configuration_message(self) -> str | None:
        missing = []
        if not self.api_key or not self._is_real_value(self.api_key):
            missing.append("config.py 中的 openai_api_key")
        if not self.model or not self._is_real_value(self.model):
            missing.append("config.py 中的 openai_model")
        if missing:
            return "未配置 " + " 和 ".join(missing) + "；因此未调用 LLM，也未生成替代答案。"
        return None

    def summarize(self, *, question: str, evidence_package: Mapping[str, Any]) -> dict[str, str]:
        if not self.configured:
            raise LLMConfigurationError(self.configuration_message() or "LLM 未配置")
        citations = evidence_package.get("citations") or []
        if not citations:
            raise LLMRequestError("没有可引用的 PubMed 证据，未请求 LLM 总结")
        evidence_text = "\n\n".join(
            f"[{item['citation_id']}] {item['title']}\n{item['quote']}"
            for item in citations
        )
        system = (
            "你是中国医生助手中的循证信息整理组件，不是诊断系统。"
            "只依据给定证据回答；不可补充外部事实、不可给出个体化诊断、处方或剂量。"
            "每个可验证医学陈述后必须附对应的 [citation_id]。"
            "若证据不足或相互矛盾，要明确说明。用中文，简洁分点。"
        )
        user = f"问题：{question}\n\n可用证据：\n{evidence_text}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        mode = self.api_mode.lower()
        if mode not in {"auto", "responses", "chat_completions"}:
            raise LLMRequestError("config.py 中的 openai_api_mode 只能是 auto、responses 或 chat_completions")
        if mode == "chat_completions":
            data = self._request("/chat/completions", {"model": self.model, "messages": messages})
            text = _chat_completion_text(data)
            provider = "openai_chat_completions"
        else:
            try:
                data = self._request("/responses", {
                    "model": self.model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": system}]},
                        {"role": "user", "content": [{"type": "input_text", "text": user}]},
                    ],
                })
                text = _response_text(data)
                provider = "openai_responses"
                if mode == "auto" and not text:
                    data = self._request(
                        "/chat/completions",
                        {"model": self.model, "messages": messages},
                    )
                    text = _chat_completion_text(data)
                    provider = "openai_chat_completions"
            except LLMRequestError as exc:
                # Many OpenAI-compatible relays expose only Chat Completions;
                # their Responses endpoint may return 400/404/405 or a relay
                # 5xx instead of a clean "not found" response.
                if mode != "auto" or exc.status_code not in {400, 404, 405, 500, 502, 503}:
                    raise
                try:
                    data = self._request("/chat/completions", {"model": self.model, "messages": messages})
                    text = _chat_completion_text(data)
                    provider = "openai_chat_completions"
                except LLMRequestError as fallback_exc:
                    raise LLMRequestError(
                        f"Responses 端点失败（{exc.status_code}），Chat Completions 端点也失败：{fallback_exc}",
                        status_code=fallback_exc.status_code,
                    ) from fallback_exc
        if not text:
            raise LLMRequestError("LLM 响应中没有可显示文本")
        return {"provider": provider, "model": str(self.model), "text": text}

    def _request(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        endpoint = self.base_url.rstrip("/") + path
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            # Avoid an invalid process-wide HTTP(S)_PROXY (common in the
            # research runtime: 127.0.0.1:9). The configured relay is a
            # direct HTTPS provider, just like the PubMed connector.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                error = parsed.get("error") if isinstance(parsed, Mapping) else None
                if isinstance(error, Mapping):
                    detail = str(error.get("message") or error.get("code") or "")
                elif isinstance(parsed, Mapping):
                    detail = str(parsed.get("message") or "")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            suffix = f"：{detail[:240]}" if detail else ""
            if exc.code == 426:
                message = (
                    f"中转站协议不兼容（HTTP 426，端点 {endpoint} 要求 WebSocket 升级；"
                    "v5 当前使用标准 HTTP Responses/Chat Completions，请更换支持 OpenAI 兼容 HTTP API 的中转站）"
                )
                raise LLMRequestError(message, status_code=exc.code) from exc
            message = f"中转站请求失败（HTTP {exc.code}，端点 {endpoint}）{suffix}"
            raise LLMRequestError(message, status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            reason = str(getattr(exc, "reason", exc)).strip()
            raise LLMRequestError(
                f"LLM 请求失败：{type(exc).__name__}（端点 {endpoint}；{reason[:240]}）"
            ) from exc
        if not isinstance(data, Mapping):
            raise LLMRequestError("LLM 返回格式不是 JSON 对象")
        return data


def _response_text(response: Mapping[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    fragments: list[str] = []
    output_items = response.get("output") or []
    if not isinstance(output_items, list):
        return ""
    for output in output_items:
        if not isinstance(output, Mapping):
            continue
        content_items = output.get("content") or []
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                fragments.append(content["text"])
    return "\n".join(fragments).strip()


def _chat_completion_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(str(item.get("text")) for item in content if isinstance(item, Mapping) and item.get("text")).strip()
    return ""


def _normalize_base_url(value: str) -> str:
    """Make a provider root usable for both official APIs and relay roots."""
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme in {"http", "https"} and not parsed.path:
        parsed = parsed._replace(path="/v1")
    return urlunsplit(parsed).rstrip("/")



