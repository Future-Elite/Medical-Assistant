"""Dependency-free fielded BM25 for reproducible RAG retrieval experiments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import QueryPlan, SourceDocument

_ASCII_RE = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> tuple[str, ...]:
    """共享分词器：ASCII 词 + CJK 二元组。

    - ASCII：小写词（字母/数字，允许 -./ 连接，如 "covid-19"、"38.9"）。
    - CJK：按连续中文串切分并生成重叠二元组（“新生儿黄疸” -> 新生/生儿/儿黄/黄疸）；
      单字串保留自身。与 patient_retrieval/evidence 的分词粒度一致，避免单字
      （的/是/和）造成的噪声匹配。
    - 纯数字 token（"2024"）不参与匹配，避免年份/序号误串；
      带标点数值（"38.9"、"145/92"）保留。
    """
    string = str(text)
    tokens: list[str] = []
    for match in _ASCII_RE.finditer(string):
        token = match.group(0).lower()
        if not token.isdigit():
            tokens.append(token)
    for match in _CJK_RE.finditer(string):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(tokens)


@dataclass(frozen=True)
class BM25Score:
    score: float
    matched_terms: tuple[str, ...]
    matched_fields: tuple[str, ...]
    matched_query: str


def score_documents(
    plan: QueryPlan,
    documents: Iterable[SourceDocument],
    *,
    field_weights: Mapping[str, float] | None = None,
    k1: float = 1.2,
    b: float = 0.75,
) -> dict[str, BM25Score]:
    docs = tuple(documents)
    weights = dict(field_weights or {"title": 2.5, "mesh": 2.0, "keywords": 1.5, "text": 1.0, "population": 1.0})
    token_fields = {doc.identity_key(): {field: tokenize(value) for field, value in doc.field_values().items()} for doc in docs}
    idf: dict[tuple[str, str], float] = {}
    for field in weights:
        df: dict[str, int] = {}
        for fields in token_fields.values():
            for token in set(fields.get(field, ())):
                df[token] = df.get(token, 0) + 1
        for token, count in df.items():
            idf[(field, token)] = math.log(1.0 + (len(docs) - count + 0.5) / (count + 0.5))
    averages = {
        field: (sum(len(fields.get(field, ())) for fields in token_fields.values()) / max(1, len(docs)))
        for field in weights
    }
    output: dict[str, BM25Score] = {}
    for doc in docs:
        key = doc.identity_key()
        best: BM25Score | None = None
        for query in plan.variants:
            query_tokens = tuple(dict.fromkeys(tokenize(query)))
            total = 0.0
            matched: set[str] = set()
            fields_hit: set[str] = set()
            for field, weight in weights.items():
                values = token_fields[key].get(field, ())
                if not values:
                    continue
                length = len(values)
                average = averages[field] or 1.0
                counts = {token: values.count(token) for token in set(values)}
                for token in query_tokens:
                    tf = counts.get(token, 0)
                    if not tf:
                        continue
                    matched.add(token)
                    fields_hit.add(field)
                    term_idf = idf.get((field, token), math.log(1.0 + len(docs)))
                    saturation = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * length / average))
                    total += weight * term_idf * saturation
            candidate = BM25Score(total, tuple(sorted(matched)), tuple(sorted(fields_hit)), query)
            if best is None or candidate.score > best.score:
                best = candidate
        output[key] = best or BM25Score(0.0, (), (), "")
    return output

