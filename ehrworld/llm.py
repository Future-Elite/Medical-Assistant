from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

from dotenv import dotenv_values
from openai import APIError, OpenAI
from pydantic import BaseModel, ValidationError

from .io import ROOT
from .model_policy import request_options, thinking_policy

T = TypeVar("T", bound=BaseModel)


class ModelResponseError(RuntimeError):
    pass


class ContextLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class APIConfig:
    base_url: str = ""
    model: str = "Qwen3.8-27B"
    api_key: str = field(default="", repr=False)
    timeout: float = 120
    max_tokens: int = 4096
    temperature: float = 0
    json_mode: bool = False
    repair_attempts: int = 1
    max_input_chars: int = 240000

    @classmethod
    def from_env(cls) -> "APIConfig":
        # Do not search parent directories for .env, or modify the global environment.
        values = {**dotenv_values(ROOT / ".env"), **os.environ}
        key = values.get("EHRWORLD_API_KEY") or values.get("OPENAI_API_KEY", "")
        if not key or key == "replace-with-your-key":
            raise ValueError("Set EHRWORLD_API_KEY in the project .env or process environment")
        base_url = (values.get("EHRWORLD_BASE_URL") or "").strip()
        if not base_url:
            raise ValueError("Set EHRWORLD_BASE_URL in the project .env or process environment")
        return cls(
            api_key=key,
            base_url=base_url,
            model=values.get("EHRWORLD_MODEL", cls.model),
            timeout=float(values.get("EHRWORLD_TIMEOUT", 120)),
            max_tokens=int(values.get("EHRWORLD_MAX_TOKENS", 4096)),
            temperature=float(values.get("EHRWORLD_TEMPERATURE", 0)),
            json_mode=values.get("EHRWORLD_JSON_MODE", "0") == "1",
            max_input_chars=int(values.get("EHRWORLD_MAX_INPUT_CHARS", 240000)),
        )


def parse_json(text: str, *, strict: bool = False) -> dict:
    """Accept bare or fenced JSON, and gateways that prepend a short preamble."""
    if strict:
        # Never silently keep the last of two values for the same lab.
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        def invalid_constant(_):
            raise ValueError('Non-finite JSON number')
        content = text.strip()
        if content.startswith('```') and content.endswith('```'):
            content = content.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        obj = json.loads(content, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
        if not isinstance(obj, dict):
            raise ModelResponseError('Response must be one JSON object')
        return obj
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                obj, _ = decoder.raw_decode(text[index:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    raise ModelResponseError("Response contains no valid JSON object")


class JSONClient:
    def __init__(self, config: APIConfig, client=None):
        self.config = config
        if client is None and not config.base_url.strip():
            raise ValueError("An explicit API base URL is required")
        self.client = client or OpenAI(base_url=config.base_url, api_key=config.api_key,
                                       timeout=config.timeout, max_retries=2)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def close(self):
        self.client.close()

    def request(self, system: str, payload: dict, schema: type[T], *, operation: str,
                validator: Callable[[T], None] | None = None) -> T:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)}]
        for attempt in range(self.config.repair_attempts + 1):
            if sum(len(m["content"]) for m in messages) > self.config.max_input_chars:
                raise ContextLimitError("Full history exceeds the configured input character guard; no history was truncated")
            kwargs = dict(model=self.config.model, messages=messages,
                          temperature=self.config.temperature, max_tokens=self.config.max_tokens,
                          **request_options(self.config.model))
            if thinking_policy(self.config.model)['enabled']:
                kwargs.pop('temperature', None)
            if self.config.json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            started = time.perf_counter()
            stat = {"operation": operation, "attempt": attempt + 1, "success": False,
                    "prompt_tokens": 0, "completion_tokens": 0}
            content = ""
            try:
                response = self.client.chat.completions.create(**kwargs)
                usage = response.usage
                if usage:
                    stat.update(prompt_tokens=usage.prompt_tokens or 0,
                                completion_tokens=usage.completion_tokens or 0)
                if not response.choices:
                    raise ModelResponseError("Empty response choices")
                choice = response.choices[0]
                content = choice.message.content or ""
                if choice.finish_reason == "length":
                    raise ModelResponseError("Response truncated; increase EHRWORLD_MAX_TOKENS")
                parsed = schema.model_validate(parse_json(content, strict=getattr(schema, '__pydantic_root_model__', False)))
                if validator:
                    validator(parsed)
                stat["success"] = True
                return parsed
            except APIError as exc:
                # Gateway errors may echo credentials/patient text. Never forward their body.
                status = getattr(exc, "status_code", None)
                stat.update(error_type=type(exc).__name__, http_status=status)
                network_blocked = "当前网络环境不支持" in str(getattr(exc, "body", ""))
                hint = "gateway reports unsupported network environment" if network_blocked else "check endpoint/authentication"
                raise ModelResponseError(f"LLM request failed ({type(exc).__name__}, HTTP {status}); {hint}") from None
            except (ValidationError, ValueError, ModelResponseError) as exc:
                if attempt == self.config.repair_attempts:
                    raise ModelResponseError(f"Invalid structured response for {operation} after {attempt + 1} attempts") from None
                # ValidationError strings contain actual patient values; send only schema locations.
                if isinstance(exc, ValidationError):
                    reason = [{"field": str(e["loc"]), "type": e["type"]} for e in exc.errors()]
                else:
                    reason = "Action IDs, units, value types, allowed label values, numeric bounds or JSON shape violate the contract."
                messages.extend([
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Correct the JSON response. Schema errors: {reason}. Return JSON only."},
                ])
            finally:
                stat["elapsed_seconds"] = time.perf_counter() - started
                with self._lock:
                    self.calls.append(stat)
        raise AssertionError("unreachable")

    def statistics(self) -> dict:
        with self._lock:
            calls = list(self.calls)
        seconds = sum(c["elapsed_seconds"] for c in calls)
        generated = sum(c["completion_tokens"] for c in calls)
        return {
            "requests": len(calls), "successful_requests": sum(c["success"] for c in calls),
            "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
            "completion_tokens": generated, "request_seconds_sum": seconds,
            "mean_request_seconds": seconds / len(calls) if calls else None,
            "completion_tokens_per_request_second": generated / seconds if seconds else None,
            "model": self.config.model, "base_url": self.config.base_url,
            "enable_thinking": thinking_policy(self.config.model)['enabled'],
            "reasoning_effort": thinking_policy(self.config.model)['effort'],
            "failures": [{"error_type": c.get("error_type", "invalid_response"), "http_status": c.get("http_status")}
                         for c in calls if not c["success"]],
        }
