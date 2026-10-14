"""Mandatory post-generation audit for evidence-grounded answers."""

from __future__ import annotations

import json
import os
import re
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from rag.audit import (
    normalize_citation_groups,
    parse_citation_ids,
    split_answer_claims,
    strip_citation_groups,
)


class QwenClaimTranslator:
    """Translate cited Chinese claims to English for MiniCheck only."""

    def __init__(self) -> None:
        self._model = None
        self._translations: dict[str, str] = {}

    def __call__(self, claims: Sequence[str]) -> list[str]:
        claims = [str(claim).strip() for claim in claims]
        missing = list(dict.fromkeys(
            claim for claim in claims
            if re.search(r"[\u3400-\u9fff]", claim) and claim not in self._translations
        ))
        if missing:
            for attempt in range(2):
                try:
                    translated = self._translate_batch(missing)
                    break
                except (ValueError, TypeError, KeyError):
                    if attempt == 1:
                        raise
            if len(self._translations) + len(missing) > 512:
                self._translations.clear()
            self._translations.update(zip(missing, translated))
        return [self._translations.get(claim, claim) for claim in claims]

    def _translate_batch(self, claims: Sequence[str]) -> list[str]:
        if not claims:
            return []
        payload = {
            "claims": [{"id": index, "text": claim} for index, claim in enumerate(claims)]
        }
        response = self._get_model().invoke([
            (
                "system",
                "Translate each Chinese medical claim literally into English for evidence "
                "verification. Preserve every number, unit, comparison, negation, degree of "
                "certainty, drug name, and medical abbreviation. Do not add, remove, explain, "
                "or merge information. Return JSON only in this exact shape: "
                '{"translations":[{"id":0,"text":"..."}]}.',
            ),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ])
        data = json.loads(self._strip_json_fence(self._message_text(response.content)))
        rows = data.get("translations") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Qwen translation did not return a translations list")
        by_id = {
            row.get("id"): str(row.get("text") or "").strip()
            for row in rows
            if isinstance(row, dict)
        }
        translated = [by_id.get(index, "") for index in range(len(claims))]
        if any(not item for item in translated):
            raise ValueError("Qwen translation omitted one or more claims")
        for source, target in zip(claims, translated):
            if self._numbers(source) != self._numbers(target):
                raise ValueError("Qwen translation changed a numeric value")
        return translated

    def _get_model(self):
        if self._model is None:
            from langchain_openai import ChatOpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for Qwen claim translation")
            self._model = ChatOpenAI(
                model=os.getenv("OPENAI_MODEL", "qwen3-vl-plus"),
                api_key=api_key,
                base_url=os.getenv(
                    "OPENAI_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
                temperature=0,
            )
        return self._model

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "") if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        text = text.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        return match.group(1) if match else text

    @staticmethod
    def _numbers(text: str) -> list[str]:
        return re.findall(r"[+\-\u2212]?\d+(?:\.\d+)?%?", text.replace("−", "-"))


class AnswerAuditModule:
    """Translate cited claims, then use the RAG auditor to approve a Qwen draft."""

    def __init__(
        self,
        audit_grounded_answer: Callable[..., Mapping[str, Any]],
        translate_claims: Callable[[Sequence[str]], Sequence[str]],
        max_revisions: int = 2,
    ) -> None:
        self.audit_grounded_answer = audit_grounded_answer
        self.translate_claims = translate_claims
        self.max_revisions = max_revisions

    def audit(self, draft: str, retrieval_id: str | None, on_progress=None) -> dict[str, Any]:
        timings = {}
        def progress(stage):
            if on_progress:
                on_progress(stage)

        if not retrieval_id:
            return {
                "passed": True,
                "status": "not_applicable",
                "reason": "No external evidence was retrieved for this answer.",
            }
        try:
            normalized = self.normalize_citations(draft)
            cited_claims = [
                claim for claim in split_answer_claims(normalized) if parse_citation_ids(claim)
            ]
            verifier_claims = [strip_citation_groups(claim) for claim in cited_claims]
            progress("translation_started")
            started = perf_counter()
            translations = list(self.translate_claims(verifier_claims))
            timings["translation_seconds"] = round(perf_counter() - started, 3)
            progress("translation_completed")
            if len(translations) != len(cited_claims):
                raise ValueError("Qwen translation count does not match cited claim count")
            translated_rows = [
                {
                    "claim_zh": source_claim,
                    "claim_en": translated,
                    "citation_ids": list(parse_citation_ids(source_claim)),
                }
                for source_claim, translated in zip(cited_claims, translations)
            ]
            progress("verification_started")
            started = perf_counter()
            result = dict(
                self.audit_grounded_answer(
                    retrieval_id, normalized, claim_records=translated_rows
                )
            )
            timings["verification_seconds"] = round(perf_counter() - started, 3)
            progress("verification_completed")
            if len(result.get("claims") or []) != len(translated_rows):
                raise ValueError("Audit result count does not match input claim count")
        except Exception as exc:
            return {
                "passed": False,
                "status": "error",
                "reason": f"Answer audit failed: {type(exc).__name__}: {exc}",
                "failure_reasons": [f"Claim translation or answer audit failed: {exc}"],
                "timings": timings,
            }

        claims = list(result.get("claims") or [])
        valid_claims = [claim for claim in claims if claim.get("valid_citation_ids")]
        invalid_claims = [claim for claim in claims if claim.get("invalid_citation_ids")]
        unsupported_claims = [
            claim for claim in valid_claims if claim.get("ragtruth_label") != "supported"
        ]
        verifier_available = bool((result.get("verifier") or {}).get("available"))
        passed = (
            verifier_available
            and bool(valid_claims)
            and not invalid_claims
            and not unsupported_claims
        )
        result.update({
            "passed": passed,
            "status": "passed" if passed else "failed",
            "normalized_draft": normalized,
            "claim_translations": translated_rows,
            "timings": timings,
            "checked_claim_count": len(valid_claims),
            "failure_reasons": self._failure_reasons(
                verifier_available, valid_claims, invalid_claims, unsupported_claims
            ),
        })
        return result

    @staticmethod
    def normalize_citations(draft: str) -> str:
        return normalize_citation_groups(draft)

    @staticmethod
    def _failure_reasons(verifier_available, cited_claims, invalid_claims, unsupported_claims):
        reasons = []
        if not verifier_available:
            reasons.append("MiniCheck verifier is unavailable.")
        if not cited_claims:
            reasons.append("The draft contains no valid internal citation_id to audit.")
        if invalid_claims:
            reasons.append("The draft contains citation_id values outside this retrieval.")
        if unsupported_claims:
            reasons.append("One or more cited claims are not supported by their evidence.")
        return reasons

    @staticmethod
    def revision_prompt(audit_result: Mapping[str, Any], attempt: int) -> str:
        details = []
        for claim in audit_result.get("claims") or []:
            if claim.get("invalid_citation_ids") or (
                claim.get("valid_citation_ids")
                and claim.get("ragtruth_label") != "supported"
            ):
                details.append(
                    f"- {claim.get('claim_zh') or claim.get('claim', '')} "
                    f"-> {claim.get('ragtruth_label', 'failed')}"
                )
        reasons = "\n".join(
            f"- {reason}" for reason in audit_result.get("failure_reasons") or []
        )
        return (
            f"Internal answer audit failed. Revision attempt {attempt}.\n"
            f"{reasons}\n" + "\n".join(details) + "\n"
            "Revise the previous draft using only evidence already returned in this turn. "
            "Do not call tools. Put each exact full internal citation_id in its own pair of "
            "square brackets immediately after every claim it supports, for example "
            "[retrieval-...:cit-1][retrieval-...:cit-2]. Remove unsupported claims. "
            "Output only the revised draft."
        )

    @staticmethod
    def render_public_citations(draft: str, citation_map: Mapping[str, str]) -> str:
        rendered = normalize_citation_groups(draft)
        for citation_id, source_id in sorted(
            citation_map.items(), key=lambda item: len(item[0]), reverse=True
        ):
            rendered = rendered.replace(f"[{citation_id}]", f"（{source_id}）")
        return rendered
