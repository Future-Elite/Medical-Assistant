"""Source-preserving key sentence location for RAG citations."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*|[\u4e00-\u9fff]", re.I)


@dataclass(frozen=True)
class EvidenceSentence:
    sentence_id: str
    sentence_index: int
    text: str
    start: int
    end: int
    score: float
    locator: str
    matched_terms: tuple[str, ...] = ()


def split_sentence_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    if not text:
        return ()
    spans: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        boundary = char in "。！？!?；;\n"
        if char == ".":
            next_char = text[index + 1] if index + 1 < len(text) else ""
            boundary = not next_char or next_char.isspace()
        if boundary:
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            _append_span(spans, text, start, index + 1)
            start = end
            index = end
            continue
        index += 1
    _append_span(spans, text, start, len(text))
    return tuple(spans)


def extract_key_evidence_sentences(document_id: str, text: str, queries: Iterable[str], *, budget: int = 3) -> tuple[EvidenceSentence, ...]:
    if budget < 1:
        return ()
    query_terms = set().union(*(_terms(query) for query in queries if query))
    if not query_terms:
        return ()
    candidates: list[tuple[float, int, int, int, str, tuple[str, ...]]] = []
    for sentence_index, (start, end, sentence) in enumerate(split_sentence_spans(text)):
        matched = tuple(sorted(query_terms & _terms(sentence)))
        if matched:
            candidates.append((len(matched) / max(1, len(query_terms)), sentence_index, start, end, sentence, matched))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        EvidenceSentence(
            sentence_id=f"{document_id}:sent-{sentence_index + 1}", sentence_index=sentence_index,
            text=sentence, start=start, end=end, score=round(score, 6),
            locator=f"sentence {sentence_index + 1} (chars {start}-{end})", matched_terms=matched,
        )
        for score, sentence_index, start, end, sentence, matched in candidates[:budget]
    )


def _append_span(spans, text: str, start: int, end: int) -> None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end, text[start:end]))


def _terms(text: str) -> set[str]:
    tokens = set(_TOKEN_RE.findall(str(text).lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", str(text)))
    tokens.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return tokens
