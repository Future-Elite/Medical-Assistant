"""Deterministic key-sentence extraction for RAG v5 citations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class EvidenceSentence:
    """A source-preserving, query-ranked sentence span."""

    sentence_id: str
    sentence_index: int
    text: str
    start: int
    end: int
    score: float
    locator: str
    matched_terms: tuple[str, ...] = ()


def split_sentence_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Split mixed Chinese/English text without losing source offsets."""
    if not text:
        return ()
    spans: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        boundary = char in "。！？!?；;\n"
        if char == ".":
            next_char = text[index + 1] if index + 1 < length else ""
            boundary = not next_char or next_char.isspace()
        if boundary:
            end = index + 1
            while end < length and text[end].isspace():
                end += 1
            _append_span(spans, text, start, index + 1)
            start = end
            index = end
            continue
        index += 1
    _append_span(spans, text, start, length)
    return tuple(spans)


def extract_key_evidence_sentences(
    document_id: str,
    text: str,
    queries: Iterable[str],
    *,
    budget: int = 3,
) -> tuple[EvidenceSentence, ...]:
    """Return the highest-overlap sentence spans for one retrieved document.

    This is a locating aid, not a factual verifier. Only sentences with a
    positive lexical overlap are selected; an empty result means this document
    did not expose a query-matched sentence under this transparent heuristic.
    """
    if budget < 1:
        return ()
    query_terms = set().union(*(_terms(query) for query in queries if query))
    if not query_terms:
        return ()
    candidates: list[tuple[float, int, int, str, tuple[str, ...]]] = []
    for sentence_index, (start, end, sentence) in enumerate(split_sentence_spans(text)):
        sentence_terms = _terms(sentence)
        matched = tuple(sorted(query_terms & sentence_terms))
        if not matched:
            continue
        score = len(matched) / max(1, len(query_terms))
        candidates.append((score, sentence_index, start, sentence, matched))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[:budget]
    return tuple(
        EvidenceSentence(
            sentence_id=f"{document_id}:sent-{sentence_index + 1}",
            sentence_index=sentence_index,
            text=sentence,
            start=start,
            end=start + len(sentence),
            score=round(score, 6),
            locator=f"sentence {sentence_index + 1} (chars {start}-{start + len(sentence)})",
            matched_terms=matched,
        )
        for score, sentence_index, start, sentence, matched in selected
    )


def _append_span(spans: list[tuple[int, int, str]], text: str, start: int, end: int) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end, text[start:end]))


def _terms(text: str) -> set[str]:
    ascii_terms = set(_TOKEN_RE.findall(text.lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return ascii_terms
