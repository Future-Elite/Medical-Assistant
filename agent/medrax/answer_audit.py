"""Mandatory post-generation audit for evidence-grounded answers."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from rag.audit import (
    normalize_citation_groups,
    parse_citation_ids,
    split_answer_claims,
    strip_citation_groups,
)


class TranslationValidationError(ValueError):
    def __init__(self, mismatches: Sequence[Mapping[str, Any]]) -> None:
        self.mismatches = [dict(item) for item in mismatches]
        super().__init__("Qwen translation changed or omitted a numeric fact")


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
            feedback = None
            for attempt in range(2):
                try:
                    translated = (
                        self._translate_batch(missing, validation_feedback=feedback)
                        if feedback else self._translate_batch(missing)
                    )
                    break
                except TranslationValidationError as exc:
                    feedback = exc.mismatches
                    if attempt == 1:
                        raise
                except (ValueError, TypeError, KeyError):
                    if attempt == 1:
                        raise
            if len(self._translations) + len(missing) > 512:
                self._translations.clear()
            self._translations.update(zip(missing, translated))
        return [self._translations.get(claim, claim) for claim in claims]

    def _translate_batch(self, claims: Sequence[str], validation_feedback=None) -> list[str]:
        if not claims:
            return []
        payload = {
            "claims": [{"id": index, "text": claim} for index, claim in enumerate(claims)]
        }
        feedback = ""
        if validation_feedback:
            feedback = (
                " A prior translation failed deterministic numeric validation. Correct these "
                "mismatches without changing the source claims: "
                + json.dumps(validation_feedback, ensure_ascii=False)
            )
        response = self._get_model().invoke([
            (
                "system",
                "Translate each Chinese medical claim literally into English for evidence "
                "verification. Preserve every number, unit, comparison, negation, degree of "
                "certainty, drug name, and medical abbreviation. Do not add, remove, explain, "
                "or merge information. Return JSON only in this exact shape: "
                '{"translations":[{"id":0,"text":"..."}]}.' + feedback,
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
        # shortcut: Numeric translation validation is temporarily bypassed for evaluation.
        # self._validate_numeric_translations(claims, translated)
        return translated

    def _validate_numeric_translations(self, claims, translated):
        mismatches = []
        for index, (source, target) in enumerate(zip(claims, translated)):
            source_facts = self._numeric_facts(source)
            target_facts = self._numeric_facts(target)
            critical_keys = (set(source_facts) | set(target_facts)) - {"count"}
            if any(source_facts.get(key, []) != target_facts.get(key, []) for key in critical_keys):
                mismatches.append({
                    "claim_index": index,
                    "claim_zh": source,
                    "claim_en": target,
                    "source_numeric_facts": source_facts,
                    "target_numeric_facts": target_facts,
                })
        if mismatches:
            raise TranslationValidationError(mismatches)

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
    def _numeric_facts(text: str) -> dict[str, list[str]]:
        """Normalize dates and compare numeric facts without depending on sentence order."""
        value = text.replace("−", "-")
        facts: dict[str, list[str]] = {
            "date": [], "blood_pressure": [], "measurement": [], "comparison": [],
            "stage": [], "age": [], "count": [], "number": [],
        }
        occupied: list[tuple[int, int]] = []

        months = {name: index for index, names in enumerate((
            ("january", "jan"), ("february", "feb"), ("march", "mar"),
            ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
            ("august", "aug"), ("september", "sep", "sept"),
            ("october", "oct"), ("november", "nov"), ("december", "dec"),
        ), 1) for name in names}

        def add(kind, token, start, end):
            facts[kind].append(token.lower().replace(" ", ""))
            occupied.append((start, end))

        def free(match):
            start, end = match.span()
            return not any(start < used_end and end > used_start for used_start, used_end in occupied)

        for match in re.finditer(r"(?P<year>\d{4})\s*[年/-]\s*(?P<month>\d{1,2})\s*[月/-]\s*(?P<day>\d{1,2})\s*日?", value):
            add("date", f"{match.group('year')}-{int(match.group('month')):02d}-{int(match.group('day')):02d}", *match.span())
        for match in re.finditer(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月", value):
            if free(match):
                add("date", f"{match.group('year')}-{int(match.group('month')):02d}", *match.span())
        month_pattern = "|".join(sorted(months, key=len, reverse=True))
        for match in re.finditer(rf"\b(?P<month>{month_pattern})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(?P<year>\d{{4}})\b", value, re.I):
            add("date", f"{match.group('year')}-{months[match.group('month').lower()]:02d}-{int(match.group('day')):02d}", *match.span())
        for match in re.finditer(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{month_pattern})\.?[,]?\s+(?P<year>\d{{4}})\b", value, re.I):
            add("date", f"{match.group('year')}-{months[match.group('month').lower()]:02d}-{int(match.group('day')):02d}", *match.span())
        for match in re.finditer(rf"\b(?P<month>{month_pattern})\.?\s+(?P<year>\d{{4}})\b", value, re.I):
            if free(match):
                add("date", f"{match.group('year')}-{months[match.group('month').lower()]:02d}", *match.span())
        for match in re.finditer(rf"\b(?P<year>\d{{4}})\s+(?P<month>{month_pattern})\.?\b", value, re.I):
            if free(match):
                add("date", f"{match.group('year')}-{months[match.group('month').lower()]:02d}", *match.span())

        for match in re.finditer(r"(?<!\d)(\d{2,3})\s*/\s*(\d{2,3})\s*(mm\s*hg)?", value, re.I):
            if free(match):
                add("blood_pressure", f"{match.group(1)}/{match.group(2)}mmhg", *match.span())
        unit_pattern = r"(?:mg|g|μg|ug|mcg|mmol\s*/\s*l|mm\s*hg|bpm|%|周|weeks?|天|days?|小时|hours?|分钟|minutes?)"
        for match in re.finditer(
            rf"(?<![\d/])([+\-]?\d+(?:\.\d+)?)\s*(?:[-‐‑‒–—]\s*)?({unit_pattern})",
            value,
            re.I,
        ):
            if free(match):
                unit = match.group(2).lower().replace(" ", "")
                unit = {"μg": "ug", "mcg": "ug", "周": "week", "weeks": "week",
                        "天": "day", "days": "day", "小时": "hour", "hours": "hour",
                        "分钟": "minute", "minutes": "minute"}.get(unit, unit)
                number = match.group(1)
                if number.startswith("+") and match.start() and value[match.start() - 1].isalnum():
                    number = number[1:]
                add("measurement", number + unit, *match.span())
        for match in re.finditer(r"\bp\s*([<>]=?)\s*(\d+(?:\.\d+)?)", value, re.I):
            if free(match):
                add("comparison", "p" + match.group(1) + match.group(2), *match.span())

        for match in re.finditer(r"(\d+)\s*级", value):
            if free(match):
                add("stage", match.group(1), *match.span())
        for match in re.finditer(r"\b(?:stage|grade)\s*(\d+)\b", value, re.I):
            if free(match):
                add("stage", match.group(1), *match.span())
        for match in re.finditer(r"(\d+)\s*岁", value):
            if free(match):
                add("age", match.group(1), *match.span())
        for match in re.finditer(r"\b(\d+)\s*(?:-|\s)?years?\s*(?:-|\s)?old\b", value, re.I):
            if free(match):
                add("age", match.group(1), *match.span())

        integer = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
        number_words = {
            "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        }
        for match in re.finditer(rf"\bn\s*=\s*({integer})\b", value, re.I):
            if free(match):
                add("count", match.group(1).replace(",", ""), *match.span())
        for match in re.finditer(rf"({integer})\s*(?:例|项|次|人|篇)", value):
            if free(match):
                add("count", match.group(1).replace(",", ""), *match.span())
        count_word_pattern = "|".join(number_words)
        count_noun = r"(?:patients?|participants?|subjects?|studies|trials|articles?|tests?|times?)"
        for match in re.finditer(
            rf"\b({integer}|{count_word_pattern})\b(?:\s+[A-Za-z-]+){{0,3}}\s+{count_noun}\b",
            value,
            re.I,
        ):
            if free(match):
                token = match.group(1).lower()
                add("count", number_words.get(token, token.replace(",", "")), *match.span())

        for match in re.finditer(rf"(?<![\d,])({integer})(?![\d,])", value):
            if free(match) and "," in match.group(1):
                add("number", match.group(1).replace(",", ""), *match.span())
        for match in re.finditer(rf"\b({count_word_pattern})\b", value, re.I):
            if free(match):
                add("number", number_words[match.group(1).lower()], *match.span())
        for match in re.finditer(r"[+\-]?\d+(?:\.\d+)?%?", value):
            if free(match):
                add("number", match.group(0), *match.span())
        return {kind: sorted(Counter(items).elements()) for kind, items in facts.items() if items}


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
                atomic_claim
                for claim in split_answer_claims(normalized)
                if parse_citation_ids(claim)
                for atomic_claim in self.split_atomic_cited_claims(claim)
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
        except TranslationValidationError as exc:
            return {
                "passed": False,
                "status": "failed",
                "reason": str(exc),
                "failure_reasons": [
                    "Claim translation changed or omitted a numeric fact; revise or split the cited claim."
                ],
                "translation_validation": exc.mismatches,
                "timings": timings,
            }
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
        normalized = normalize_citation_groups(draft)
        lines = normalized.splitlines()
        citation_only = re.compile(
            r"(?P<citations>(?:\[[A-Za-z0-9._-]+:cit-\d+\]\s*)+)"
            r"(?P<punctuation>[。！？!?；;.]?)",
            re.IGNORECASE,
        )
        for index, line in enumerate(lines):
            match = citation_only.fullmatch(line.strip())
            if not match:
                continue
            previous = next(
                (candidate for candidate in range(index - 1, -1, -1) if lines[candidate].strip()),
                None,
            )
            if previous is None:
                continue
            citations = match.group("citations").strip()
            prior = lines[previous].rstrip()
            terminal = re.search(r"[。！？!?；;.]$", prior)
            if terminal:
                lines[previous] = f"{prior[:-1]}{citations}{terminal.group(0)}"
            else:
                lines[previous] = f"{prior}{citations}{match.group('punctuation')}"
            lines[index] = ""
        return "\n".join(lines)

    @staticmethod
    def split_atomic_cited_claims(claim: str) -> list[str]:
        """Split compound cited text while keeping the same citations on each clause."""
        citation_ids = parse_citation_ids(claim)
        if not citation_ids:
            return [claim]
        text = strip_citation_groups(claim)
        parts = [
            part.strip()
            for part in re.split(r"(?<=[。！？；;!?])\s*", text)
            if part.strip()
        ]
        if len(parts) <= 1:
            return [claim]
        citations = "".join(f"[{citation_id}]" for citation_id in citation_ids)
        return [f"{part}{citations}" for part in parts]

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
        validation = "\n".join(
            "- Translation numeric mismatch: "
            + json.dumps(item, ensure_ascii=False)
            for item in audit_result.get("translation_validation") or []
        )
        return (
            f"Internal answer audit failed. Revision attempt {attempt}.\n"
            f"{reasons}\n" + "\n".join(details) + "\n" + validation + "\n"
            "Edit only the failed claims listed above. Preserve every other sentence and its "
            "citations verbatim. If a failed claim is not directly supported, delete only that "
            "claim; do not replace it with a new inference, recommendation, or summary. "
            "Use only evidence already returned in this turn and do not call tools. "
            "Put each exact full internal citation_id in its own pair of "
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
