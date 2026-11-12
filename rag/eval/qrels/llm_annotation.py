"""LLM-assisted qrels annotation with strict validation and audit fields.

This module calls an OpenAI-compatible chat-completions API.  It is deliberately
separate from retrieval: the LLM only proposes a label, evidence span, value and
confidence; deterministic checks decide whether the row is usable.  A missing
API key/model raises ``AnnotationUnavailable`` instead of fabricating labels.

Credentials are read from the environment only:
  CHINALLM_LLM_API_KEY / OPENAI_API_KEY
  CHINALLM_LLM_BASE_URL / OPENAI_BASE_URL
  CHINALLM_LLM_MODEL / OPENAI_MODEL
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from typing import Any, Mapping, Sequence

PROMPT_VERSION = "v672-llm-annotate-v1"
TRACKS = ("external_evidence", "patient_fact", "applicability")
EXTERNAL_LABELS = (0, 1, 2, 3)
PATIENT_ANSWERABILITY = ("answerable", "unknown")
APPLICABILITY_STATUSES = ("match", "mismatch", "unknown")
CONFIDENCE_LEVELS = ("high", "medium", "low")


class AnnotationUnavailable(RuntimeError):
    """Raised when no API key/model is configured or the client is unavailable."""


class AnnotationError(RuntimeError):
    """Raised when the API response is missing, malformed, or fails validation."""


@dataclass
class LLMAnnotator:
    client: Any | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 900
    timeout_seconds: float = 60.0
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.model = self.model or os.environ.get("CHINALLM_LLM_MODEL") or os.environ.get("OPENAI_MODEL")
        self.base_url = self.base_url or os.environ.get("CHINALLM_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or None
        self.api_key = self.api_key or os.environ.get("CHINALLM_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if self.client is None:
            if not self.model:
                raise AnnotationUnavailable("未配置 LLM 模型：设置 CHINALLM_LLM_MODEL（或 OPENAI_MODEL）")
            if not self.api_key:
                raise AnnotationUnavailable("未配置 LLM API key：设置 CHINALLM_LLM_API_KEY（或 OPENAI_API_KEY）")
            try:
                from openai import OpenAI
            except Exception as exc:  # pragma: no cover - dependency failure
                raise AnnotationUnavailable("openai 包不可用；无法调用 LLM 标注") from exc
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )

    def annotate(
        self,
        *,
        track: str,
        question: str,
        candidate: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if track not in TRACKS:
            raise ValueError(f"unknown track: {track}")
        candidate_text = str(candidate.get("text") or candidate.get("abstract") or "")
        candidate_id = str(candidate.get("id") or candidate.get("document_id") or candidate.get("candidate_id") or "")
        prompt = build_prompt(track=track, question=question, candidate=candidate, context=context)
        cache_key = hashlib.sha256(json.dumps({
            "prompt_version": PROMPT_VERSION, "model": self.model, "prompt": prompt,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if cache_key in self._cache:
            cached = dict(self._cache[cache_key])
            cached["cached"] = True
            return cached

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _system_prompt(track)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise AnnotationError(f"LLM API 调用失败：{type(exc).__name__}") from exc
        content = _response_text(response)
        payload = _extract_json(content)
        result = validate_annotation(
            track=track,
            payload=payload,
            candidate_text=candidate_text,
            candidate_id=candidate_id,
            question=question,
            model=str(self.model),
        )
        result["cached"] = False
        self._cache[cache_key] = result
        return result


def build_prompt(
    *, track: str, question: str, candidate: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> str:
    text = str(candidate.get("text") or candidate.get("abstract") or "")
    title = str(candidate.get("title") or "")
    context_text = json.dumps(context or {}, ensure_ascii=False, sort_keys=True)
    if track == "external_evidence":
        task = (
            "判断候选文档对该临床问题的相关性。label 取 0/1/2/3："
            "0=不相关；1=主题沾边但不回答；2=相关；3=直接支持。"
            "evidence_span 必须是候选原文的连续片段；label=0 时可为空。"
        )
    elif track == "patient_fact":
        task = (
            "判断候选病历事实能否回答该问题。answerability 取 answerable/unknown；"
            "answerable 时必须给出 answer_value 和 evidence_span；"
            "unknown 时 answer_value 为 null，不要猜阴性。"
        )
    else:
        task = (
            "判断患者事实是否满足该指南条件。status 取 match/mismatch/unknown；"
            "缺记录、单位冲突、待回报一律 unknown，不得判 mismatch；"
            "evidence_span 必须是候选原文的连续片段。"
        )
    return (
        f"任务：{task}\n\n"
        f"问题：{question}\n\n"
        f"候选 ID：{candidate.get('id') or candidate.get('document_id') or ''}\n"
        f"候选标题：{title}\n"
        f"候选原文：{text}\n\n"
        f"补充上下文（可能为空）：{context_text}\n\n"
        "只输出一个 JSON 对象，不要 Markdown，不要解释。字段："
        "label（external_evidence 用 0-3）、answerability（patient_fact 用）、"
        "status（applicability 用）、answer_value、evidence_span、reason、"
        "confidence（high/medium/low）、abstain（bool）。"
    )


def validate_annotation(
    *, track: str, payload: Mapping[str, Any], candidate_text: str,
    candidate_id: str, question: str, model: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AnnotationError("LLM 返回不是 JSON 对象")
    confidence = str(payload.get("confidence") or "low").strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"
    span = str(payload.get("evidence_span") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    abstain = bool(payload.get("abstain"))
    result: dict[str, Any] = {
        "candidate_id": candidate_id,
        "track": track,
        "question": question,
        "label_source": "llm",
        "annotator_type": "llm",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "confidence": confidence,
        "reason": reason,
        "abstain": abstain,
        "evidence_span": span,
    }
    if track == "external_evidence":
        try:
            label = int(payload.get("label"))
        except (TypeError, ValueError):
            raise AnnotationError("external_evidence label 必须是 0-3 整数") from None
        if label not in EXTERNAL_LABELS:
            raise AnnotationError("external_evidence label 越界")
        result["label"] = label
    elif track == "patient_fact":
        answerability = str(payload.get("answerability") or "").strip().lower()
        if answerability not in PATIENT_ANSWERABILITY:
            raise AnnotationError("patient_fact answerability 必须是 answerable/unknown")
        result["answerability"] = answerability
        result["answer_value"] = payload.get("answer_value")
    else:
        status = str(payload.get("status") or "").strip().lower()
        if status not in APPLICABILITY_STATUSES:
            raise AnnotationError("applicability status 必须是 match/mismatch/unknown")
        result["status"] = status
        result["observed_value"] = payload.get("observed_value")

    span_exists = _span_exists(span, candidate_text)
    numeric_ok = _numeric_ok(result.get("answer_value"), span or candidate_text)
    result["validation"] = {
        "span_exists": span_exists,
        "numeric_ok": numeric_ok,
        "has_candidate_text": bool(candidate_text.strip()),
    }
    span_required = (
        (track == "external_evidence" and result.get("label", 0) > 0)
        or (track == "patient_fact" and result.get("answerability") == "answerable")
        or (track == "applicability" and result.get("status") != "unknown")
    )
    result["needs_review"] = (
        not candidate_text.strip()
        or (bool(span) and not span_exists)
        or (span_required and not span)
        or not numeric_ok
        or confidence == "low"
        or abstain
    )
    return result


def _system_prompt(track: str) -> str:
    return (
        "你是医学检索评测标注员。只依据候选原文判断，不补充外部知识，不猜缺失信息。"
        "输出严格 JSON；证据必须逐字来自候选原文。"
        f"当前 track={track}。"
    )


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception:
        try:
            content = response["choices"][0]["message"]["content"]
        except Exception:
            raise AnnotationError("LLM 响应缺少 choices/message/content") from None
    if isinstance(content, list):
        content = "".join(str(item.get("text", item)) if isinstance(item, Mapping) else str(item) for item in content)
    text = str(content or "").strip()
    if not text:
        raise AnnotationError("LLM 返回空内容")
    return text


def _extract_json(text: str) -> Mapping[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AnnotationError("LLM 返回不是可解析 JSON") from None
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            raise AnnotationError("LLM 返回不是可解析 JSON") from None
    if not isinstance(value, Mapping):
        raise AnnotationError("LLM 返回 JSON 不是对象")
    return value


def _span_exists(span: str, text: str) -> bool:
    if not span:
        return False
    return _normalize(span) in _normalize(text)


def _numeric_ok(value: Any, text: str) -> bool:
    if value is None or isinstance(value, bool):
        return True
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if match is None:
        return True
    number = match.group(0).rstrip("0").rstrip(".") if "." in match.group(0) else match.group(0)
    return number in re.sub(r"\s+", "", text)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()
