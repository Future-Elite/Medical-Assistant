"""Real claim-level verification adapters for grounded-answer auditing.

The default auditor remains structural (citation presence and retrieval-scoped
IDs).  This module adds an **opt-in** semantic verifier built from real,
existing models:

* ``MiniCheckVerifier`` wraps the official ``minicheck`` package and its
  published checkpoints (``flan-t5-large``, ``roberta-large``,
  ``deberta-v3-large``).  It does not implement a lookalike scorer.
* ``LocalZhEnClaimTranslator`` wraps the same local Marian checkpoint already
  used by the ``translate`` retrieval method.  Chinese claims are translated
  zh→en before an English-only verifier sees them; the translation is recorded
  and its limitation is explicit.
* Missing packages, missing weights, or a failed translation produce
  ``not_checked`` with a reason.  The auditor never falls back to lexical
  overlap and never reports an unverified claim as supported.

The adapters are lazy: importing the package or serving ``/api/health`` does
not load model weights.  Model weights stay in the local Hugging Face cache;
``RAG_ALLOW_MODEL_DOWNLOAD=1`` is required for a download.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..config import get_config

_CJK_RE = re.compile(r"[\u3400-\u9fff]")

_MODEL_ALIASES = {
    "flan-t5-large": "flan-t5-large",
    "lytang/MiniCheck-Flan-T5-Large": "flan-t5-large",
    "roberta-large": "roberta-large",
    "lytang/MiniCheck-RoBERTa-Large": "roberta-large",
    "deberta-v3-large": "deberta-v3-large",
    "lytang/MiniCheck-DeBERTa-v3-Large": "deberta-v3-large",
    "Bespoke-MiniCheck-7B": "Bespoke-MiniCheck-7B",
    "Granite-Guardian-3.3-8B": "Granite-Guardian-3.3-8B",
}


def _offline_default() -> bool:
    return str(os.environ.get("RAG_ALLOW_MODEL_DOWNLOAD", "")).strip() not in {"1", "true", "yes", "on"}


def _probability(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


@dataclass
class MiniCheckVerifier:
    """Lazy adapter for the official ``minicheck`` package.

    ``model_name`` accepts the package's canonical names and the Hugging Face
    checkpoint ids used in the model cards.  The package performs a restricted
    yes/no softmax; the probability is recorded, not reinterpreted.
    """

    model_name: str | None = None
    cache_dir: str | None = None
    batch_size: int = 8
    _scorer: Any = field(default=None, init=False, repr=False)
    _load_error: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        config = get_config()
        if self.model_name is None:
            self.model_name = config.minicheck_model or None
        if self.cache_dir is None:
            self.cache_dir = config.minicheck_cache_dir or None
        if self.model_name:
            canonical = _MODEL_ALIASES.get(str(self.model_name).strip(), str(self.model_name).strip())
            self.model_name = canonical

    @property
    def name(self) -> str:
        return "minicheck"

    @property
    def available(self) -> bool:
        return bool(self.model_name) and self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if not self.model_name:
            return "未配置 minicheck_model；未执行主张级模型校验。"
        self._ensure_loaded()
        return self._load_error or "MiniCheck 当前不可用。"

    def verify(self, pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[bool, float | None]]:
        if not pairs:
            return []
        if not self.available:
            raise RuntimeError(self.unavailable_reason)
        claims = [str(claim) for claim, _ in pairs]
        documents = [str(document) for _, document in pairs]
        try:
            labels, probabilities, _, _ = self._scorer.score(docs=documents, claims=claims)
        except Exception as exc:
            raise RuntimeError(f"MiniCheck 推理失败：{type(exc).__name__}") from exc
        return [
            (bool(label), _probability(probability))
            for label, probability in zip(labels, probabilities)
        ]

    def _ensure_loaded(self) -> bool:
        if self._scorer is not None:
            return True
        if self._load_error or not self.model_name:
            return False
        try:
            from minicheck.minicheck import MiniCheck  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {"model_name": self.model_name, "batch_size": int(self.batch_size)}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._scorer = MiniCheck(**kwargs)
            return True
        except Exception as exc:
            self._load_error = f"MiniCheck 未就绪：{type(exc).__name__}。"
            return False

    def describe(self) -> dict[str, Any]:
        import importlib.util

        return {
            "name": self.name,
            "model": self.model_name or "",
            "configured": bool(self.model_name),
            "package_installed": bool(importlib.util.find_spec("minicheck")),
            "loaded": self._scorer is not None,
            "reason": self._load_error,
        }


@dataclass
class LocalZhEnClaimTranslator:
    """Lazy zh→en Marian translator used only for claim verification.

    This is the same local checkpoint as the retrieval ``translate`` method
    (``Helsinki-NLP/opus-mt-zh-en``), but it is a separate object: enabling
    claim verification must not change retrieval query expansion.
    """

    model_name: str = "Helsinki-NLP/opus-mt-zh-en"
    cache_dir: str | None = None
    max_length: int = 512
    num_beams: int = 4
    _model: Any = field(default=None, init=False, repr=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _torch: Any = field(default=None, init=False, repr=False)
    _load_error: str | None = field(default=None, init=False, repr=False)
    _cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    last_translations: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    @property
    def name(self) -> str:
        return "opus-mt-zh-en"

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        self._ensure_loaded()
        return self._load_error or "zh→en 翻译模型未加载。"

    def translate(self, texts: Sequence[str]) -> list[str]:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        unique = list(dict.fromkeys(str(text) for text in texts))
        pending = [text for text in unique if text not in self._cache]
        if pending:
            torch = self._torch
            with torch.no_grad():
                for start in range(0, len(pending), 8):
                    batch = pending[start:start + 8]
                    encoded = self._tokenizer(
                        batch, return_tensors="pt", padding=True, truncation=True,
                        max_length=self.max_length,
                    )
                    generated = self._model.generate(
                        **encoded, max_length=self.max_length, num_beams=self.num_beams,
                    )
                    decoded = self._tokenizer.batch_decode(generated, skip_special_tokens=True)
                    for source, target in zip(batch, decoded):
                        self._cache[source] = str(target).strip()
        self.last_translations = {text: self._cache[text] for text in unique}
        return [self._cache[text] for text in (str(text) for text in texts)]

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        try:
            import torch
            from transformers import MarianMTModel, MarianTokenizer

            self._tokenizer = MarianTokenizer.from_pretrained(
                self.model_name, cache_dir=self.cache_dir, local_files_only=_offline_default()
            )
            self._model = MarianMTModel.from_pretrained(
                self.model_name, cache_dir=self.cache_dir, local_files_only=_offline_default()
            ).eval()
            self._torch = torch
            return True
        except Exception as exc:
            self._load_error = f"zh→en 翻译模型未就绪：{type(exc).__name__}。"
            return False

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model_name,
            "configured": True,
            "loaded": self._model is not None,
            "reason": self._load_error,
        }


@dataclass
class CrossLingualMiniCheckVerifier:
    """MiniCheck plus optional zh→en claim translation.

    English claims go straight to MiniCheck.  A Chinese claim is translated
    first; the translated text is exposed in ``last_translations`` so the audit
    can show exactly what the verifier saw.  If a Chinese claim appears while no
    translator is configured, the verifier raises instead of silently comparing
    Chinese text with an English-only model.
    """

    scorer: MiniCheckVerifier
    translator: LocalZhEnClaimTranslator | None = None
    _last_translations: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    @property
    def name(self) -> str:
        return "minicheck+opus-mt-zh-en" if self.translator is not None else self.scorer.name

    @property
    def model_name(self) -> str:
        return self.scorer.model_name or ""

    @property
    def translator_name(self) -> str | None:
        return self.translator.name if self.translator is not None else None

    @property
    def available(self) -> bool:
        return self.scorer.available

    @property
    def unavailable_reason(self) -> str:
        return self.scorer.unavailable_reason

    @property
    def last_translations(self) -> Mapping[str, str]:
        return dict(self._last_translations)

    def verify(self, pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[bool, float | None]]:
        if not pairs:
            return []
        if not self.scorer.available:
            raise RuntimeError(self.scorer.unavailable_reason)
        claims = [str(claim) for claim, _ in pairs]
        chinese = [claim for claim in claims if _CJK_RE.search(claim)]
        self._last_translations = {}
        if chinese:
            if self.translator is None:
                raise RuntimeError("存在中文主张但未配置 zh→en 翻译器；未执行跨语言校验。")
            if not self.translator.available:
                raise RuntimeError(self.translator.unavailable_reason)
            translated = self.translator.translate(chinese)
            mapping = dict(zip(chinese, translated))
            self._last_translations = mapping
            claims = [mapping.get(claim, claim) for claim in claims]
        return self.scorer.verify([(claim, document) for claim, (_, document) in zip(claims, pairs)])

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model_name,
            "configured": bool(self.model_name),
            "package_installed": self.scorer.describe().get("package_installed"),
            "loaded": self.scorer._scorer is not None,
            "reason": self.scorer._load_error,
            "translation": self.translator.describe() if self.translator is not None else {"configured": False},
        }


def build_claim_verifier(
    *,
    model_name: str = "",
    cache_dir: str = "",
    translation: bool = True,
) -> Any | None:
    """Build the configured verifier without loading weights."""
    model = str(model_name or "").strip()
    if not model:
        return None
    scorer = MiniCheckVerifier(model_name=model, cache_dir=cache_dir or None)
    if not translation:
        return scorer
    return CrossLingualMiniCheckVerifier(
        scorer=scorer,
        translator=LocalZhEnClaimTranslator(cache_dir=cache_dir or None),
    )


def claim_fingerprint(text: str) -> str:
    """Stable short fingerprint for logs; never includes the claim text."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]
