"""Optional neural adapters (MedCPT dense / SPLADE / ColBERT / cross-encoder / multilingual-e5).

Design constraints:

- The core stays stdlib-only. This module imports ``torch``/``transformers``
  lazily, only when a model is actually used, so importing the package never
  pulls in heavy dependencies.
- Every adapter exposes ``available``; when a model or dependency is missing the
  adapter reports ``not_configured`` in the retrieval trace. Nothing here ever
  substitutes lexical heuristics for a neural model.
- Adapters implement the existing ``SemanticRetriever`` / ``Reranker`` protocols
  from ``retrieval_adapters.py`` so ``ranking.py``/``pipeline.py`` treat them
  uniformly with the deterministic test stubs.
- Adapters are read-only scorers over the candidate documents supplied by
  connectors; they do not fetch or fabricate sources.

Model routing (documented, not implied):

- ``dense``    -> MedCPT Query/Article encoders (bi-encoder cosine similarity)
- ``splade``   -> naver/splade-cocondenser-ensembledistil (sparse lexical weights)
- ``colbert``  -> colbert-ir/colbertv2.0 (late-interaction MaxSim)
- ``rerank``   -> MedCPT Cross-Encoder (query-passage pair scoring)
- ``qwen3_reranker`` -> Qwen/Qwen3-Reranker-0.6B (pointwise generative yes/no
  scoring; multilingual, the Chinese question stays visible). Local cache
  weights only unless ``RAG_ALLOW_MODEL_DOWNLOAD=1``; needs transformers>=4.51.
- ``query2doc``-> any injected text generator; a small local seq2seq model can
  serve as the demo generator. Without a generator the method is not_configured.
- ``xlingual`` -> intfloat/multilingual-e5-base. A multilingual bi-encoder that
  encodes the Chinese question and the English document into one shared space
  (query:/passage: prefixes, mean pooling, L2 normalization). Rationale from
  MMed-Bench-IR (arXiv:2606.24200): for cross-lingual medical retrieval,
  multilingual dense retrievers outperform biomedical-only encoders (trained
  on English), which in turn outperform lexical baselines; this adapter exists
  because MedCPT's encoders are English-only and cannot see the Chinese query.
- ``translate``-> any injected zh->en translator; the default is a local
  Marian model (Helsinki-NLP/opus-mt-zh-en). The translation is added as an
  extra query variant for the lexical scorers (translate-then-retrieve, the
  CLIR baseline of Translate-Distill, arXiv:2401.04810). Translation may
  corrupt medical entities (measured in the offline cross-lingual evaluation), so the pipeline
  records the translation and flags numeric mismatches; it never replaces the
  original Chinese question.

The environment endpoint for model downloads is read from ``HF_ENDPOINT``
(hf-mirror.com in this workspace); no endpoint is hardcoded here.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .models import QueryPlan, RankedDocument, SourceDocument
from .retrieval_adapters import SemanticSearchResult


def neural_dependencies_state() -> dict[str, str | bool]:
    """Report whether torch/transformers are importable (no model loading)."""
    state: dict[str, str | bool] = {"torch": False, "transformers": False, "detail": ""}
    try:
        import torch  # noqa: F401

        state["torch"] = True
    except Exception as exc:  # pragma: no cover - depends on environment
        state["detail"] = f"torch unavailable: {type(exc).__name__}"
        return state
    try:
        import transformers  # noqa: F401

        state["transformers"] = True
    except Exception as exc:  # pragma: no cover
        state["detail"] = f"transformers unavailable: {type(exc).__name__}"
    return state


# 显式允许联网下载模型的环境变量；未设置时缓存缺失直接快速失败，绝不挂在不可达的 Hub 上。
DOWNLOAD_ENV_FLAG = "RAG_ALLOW_MODEL_DOWNLOAD"


def configure_hf_offline_default() -> str:
    """默认启用 HF 离线模式，避免 transformers 的联网检查把请求挂死。

    背景（实测）：在不可达 huggingface.co 的网络里，加载一个权重为 ``.bin``
    的本地缓存模型时，transformers 会执行 ``auto_conversion`` 检查
    （``has_file`` -> Hub API），在后台线程中阻塞于 socket 连接——
    ``from_pretrained(local_files_only=True)`` 并不能阻止它，因为该检查由
    ``is_offline_mode()`` 单独控制。结果是"模型加载 4 秒完成、但进程/请求被
    永久拖住"。

    因此：除非用户显式设置 ``RAG_ALLOW_MODEL_DOWNLOAD=1``（需要联网下载），
    本模块在导入时自动 ``HF_HUB_OFFLINE=1``。用 ``setdefault`` 实现，
    用户显式设置的任何 HF_* 变量均不被覆盖。

    返回应用后的状态描述（"offline-default" 或 "download-allowed"）。
    """
    if os.environ.get(DOWNLOAD_ENV_FLAG) == "1":
        return "download-allowed"
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # huggingface_hub.constants 在导入时一次性读取 HF_HUB_OFFLINE；
    # 若 hub 已被更早的 import 装载，环境变量为时已晚，直接同步常量。
    import sys
    if "huggingface_hub.constants" in sys.modules:
        try:
            from huggingface_hub import constants as hub_constants
            hub_constants.HF_HUB_OFFLINE = True
        except Exception:  # pragma: no cover - 防御性
            pass
    return "offline-default"


CONFIGURE_STATE = configure_hf_offline_default()


def _brief_error(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail[:140]}" if detail else type(error).__name__


def _prefer_local_load(loader: Any, repo: str, *, cache_dir: str | None = None, **kwargs: Any) -> Any:
    """本地缓存优先加载模型，绝不因不可达的 Hub 挂起请求。

    - 第一步 ``local_files_only=True``：只读本地缓存、完全不触网（命中秒级成功，
      未命中立即失败）。模型文件已在 HF 缓存时，网络是否可用与检索无关。
    - 缓存缺失时默认直接报错并给出操作指引；只有显式设置
      ``RAG_ALLOW_MODEL_DOWNLOAD=1`` 才允许联网下载，且强制短超时
      （HF_HUB_ETAG_TIMEOUT / HF_HUB_DOWNLOAD_TIMEOUT），避免无限等待。
    """
    merged = dict(kwargs)
    if cache_dir:
        merged["cache_dir"] = cache_dir
    try:
        return loader.from_pretrained(repo, local_files_only=True, **merged)
    except Exception as local_error:
        if os.environ.get(DOWNLOAD_ENV_FLAG) != "1":
            raise RuntimeError(
                f"模型 {repo} 不在本地缓存；请预下载模型（HF_ENDPOINT 指向可达镜像）"
                f"或设置 {DOWNLOAD_ENV_FLAG}=1 后重试。本地加载错误：{type(local_error).__name__}"
            ) from local_error
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
        return loader.from_pretrained(repo, **merged)


def _query_text(plan: QueryPlan) -> str:
    """English query text for neural scorers (never Chinese-only fallback)."""
    parts = [*plan.english_terms, *plan.question_english_terms, *plan.mesh_terms]
    ascii_concepts = [item for item in plan.concepts if str(item).isascii()]
    for item in ascii_concepts:
        if item not in parts:
            parts.append(item)
    return " ".join(dict.fromkeys(part for part in parts if part.strip()))


def _document_text(document: SourceDocument) -> str:
    fields = document.field_values()
    mesh = str(fields.get("mesh") or "")
    text = f"{document.title}. {document.text}"
    if mesh:
        text = f"{text} {mesh}"
    return text


def _vector_cache_key(document: SourceDocument) -> str:
    return f"{document.identity_key()}|{hash(_document_text(document))}"


def _vector_cache_get(cache: dict[str, tuple[str, Any]], document: SourceDocument, limit: int):
    key = _vector_cache_key(document)
    entry = cache.get(key)
    return entry[1] if entry else None


def _vector_cache_put(cache: dict[str, tuple[str, Any]], document: SourceDocument, vector: Any, limit: int) -> None:
    if len(cache) >= limit:
        # 简单容量保护：清空重建即可，缓存只是速度优化，不影响结果。
        cache.clear()
    cache[_vector_cache_key(document)] = (document.identity_key(), vector)


@dataclass
class MedCPTDenseRetriever:
    """MedCPT bi-encoder dense retriever (cosine similarity)."""

    query_model_name: str = "ncbi/MedCPT-Query-Encoder"
    document_model_name: str = "ncbi/MedCPT-Article-Encoder"
    retriever_id: str = "medcpt-dense"
    cache_dir: str | None = None
    max_length: int = 512
    batch_size: int = 8
    _query_model: Any = field(default=None, repr=False)
    _document_model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)
    _vector_cache: dict[str, tuple[str, Any]] = field(default_factory=dict, repr=False)
    _vector_cache_limit: int = 8192

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "MedCPT 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._query_model is not None and self._document_model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.query_model_name, cache_dir=self.cache_dir)
            self._query_model = _prefer_local_load(AutoModel, self.query_model_name, cache_dir=self.cache_dir).eval()
            self._document_model = _prefer_local_load(AutoModel, self.document_model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"MedCPT 加载失败：{type(exc).__name__}。"
            return False

    def _encode(self, model: Any, texts: Sequence[str]) -> Any:
        torch = self._torch
        encoded = self._tokenizer(
            list(texts), padding=True, truncation=True, max_length=self.max_length, return_tensors="pt"
        )
        with torch.no_grad():
            output = model(**encoded)
        return output.last_hidden_state[:, 0, :]

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _query_text(plan)
        if not query:
            return SemanticSearchResult({}, self.retriever_id, self.retriever_version, None)
        with torch.no_grad():
            query_vector = self._encode(self._query_model, [query])[0]
            scores: dict[str, float] = {}
            pending: list[SourceDocument] = []
            pending_vectors: dict[str, Any] = {}
            for doc in documents:
                cached = _vector_cache_get(self._vector_cache, doc, self._vector_cache_limit)
                if cached is not None:
                    pending_vectors[doc.identity_key()] = cached
                else:
                    pending.append(doc)
            for start in range(0, len(pending), self.batch_size):
                batch = pending[start:start + self.batch_size]
                vectors = self._encode(self._document_model, [_document_text(doc) for doc in batch])
                for doc, vector in zip(batch, vectors):
                    _vector_cache_put(self._vector_cache, doc, vector, self._vector_cache_limit)
                    pending_vectors[doc.identity_key()] = vector
            for doc in documents:
                vector = pending_vectors.get(doc.identity_key())
                if vector is None:
                    continue
                sim = torch.nn.functional.cosine_similarity(vector.unsqueeze(0), query_vector.unsqueeze(0), dim=1)
                # cosine [-1,1] -> [0,1], matching the SemanticRetriever contract.
                scores[doc.identity_key()] = max(0.0, min(1.0, (float(sim[0]) + 1.0) / 2.0))
        return SemanticSearchResult(scores, self.retriever_id, self.retriever_version, None)

    @property
    def retriever_version(self) -> str:
        deps = neural_dependencies_state()
        return f"transformers={deps.get('transformers') and 'yes' or 'no'}"


@dataclass
class MedCPTReranker:
    """MedCPT Cross-Encoder reranker (query-passage pair scoring)."""

    model_name: str = "ncbi/MedCPT-Cross-Encoder"
    reranker_id: str = "medcpt-cross-encoder"
    cache_dir: str | None = None
    max_length: int = 512
    batch_size: int = 8
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        if self._model is not None:
            return True
        deps = neural_dependencies_state()
        return bool(deps["torch"] and deps["transformers"]) and self._load_error is None

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "MedCPT cross-encoder 未加载。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModelForSequenceClassification, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover
            self._load_error = f"MedCPT cross-encoder 加载失败：{type(exc).__name__}。"
            return False

    def rank(self, plan: QueryPlan, candidates: Sequence[RankedDocument]) -> Sequence[RankedDocument]:
        from dataclasses import replace

        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _query_text(plan)
        if not query or not candidates:
            return candidates
        docs = [item.document for item in candidates]
        scores: list[float] = []
        with torch.no_grad():
            for start in range(0, len(docs), self.batch_size):
                batch = docs[start:start + self.batch_size]
                encoded = self._tokenizer(
                    [[query, _document_text(doc)] for doc in batch],
                    padding=True, truncation=True, max_length=self.max_length, return_tensors="pt",
                )
                logits = self._model(**encoded).logits.squeeze(-1)
                for value in logits.tolist():
                    scores.append(float(value))
        order = sorted(range(len(candidates)), key=lambda index: (-scores[index], candidates[index].document.identity_key()))
        return [
            replace(candidates[index], reranker_id=self.reranker_id, reranker_score=round(scores[index], 6))
            for index in order
        ]


# Frozen scoring protocol for Qwen3-Reranker. The instruction is part of the
# scoring protocol (not a tuning knob): changing it changes scores, so it is a
# constant and any change is a protocol revision. English per the model card's
# multilingual guidance; the query/document pair keeps the original Chinese.
QWEN3_RERANKER_INSTRUCTION = (
    "Given a clinical question and a document, decide whether the document "
    "contains evidence relevant to the question. Match disease, drug, population "
    "and numeric conditions, not just topical similarity. "
    "Answer yes or no."
)


def yes_no_probability(logit_true: float, logit_false: float) -> float:
    """P(yes) from the yes/no token logits (restricted softmax; model-card protocol)."""
    maximum = max(logit_true, logit_false)
    exp_true = math.exp(logit_true - maximum)
    exp_false = math.exp(logit_false - maximum)
    return exp_true / (exp_true + exp_false)


@dataclass
class Qwen3Reranker:
    """Qwen3-Reranker pointwise generative reranker (multilingual).

    Scoring per the model card: chat-template each (query, document) pair under
    the frozen :data:`QWEN3_RERANKER_INSTRUCTION`, read the final-position
    ``yes``/``no`` token logits, and rank by ``yes_no_probability`` descending.

    Unlike the MedCPT cross-encoder, the query text here keeps the Chinese
    original question (plus reviewed English terms) — the point of this adapter
    for cross-lingual retrieval. Offline-first like every adapter: local cache
    only unless ``RAG_ALLOW_MODEL_DOWNLOAD=1``. Requires ``transformers>=4.51``
    (Qwen3 architecture). Tests inject ``score_pairs`` and never load the model.
    """

    model_name: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_id: str = "qwen3-reranker"
    cache_dir: str | None = None
    max_length: int = 4096
    batch_size: int = 4
    score_pairs: Callable[[Sequence[tuple[str, str]]], Sequence[float]] | None = None
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        if self.score_pairs is not None:
            return True
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "Qwen3-Reranker 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None and self._tokenizer is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import transformers

            version = str(getattr(transformers, "__version__", "") or "")
            if _version_tuple(version) < (4, 51):
                self._load_error = f"Qwen3-Reranker 需要 transformers>=4.51（当前 {version or '未知'}）。"
                return False
        except Exception as exc:  # pragma: no cover - depends on environment
            self._load_error = f"transformers 版本检查失败：{type(exc).__name__}。"
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModelForCausalLM, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            self._yes_no_ids(self._tokenizer)  # 显式失败：词表缺 yes/no 时不允许加载成功
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"Qwen3-Reranker 加载失败：{type(exc).__name__}。"
            return False

    @staticmethod
    def _yes_no_ids(tokenizer: Any) -> tuple[int, int]:
        true_id = tokenizer.convert_tokens_to_ids("yes")
        false_id = tokenizer.convert_tokens_to_ids("no")
        unk = getattr(tokenizer, "unk_token_id", None)
        if true_id == unk or false_id == unk or true_id == false_id:
            raise RuntimeError("tokenizer 词表缺少独立的 yes/no 词元，无法按模型卡协议打分")
        return int(true_id), int(false_id)

    def _score_model(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        import torch

        tokenizer = self._tokenizer
        true_id, false_id = self._yes_no_ids(tokenizer)
        scores: list[float] = []
        for start in range(0, len(pairs), self.batch_size):
            batch = pairs[start:start + self.batch_size]
            # 官方 chat template 期望三个角色：system（指令）/ query / document；
            # 拼进 user 消息会让 query_text/document_text 为空（模板只从对应角色取内容）。
            texts = [
                tokenizer.apply_chat_template(
                    [
                        {"role": "system", "content": QWEN3_RERANKER_INSTRUCTION},
                        {"role": "query", "content": query},
                        {"role": "document", "content": document},
                    ],
                    tokenize=False,
                )
                for query, document in batch
            ]
            encoded = tokenizer(
                list(texts), padding=True, truncation=True, max_length=self.max_length, return_tensors="pt",
            )
            with torch.no_grad():
                logits = self._model(**encoded).logits
            # 取每行 attention_mask 最后一个真实 token 位置的 logits（兼容左/右填充）。
            attention = encoded["attention_mask"]
            for index in range(len(batch)):
                last_position = int(attention[index].nonzero()[-1])
                last = logits[index, last_position]
                scores.append(yes_no_probability(float(last[true_id]), float(last[false_id])))
        return scores

    def rank(self, plan: QueryPlan, candidates: Sequence[RankedDocument]) -> Sequence[RankedDocument]:
        from dataclasses import replace

        if not candidates:
            return candidates
        query = (_multilingual_query_text(plan) or plan.original_question or "").strip()
        if not query:
            return candidates
        pairs = [(query, _document_text(item.document)) for item in candidates]
        if self.score_pairs is not None:
            values = [float(value) for value in self.score_pairs(pairs)]
            if len(values) != len(candidates):
                raise ValueError("score_pairs must return one score per candidate")
            scores = values
        else:
            if not self._ensure_loaded():
                raise RuntimeError(self.unavailable_reason)
            scores = self._score_model(pairs)
        order = sorted(range(len(candidates)), key=lambda index: (-scores[index], candidates[index].document.identity_key()))
        return [
            replace(candidates[index], reranker_id=self.reranker_id, reranker_score=round(scores[index], 6))
            for index in order
        ]


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in str(version).split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def _splade_encode(model: Any, tokenizer: Any, torch: Any, texts: Sequence[str], max_length: int = 256) -> Any:
    """SPLADE sparse vector: log(1 + relu(logits)) max-pooled over tokens."""
    encoded = tokenizer(list(texts), padding=True, truncation=True, max_length=max_length, return_tensors="pt")
    with torch.no_grad():
        logits = model(**encoded).logits
    weights = torch.log1p(torch.relu(logits))
    return weights.max(dim=1).values


@dataclass
class SpladeRetriever:
    """SPLADE sparse retriever (naver/splade-cocondenser-ensembledistil)."""

    model_name: str = "naver/splade-cocondenser-ensembledistil"
    retriever_id: str = "splade"
    cache_dir: str | None = None
    max_length: int = 256
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "SPLADE 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModelForMaskedLM, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover
            self._load_error = f"SPLADE 加载失败：{type(exc).__name__}。"
            return False

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _query_text(plan)
        if not query:
            return SemanticSearchResult({}, self.retriever_id, None, None)
        query_vector = _splade_encode(self._model, self._tokenizer, torch, [query], self.max_length)[0]
        scores: dict[str, float] = {}
        for doc in documents:
            doc_vector = _splade_encode(self._model, self._tokenizer, torch, [_document_text(doc)], self.max_length)[0]
            score = float(torch.dot(query_vector, doc_vector))
            scores[doc.identity_key()] = score
        maximum = max(scores.values(), default=0.0) or 1.0
        scores = {key: max(0.0, min(1.0, value / maximum)) for key, value in scores.items()}
        return SemanticSearchResult(scores, self.retriever_id, None, None)


@dataclass
class ColBertRetriever:
    """ColBERT late-interaction (MaxSim) retriever (colbert-ir/colbertv2.0).

    Uses the checkpoint's BERT encoder plus its 768->128 projection layer
    (``linear.weight``/``linear.bias`` in the state dict). Scoring is the
    standard ColBERT MaxSim over token embeddings.
    """

    model_name: str = "colbert-ir/colbertv2.0"
    retriever_id: str = "colbert"
    cache_dir: str | None = None
    max_query_length: int = 32
    max_doc_length: int = 300
    _bert: Any = field(default=None, repr=False)
    _projection: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "ColBERT 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._bert is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoConfig, AutoTokenizer, BertModel

            # ColBERT v2 自带 tokenizer 与 BERT 配置（仓库内文件），不依赖外部 bert-base-uncased。
            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            config = _prefer_local_load(AutoConfig, self.model_name, cache_dir=self.cache_dir)
            state = torch.load(self._resolve_checkpoint(), map_location="cpu")
            bert_state = {key[len("bert."):]: value for key, value in state.items() if key.startswith("bert.")}
            if not bert_state:
                bert_state = dict(state)
            self._bert = BertModel(config).eval()
            missing, unexpected = self._bert.load_state_dict(bert_state, strict=False)
            projection_weight = state.get("linear.weight")
            projection_bias = state.get("linear.bias")
            if projection_weight is None:
                raise KeyError("linear.weight not found in ColBERT checkpoint")
            self._projection = torch.nn.Linear(projection_weight.shape[1], projection_weight.shape[0])
            with torch.no_grad():
                self._projection.weight.copy_(projection_weight)
                if projection_bias is not None:
                    self._projection.bias.copy_(projection_bias)
            self._projection.eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover
            self._load_error = f"ColBERT 加载失败：{type(exc).__name__}。"
            return False

    def _resolve_checkpoint(self) -> str:
        from huggingface_hub import hf_hub_download

        kwargs: dict[str, Any] = {}
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir
        try:
            return hf_hub_download(self.model_name, "pytorch_model.bin", local_files_only=True, **kwargs)
        except Exception as local_error:
            if os.environ.get(DOWNLOAD_ENV_FLAG) != "1":
                raise RuntimeError(
                    f"模型 {self.model_name} 不在本地缓存；请预下载模型或设置 {DOWNLOAD_ENV_FLAG}=1 后重试。"
                    f"本地加载错误：{type(local_error).__name__}"
                ) from local_error
            os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
            os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
            return hf_hub_download(self.model_name, "pytorch_model.bin", **kwargs)

    def _encode_tokens(self, text: str, max_length: int) -> Any:
        torch = self._torch
        encoded = self._tokenizer(text, truncation=True, max_length=max_length, return_tensors="pt")
        with torch.no_grad():
            output = self._bert(**encoded)
            embeddings = self._projection(output.last_hidden_state)
            embeddings = torch.nn.functional.normalize(embeddings, dim=-1)[0]
        mask = encoded["attention_mask"][0].bool()
        keep = mask & (encoded["input_ids"][0] != self._tokenizer.pad_token_id)
        return embeddings[keep]

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _query_text(plan)
        if not query:
            return SemanticSearchResult({}, self.retriever_id, None, None)
        query_tokens = self._tokenizer(query, truncation=True, max_length=self.max_query_length, return_tensors="pt")
        with torch.no_grad():
            query_output = self._bert(**query_tokens)
            query_embeddings = self._projection(query_output.last_hidden_state)
            query_embeddings = torch.nn.functional.normalize(query_embeddings, dim=-1)[0]
            query_mask = query_tokens["attention_mask"][0].bool()
            query_embeddings = query_embeddings[query_mask]
        scores: dict[str, float] = {}
        with torch.no_grad():
            for doc in documents:
                doc_embeddings = self._encode_tokens(_document_text(doc), self.max_doc_length)
                if doc_embeddings.numel() == 0 or query_embeddings.numel() == 0:
                    scores[doc.identity_key()] = 0.0
                    continue
                sim = torch.matmul(query_embeddings, doc_embeddings.T)
                maxsim = sim.max(dim=1).values.sum().item()
                scores[doc.identity_key()] = float(maxsim)
        if scores:
            # Normalize to [0,1] by the maximum observed MaxSim in this batch.
            maximum = max(scores.values()) or 1.0
            scores = {key: max(0.0, min(1.0, value / maximum)) for key, value in scores.items()}
        return SemanticSearchResult(scores, self.retriever_id, None, None)


@dataclass
class MultilingualE5Retriever:
    """Multilingual bi-encoder dense retriever (intfloat/multilingual-e5-base).

    Encodes the Chinese question ("query: ") and the English documents
    ("passage: ") into one shared multilingual space with mean pooling and L2
    normalization, then scores with cosine similarity. Exists because MedCPT's
    encoders are English-only: with a Chinese question, MedCPT retrieves from
    an English corpus via English terms only. MMed-Bench-IR (arXiv:2606.24200)
    reports that multilingual dense retrievers beat biomedical-only encoders
    on cross-lingual medical retrieval tasks.
    """

    model_name: str = "intfloat/multilingual-e5-base"
    retriever_id: str = "multilingual-e5"
    cache_dir: str | None = None
    max_length: int = 512
    batch_size: int = 8
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)
    _vector_cache: dict[str, tuple[str, Any]] = field(default_factory=dict, repr=False)
    _vector_cache_limit: int = 8192

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "multilingual-e5 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModel, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"multilingual-e5 加载失败：{type(exc).__name__}。"
            return False

    def _encode(self, texts: Sequence[str], prefix: str) -> Any:
        torch = self._torch
        encoded = self._tokenizer(
            [prefix + text for text in texts], padding=True, truncation=True,
            max_length=self.max_length, return_tensors="pt",
        )
        with torch.no_grad():
            output = self._model(**encoded)
        mask = encoded["attention_mask"].unsqueeze(-1).float()
        pooled = (output.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
        return torch.nn.functional.normalize(pooled, dim=-1)

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _multilingual_query_text(plan)
        if not query:
            return SemanticSearchResult({}, self.retriever_id, self.retriever_version, None)
        with torch.no_grad():
            query_vector = self._encode([query], self.query_prefix)[0]
            scores: dict[str, float] = {}
            pending: list[SourceDocument] = []
            vectors_by_key: dict[str, Any] = {}
            for doc in documents:
                cached = _vector_cache_get(self._vector_cache, doc, self._vector_cache_limit)
                if cached is not None:
                    vectors_by_key[doc.identity_key()] = cached
                else:
                    pending.append(doc)
            for start in range(0, len(pending), self.batch_size):
                batch = pending[start:start + self.batch_size]
                vectors = self._encode([_document_text(doc) for doc in batch], self.passage_prefix)
                for doc, vector in zip(batch, vectors):
                    _vector_cache_put(self._vector_cache, doc, vector, self._vector_cache_limit)
                    vectors_by_key[doc.identity_key()] = vector
            for doc in documents:
                vector = vectors_by_key.get(doc.identity_key())
                if vector is None:
                    continue
                sim = torch.nn.functional.cosine_similarity(vector.unsqueeze(0), query_vector.unsqueeze(0), dim=1)
                # cosine [-1,1] -> [0,1], matching the SemanticRetriever contract.
                scores[doc.identity_key()] = max(0.0, min(1.0, (float(sim[0]) + 1.0) / 2.0))
        return SemanticSearchResult(scores, self.retriever_id, self.retriever_version, None)

    @property
    def retriever_version(self) -> str:
        return f"model={self.model_name}"


@dataclass
class BgeM3Retriever:
    """BGE-M3 multilingual bi-encoder dense retriever (BAAI/bge-m3).

    A multilingual bi-encoder supporting 100+ working languages, encoding the
    Chinese question and the English documents into one shared space without
    query/passage prefixes. The official sentence-transformers config for this
    checkpoint uses **CLS pooling** (``pooling_mode_cls_token: true``), so the
    first token's hidden state is L2-normalized; scoring is cosine similarity.
    Exists alongside ``xlingual`` because MMed-Bench-IR (arXiv:2606.24200) and
    MMTEB report strong multilingual retrieval for BGE-M3, and because the
    multilingual capability — not the biomedical-only training — is what
    cross-lingual retrieval needs. This adapter does **not** use the model's
    sparse/multi-vector heads; it is a dense retriever like every other
    ``SemanticRetriever`` adapter here.
    """

    model_name: str = "BAAI/bge-m3"
    retriever_id: str = "bge-m3"
    cache_dir: str | None = None
    max_length: int = 512
    batch_size: int = 8
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)
    _vector_cache: dict[str, tuple[str, Any]] = field(default_factory=dict, repr=False)
    _vector_cache_limit: int = 8192

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "bge-m3 模型未加载。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModel, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"bge-m3 加载失败：{type(exc).__name__}。"
            return False

    def _encode(self, texts: Sequence[str]) -> Any:
        torch = self._torch
        encoded = self._tokenizer(
            list(texts), padding=True, truncation=True,
            max_length=self.max_length, return_tensors="pt",
        )
        with torch.no_grad():
            output = self._model(**encoded)
        # BGE-M3 官方配置为 CLS 池化（1_Pooling/config.json: pooling_mode_cls_token=true）。
        cls = output.last_hidden_state[:, 0]
        return torch.nn.functional.normalize(cls, dim=-1)

    def search(self, plan: QueryPlan, documents: Sequence[SourceDocument], *, limit: int) -> SemanticSearchResult:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        query = _multilingual_query_text(plan)
        if not query:
            return SemanticSearchResult({}, self.retriever_id, self.retriever_version, None)
        with torch.no_grad():
            query_vector = self._encode([query])[0]
            scores: dict[str, float] = {}
            pending: list[SourceDocument] = []
            vectors_by_key: dict[str, Any] = {}
            for doc in documents:
                cached = _vector_cache_get(self._vector_cache, doc, self._vector_cache_limit)
                if cached is not None:
                    vectors_by_key[doc.identity_key()] = cached
                else:
                    pending.append(doc)
            for start in range(0, len(pending), self.batch_size):
                batch = pending[start:start + self.batch_size]
                vectors = self._encode([_document_text(doc) for doc in batch])
                for doc, vector in zip(batch, vectors):
                    _vector_cache_put(self._vector_cache, doc, vector, self._vector_cache_limit)
                    vectors_by_key[doc.identity_key()] = vector
            for doc in documents:
                vector = vectors_by_key.get(doc.identity_key())
                if vector is None:
                    continue
                sim = torch.nn.functional.cosine_similarity(vector.unsqueeze(0), query_vector.unsqueeze(0), dim=1)
                # cosine [-1,1] -> [0,1], matching the SemanticRetriever contract.
                scores[doc.identity_key()] = max(0.0, min(1.0, (float(sim[0]) + 1.0) / 2.0))
        return SemanticSearchResult(scores, self.retriever_id, self.retriever_version, None)

    @property
    def retriever_version(self) -> str:
        return f"model={self.model_name}"


def _multilingual_query_text(plan: QueryPlan) -> str:
    """Query text for the multilingual encoder: original question + reviewed English.

    The Chinese original stays first so the encoder sees the real question; the
    reviewed English terms (if the caller supplied any) are appended because
    they are already validated terminology. Terms are deduplicated.
    """
    parts = [plan.original_question, *plan.english_terms, *plan.mesh_terms]
    return " ".join(dict.fromkeys(part.strip() for part in parts if part and part.strip()))


@dataclass
class LocalZhEnTranslator:
    """zh->en translator used as a ``translate`` query-expansion method.

    Default: local Marian model ``Helsinki-NLP/opus-mt-zh-en`` (weights must be
    in the local HF cache; offline-first like every other adapter). The
    translator only produces an extra English query variant for lexical
    scorers; it never replaces the Chinese question, and the pipeline records
    the produced text in ``expansion_trace`` so entity corruption is auditable.
    A custom translator can be injected via ``translate`` for tests.
    """

    model_name: str = "Helsinki-NLP/opus-mt-zh-en"
    expander_id: str = "opus-mt-zh-en"
    cache_dir: str | None = None
    max_new_tokens: int = 96
    translate: Callable[[str], str] | None = None
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        if self.translate is not None:
            return True
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "zh→en 翻译模型未配置。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModelForSeq2SeqLM, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"zh→en 翻译模型加载失败：{type(exc).__name__}。"
            return False

    def expand(self, plan: QueryPlan) -> tuple[str, ...]:
        """Return the English translation of the original question (one string)."""
        question = plan.original_question.strip()
        if not question:
            return ()
        if self.translate is not None:
            text = str(self.translate(question) or "").strip()
            return (text,) if text else ()
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        encoded = self._tokenizer([question], return_tensors="pt", truncation=True, max_length=256)
        with torch.no_grad():
            output = self._model.generate(**encoded, max_new_tokens=self.max_new_tokens)
        text = self._tokenizer.batch_decode(output, skip_special_tokens=True)[0].strip()
        return (text,) if text else ()


@dataclass
class LocalQuery2DocExpander:
    """Query2doc pseudo-document expansion with a local seq2seq generator.

    Query2doc (Wang et al., 2023) uses an LLM to write a pseudo document for a
    query and concatenates it with the query before BM25 retrieval. Here the
    generator is injectable; the default uses a small local flan-t5 model as a
    demo generator. If no generator can be loaded the expansion reports
    not_configured instead of fabricating text.
    """

    model_name: str = "google/flan-t5-small"
    expander_id: str = "query2doc-local"
    cache_dir: str | None = None
    max_new_tokens: int = 64
    generate: Callable[[str], str] | None = None
    _model: Any = field(default=None, repr=False)
    _tokenizer: Any = field(default=None, repr=False)
    _torch: Any = field(default=None, repr=False)
    _load_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        if self.generate is not None:
            return True
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            return str(deps.get("detail") or "torch/transformers 未安装。")
        return "Query2doc 生成器未配置。"

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        deps = neural_dependencies_state()
        if not (deps["torch"] and deps["transformers"]):
            self._load_error = str(deps.get("detail") or "torch/transformers 未安装。")
            return False
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = _prefer_local_load(AutoTokenizer, self.model_name, cache_dir=self.cache_dir)
            self._model = _prefer_local_load(AutoModelForSeq2SeqLM, self.model_name, cache_dir=self.cache_dir).eval()
            self._torch = torch
            return True
        except Exception as exc:  # pragma: no cover
            self._load_error = f"Query2doc 生成器加载失败：{type(exc).__name__}。"
            return False

    def expand(self, plan: QueryPlan) -> tuple[str, ...]:
        """Return pseudo-document text(s) for the query; raises when unavailable."""
        query = _query_text(plan)
        if not query:
            return ()
        if self.generate is not None:
            text = str(self.generate(query) or "").strip()
            return (text,) if text else ()
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        prompt = f"Write a short medical passage that answers: {query}"
        encoded = self._tokenizer(prompt, return_tensors="pt", truncation=True, max_length=256)
        with torch.no_grad():
            output = self._model.generate(**encoded, max_new_tokens=self.max_new_tokens)
        text = self._tokenizer.decode(output[0], skip_special_tokens=True).strip()
        return (text,) if text else ()


def build_neural_registry(
    *,
    cache_dir: str | None = None,
    enable: Sequence[str] = ("dense", "xlingual", "m3", "splade", "colbert", "query2doc", "translate", "cross_encoder", "qwen3_reranker"),
) -> dict[str, Any]:
    """Construct neural adapters (without loading models).

    Returns a mapping used by the server/pipeline:
    ``{"dense": MedCPTDenseRetriever, "xlingual": MultilingualE5Retriever,
       "m3": BgeM3Retriever, "splade": ..., "colbert": ...,
       "cross_encoder": MedCPTReranker, "qwen3_reranker": Qwen3Reranker,
       "query2doc": LocalQuery2DocExpander, "translate": LocalZhEnTranslator}``
    filtered by ``enable``. Construction is cheap; models load lazily on first
    use. Availability must be probed separately (``available``) and reported in
    the trace; nothing loads at import time.
    """
    registry: dict[str, Any] = {}
    if "dense" in enable:
        registry["dense"] = MedCPTDenseRetriever(cache_dir=cache_dir)
    if "xlingual" in enable:
        registry["xlingual"] = MultilingualE5Retriever(cache_dir=cache_dir)
    if "m3" in enable:
        registry["m3"] = BgeM3Retriever(cache_dir=cache_dir)
    if "splade" in enable:
        registry["splade"] = SpladeRetriever(cache_dir=cache_dir)
    if "colbert" in enable:
        registry["colbert"] = ColBertRetriever(cache_dir=cache_dir)
    if "query2doc" in enable:
        registry["query2doc"] = LocalQuery2DocExpander(cache_dir=cache_dir)
    if "translate" in enable:
        registry["translate"] = LocalZhEnTranslator(cache_dir=cache_dir)
    if "cross_encoder" in enable:
        registry["cross_encoder"] = MedCPTReranker(cache_dir=cache_dir)
    if "qwen3_reranker" in enable:
        registry["qwen3_reranker"] = Qwen3Reranker(cache_dir=cache_dir)
    return registry


def cache_dir_hint() -> str | None:
    """Return the HF cache dir if it exists, so adapters can reuse downloads."""
    env = os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if env:
        return env
    default = os.path.expanduser("~/.cache/huggingface/hub")
    return default if os.path.isdir(default) else None
