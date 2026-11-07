"""MeSH-grounded entity linking for query-side terminology.

The linker is intentionally conservative and auditable:

* it matches against a **local snapshot of NLM MeSH** (descriptor labels,
  official entry terms, term UIs) built by ``tools/build_mesh_lexicon.py``;
* it normalises surface forms (Unicode, punctuation, possessives, Greek
  letters, common British/American spellings, conservative plurals) before
  matching;
* it can expand abbreviations found in the surrounding text with the
  Schwartz & Hearst (2003) algorithm, as implemented by scispaCy's
  ``abbreviation.py`` (Apache-2.0); the port here has no spaCy dependency;
* it reports ``linked`` / ``ambiguous`` / ``unlinked`` and never silently picks
  one of several equally plausible descriptors;
* when semantic ranking is enabled it uses the project's real BGE-M3 encoder,
  not a hand-written similarity table.  Model weights stay in the local HF
  cache; missing weights produce ``not_configured`` rather than a downgrade.

The linker does **not** translate arbitrary Chinese text into English.  Chinese
concepts are linked through their reviewed ``english`` / ``mesh`` products (the
same binding used by ``term_mapping``); a Chinese surface without such a
product is reported as ``skipped_no_english_surface``.  Machine translation is
a separate, explicitly requested retrieval method.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MESH_LINKING_MODES = ("off", "shadow", "active")

# Generic query words carry no entity identity.  They are excluded from token
# candidate generation but never from exact matching of a full surface form.
_ENTITY_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "with", "without", "which", "what", "when",
    "how", "should", "can", "could", "may", "might", "do", "does", "did",
    "guideline", "guidelines", "recommendation", "recommendations", "evidence",
    "management", "treatment", "therapy", "therapies", "diagnosis",
    "diagnostic", "clinical", "patient", "patients", "study", "studies",
    "review", "update", "updates", "question", "risk", "benefit", "use",
    "versus", "vs", "compared", "comparison", "effect", "effects", "outcome",
    "outcomes", "dose", "dosing", "safety", "efficacy", "neonatal",
    "newborn", "newborns", "infant", "infants", "preterm", "premature",
    "low", "birth", "weight", "very", "extremely", "early", "late", "term",
    "postnatal", "prenatal", "antenatal", "perinatal",
})

_BRITISH_AMERICAN = {
    "paediatric": "pediatric", "paediatrics": "pediatrics",
    "anaemia": "anemia", "anaemic": "anemic",
    "haemorrhage": "hemorrhage", "haemorrhagic": "hemorrhagic",
    "haemoglobin": "hemoglobin", "haemodynamic": "hemodynamic",
    "oedema": "edema", "foetal": "fetal", "foetus": "fetus",
    "oesophageal": "esophageal", "oesophagus": "esophagus",
    "tumour": "tumor", "tumours": "tumors",
    "labour": "labor", "caesarean": "cesarean", "caesarian": "cesarean",
    "behaviour": "behavior", "behavioural": "behavioral",
    "organisation": "organization", "organisation's": "organization",
    "haematology": "hematology", "haematocrit": "hematocrit",
    "leucocyte": "leukocyte", "leucocytes": "leukocytes",
    "thrombocytopaenia": "thrombocytopenia",
}

_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron",
    "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau",
    "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_REVIEWED_ALIAS_MARKER = "__reviewed_alias__"


@dataclass(frozen=True)
class MeshTerm:
    ui: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"ui": self.ui, "text": self.text}


@dataclass(frozen=True)
class MeshDescriptor:
    ui: str
    label: str
    terms: tuple[MeshTerm, ...] = ()
    tree_numbers: tuple[str, ...] = ()
    matched_by: str = "tree"
    lookup_label: str | None = None
    lookup_match: str | None = None

    def surfaces(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*((self.label,) if self.label else ()), *(term.text for term in self.terms if term.text))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ui": self.ui,
            "label": self.label,
            "terms": [term.to_dict() for term in self.terms],
            "tree_numbers": list(self.tree_numbers),
            "matched_by": self.matched_by,
            "lookup_label": self.lookup_label,
            "lookup_match": self.lookup_match,
        }


@dataclass(frozen=True)
class MeshCandidate:
    ui: str
    label: str
    score: float
    method: str
    matched_surface: str
    matched_term_ui: str | None = None
    tree_numbers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor_ui": self.ui,
            "label": self.label,
            "score": round(float(self.score), 6),
            "method": self.method,
            "matched_surface": self.matched_surface,
            "matched_term_ui": self.matched_term_ui,
            "tree_numbers": list(self.tree_numbers),
        }


@dataclass(frozen=True)
class MeshLinkResult:
    surface: str
    normalized: str
    status: str
    candidates: tuple[MeshCandidate, ...] = ()
    expanded_from: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "normalized": self.normalized,
            "status": self.status,
            "expanded_from": self.expanded_from,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @property
    def best(self) -> MeshCandidate | None:
        return self.candidates[0] if self.status == "linked" and self.candidates else None


def normalize_text(text: str) -> str:
    """Canonical surface form used for matching and indexing."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    for greek, name in _GREEK.items():
        value = value.replace(greek, f" {name} ")
    value = value.replace("&", " and ")
    value = re.sub(r"'s\b", "", value)
    value = re.sub(r"[\u2010-\u2015/\\,;:+()\[\]{}<>\"“”‘’!?*]", " ", value)
    value = re.sub(r"\.(?=\s|$)", " ", value)
    value = re.sub(r"[^0-9a-z\u3400-\u9fff]+", " ", value)
    tokens = [token for token in value.split() if token]
    tokens = [_BRITISH_AMERICAN.get(token, token) for token in tokens]
    return " ".join(tokens)


def _singular_variants(token: str) -> tuple[str, ...]:
    if len(token) <= 3 or not token.isalpha():
        return ()
    variants: list[str] = []
    if token.endswith("ies") and len(token) > 4:
        variants.append(token[:-3] + "y")
    if token.endswith(("ses", "xes", "zes", "ches", "shes")) and len(token) > 4:
        variants.append(token[:-2])
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        variants.append(token[:-1])
    return tuple(dict.fromkeys(variants))


def normalized_variants(text: str) -> tuple[str, ...]:
    """Normalized surface plus conservative plural variants (never destructive)."""
    base = normalize_text(text)
    if not base:
        return ()
    variants = [base]
    tokens = base.split()
    for index, token in enumerate(tokens):
        for singular in _singular_variants(token):
            variant = " ".join((*tokens[:index], singular, *tokens[index + 1:]))
            if variant not in variants:
                variants.append(variant)
    return tuple(variants)


def token_features(text: str) -> set[str]:
    base = normalize_text(text)
    features: set[str] = set()
    for token in base.split():
        if token in _ENTITY_STOPWORDS and len(token) < 4:
            continue
        features.add(token)
        features.update(_singular_variants(token))
    return features


def _content_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        token for token in normalize_text(text).split()
        if token not in _ENTITY_STOPWORDS
    )


def _find_long_form(long_form: str, short_form: str) -> str | None:
    """Port of Schwartz & Hearst (2003) as implemented in scispaCy.

    Returns the matching long-form substring or ``None``.  The implementation
    follows the character-matching rules from ``scispacy/abbreviation.py``
    without importing spaCy.
    """
    long_form = str(long_form or "")
    short_form = str(short_form or "")
    if not long_form.strip() or not short_form.strip():
        return None
    long_index = len(long_form) - 1
    short_index = len(short_form) - 1
    while short_index >= 0:
        current = short_form[short_index].lower()
        if not current.isalnum():
            short_index -= 1
            continue
        while (
            (long_index >= 0 and long_form[long_index].lower() != current)
            or (
                short_index == 0
                and long_index > 0
                and long_form[long_index - 1].isalnum()
            )
        ):
            long_index -= 1
        if long_index < 0:
            return None
        long_index -= 1
        short_index -= 1
    long_index += 1
    while long_index > 0 and long_form[long_index - 1].isalnum():
        long_index -= 1
    candidate = long_form[long_index:].strip(" \t\r\n,;:-")
    return candidate or None


_ABBREVIATION_LONG_FIRST = re.compile(
    r"(?P<long>[A-Za-z][A-Za-z0-9 ,/'\-]{2,90}?)\s*\(\s*(?P<short>[A-Za-z][A-Za-z0-9\-]{1,15})\s*\)"
)
_ABBREVIATION_SHORT_FIRST = re.compile(
    r"(?P<short>[A-Z][A-Z0-9\-]{1,14})\s*\(\s*(?P<long>[A-Za-z][A-Za-z0-9 ,/'\-]{2,90})\s*\)"
)


def extract_abbreviations(text: str) -> dict[str, str]:
    """Extract ``short -> long`` definitions from prose.

    Both ``long form (SHORT)`` and ``SHORT (long form)`` are supported.  Short
    forms must contain an uppercase letter or digit and have 2–15 characters;
    this filters parenthetical prose such as ``(see above)``.
    """
    source = str(text or "")
    found: dict[str, str] = {}

    def accept(short: str, long: str) -> None:
        short = short.strip()
        long = long.strip(" \t\r\n,;:-")
        if not (2 <= len(short) <= 15) or not long:
            return
        if not (any(ch.isupper() for ch in short) or any(ch.isdigit() for ch in short)):
            return
        resolved = _find_long_form(long, short)
        if not resolved:
            return
        words = resolved.split()
        if not words or len(words) > 8:
            return
        found.setdefault(short, resolved)

    for match in _ABBREVIATION_LONG_FIRST.finditer(source):
        accept(match.group("short"), match.group("long"))
    for match in _ABBREVIATION_SHORT_FIRST.finditer(source):
        accept(match.group("short"), match.group("long"))
    return found


class MeshLexicon:
    """Validated local MeSH snapshot with normalized lookup indexes."""

    def __init__(self, descriptors: Sequence[MeshDescriptor], *, source: str = "", mesh_year: str = "", retrieved_at: str = "", reviewed_aliases: Sequence[Mapping[str, Any]] = ()):
        self.descriptors = tuple(descriptors)
        self.source = source
        self.mesh_year = mesh_year
        self.retrieved_at = retrieved_at
        self.reviewed_aliases = tuple(dict(item) for item in reviewed_aliases)
        self.by_ui = {descriptor.ui: descriptor for descriptor in self.descriptors}
        self._exact: dict[str, list[tuple[str, str, str]]] = {}
        self._token_index: dict[str, set[str]] = {}
        self._surface_cache: dict[str, tuple[tuple[str, str], ...]] = {}
        for descriptor in self.descriptors:
            self._index_descriptor(descriptor)
        self._index_reviewed_aliases(self.reviewed_aliases)

    @classmethod
    def load(cls, path: str | Path) -> "MeshLexicon":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MeshLexicon":
        rows = payload.get("descriptors")
        if not isinstance(rows, list):
            raise ValueError("MeSH lexicon requires a descriptors list")
        descriptors: list[MeshDescriptor] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("MeSH descriptor must be an object")
            ui = str(row.get("ui") or "").strip()
            label = str(row.get("label") or "").strip()
            if not ui or not label:
                raise ValueError("MeSH descriptor requires ui and label")
            terms = tuple(
                MeshTerm(str(term.get("ui") or ""), str(term.get("text") or "").strip())
                for term in row.get("terms") or ()
                if isinstance(term, Mapping) and str(term.get("text") or "").strip()
            )
            descriptors.append(MeshDescriptor(
                ui=ui,
                label=label,
                terms=terms,
                tree_numbers=tuple(str(item) for item in row.get("tree_numbers") or ()),
                matched_by=str(row.get("matched_by") or "tree"),
                lookup_label=row.get("lookup_label"),
                lookup_match=row.get("lookup_match"),
            ))
        aliases = payload.get("reviewed_aliases")
        return cls(
            descriptors,
            source=str(payload.get("source") or ""),
            mesh_year=str(payload.get("mesh_year") or ""),
            retrieved_at=str(payload.get("retrieved_at") or ""),
            reviewed_aliases=tuple(
                item for item in aliases or ()
                if isinstance(item, Mapping) and item.get("surface") and item.get("descriptor_ui")
            ),
        )

    def _index_descriptor(self, descriptor: MeshDescriptor) -> None:
        surface_rows: list[tuple[str, str]] = []
        for term in descriptor.terms:
            surface_rows.append((term.text, term.ui))
        if descriptor.label and all(normalize_text(term.text) != normalize_text(descriptor.label) for term in descriptor.terms):
            surface_rows.insert(0, (descriptor.label, ""))
        for surface, term_ui in surface_rows:
            for variant in normalized_variants(surface):
                if not variant:
                    continue
                self._exact.setdefault(variant, []).append((descriptor.ui, surface, term_ui))
            for token in token_features(surface):
                self._token_index.setdefault(token, set()).add(descriptor.ui)
            self._surface_cache.setdefault(descriptor.ui, ())
        cached = list(self._surface_cache[descriptor.ui])
        cached.extend((surface, term_ui) for surface, term_ui in surface_rows)
        self._surface_cache[descriptor.ui] = tuple(dict.fromkeys(cached))

    def _index_reviewed_aliases(self, aliases: Sequence[Mapping[str, Any]]) -> None:
        for alias in aliases:
            ui = str(alias.get("descriptor_ui") or "").strip()
            surface = str(alias.get("surface") or "").strip()
            if not ui or not surface or ui not in self.by_ui:
                continue
            for variant in normalized_variants(surface):
                if not variant:
                    continue
                self._exact.setdefault(variant, []).append((ui, surface, _REVIEWED_ALIAS_MARKER))
            for token in token_features(surface):
                self._token_index.setdefault(token, set()).add(ui)
            cached = list(self._surface_cache.get(ui, ()))
            cached.append((surface, _REVIEWED_ALIAS_MARKER))
            self._surface_cache[ui] = tuple(dict.fromkeys(cached))

    def surfaces(self, ui: str) -> tuple[tuple[str, str], ...]:
        return self._surface_cache.get(ui, ())

    def candidates_by_tokens(self, tokens: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for token in tokens:
            result.update(self._token_index.get(token, ()))
        return result

    def exact_matches(self, variants: Sequence[str]) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for variant in variants:
            rows.extend(self._exact.get(variant, ()))
        return list(dict.fromkeys(rows))

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "mesh_year": self.mesh_year,
            "retrieved_at": self.retrieved_at,
            "descriptors": len(self.descriptors),
            "terms": sum(len(descriptor.terms) for descriptor in self.descriptors),
            "reviewed_aliases": len(self.reviewed_aliases),
        }


class BgeM3SemanticScorer:
    """Optional real semantic candidate scorer using BAAI/bge-m3.

    Vectors are computed lazily for descriptor labels and kept in memory for the
    process.  The encoder is the same checkpoint and CLS-pooling protocol used
    by :class:`rag.neural_adapters.BgeM3Retriever`; no synthetic similarity
    table is used.  Missing weights or dependencies report ``not_configured``.
    """

    model_name: str = "BAAI/bge-m3"
    max_length: int = 64
    batch_size: int = 16

    def __init__(self, lexicon: MeshLexicon, *, cache_dir: str | None = None, surface_mode: str = "all", max_surfaces_per_descriptor: int = 0):
        self.lexicon = lexicon
        self.cache_dir = cache_dir
        self.surface_mode = str(surface_mode or "all").strip().lower()
        if self.surface_mode not in {"label", "all"}:
            raise ValueError("surface_mode must be label or all")
        self.max_surfaces_per_descriptor = int(max_surfaces_per_descriptor)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._load_error: str | None = None
        self._descriptor_surface_indices: dict[str, list[int]] = {}
        self._surface_rows: tuple[tuple[str, str], ...] = self._build_surface_rows(lexicon.descriptors)
        self._vectors: Any = None

    @property
    def name(self) -> str:
        return "bge-m3"

    @property
    def available(self) -> bool:
        return self._ensure_loaded()

    @property
    def unavailable_reason(self) -> str:
        if self._load_error:
            return self._load_error
        return "BGE-M3 模型未加载。"

    def _build_surface_rows(self, descriptors: Sequence[MeshDescriptor]) -> tuple[tuple[str, str], ...]:
        rows: list[tuple[str, str]] = []
        for descriptor in descriptors:
            surfaces = [descriptor.label] if descriptor.label else []
            if self.surface_mode == "all":
                surfaces.extend(term.text for term in descriptor.terms if term.text)
            seen_texts: set[str] = set()
            added = 0
            for text in surfaces:
                normalized = normalize_text(text)
                if not normalized or normalized in seen_texts:
                    continue
                seen_texts.add(normalized)
                rows.append((descriptor.ui, text))
                self._descriptor_surface_indices.setdefault(descriptor.ui, []).append(len(rows) - 1)
                added += 1
                if self.max_surfaces_per_descriptor and added >= self.max_surfaces_per_descriptor:
                    break
        return tuple(rows)

    def _ensure_loaded(self) -> bool:
        if self._model is not None and self._vectors is not None:
            return True
        if self._load_error:
            return False
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name, cache_dir=self.cache_dir, local_files_only=self._offline()
            )
            self._model = AutoModel.from_pretrained(
                self.model_name, cache_dir=self.cache_dir, local_files_only=self._offline()
            ).eval()
            self._torch = torch
            self._vectors = self._encode([text for _ui, text in self._surface_rows])
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            self._load_error = f"BGE-M3 语义打分器未就绪：{type(exc).__name__}。"
            return False

    def _offline(self) -> bool:
        import os

        return str(os.environ.get("RAG_ALLOW_MODEL_DOWNLOAD", "")).strip() not in {"1", "true", "yes", "on"}

    def _encode(self, texts: Sequence[str]) -> Any:
        torch = self._torch
        vectors = []
        with torch.no_grad():
            for start in range(0, len(texts), self.batch_size):
                batch = list(texts[start:start + self.batch_size])
                encoded = self._tokenizer(
                    batch, padding=True, truncation=True, max_length=self.max_length,
                    return_tensors="pt",
                )
                output = self._model(**encoded)
                cls = output.last_hidden_state[:, 0]
                vectors.append(torch.nn.functional.normalize(cls, dim=-1))
        return torch.cat(vectors, dim=0) if vectors else None

    def score(self, query: str, candidates: Sequence[str]) -> dict[str, float]:
        if not self._ensure_loaded():
            raise RuntimeError(self.unavailable_reason)
        torch = self._torch
        candidate_set = set(candidates)
        if not candidate_set:
            return {}
        query_vector = self._encode([query])[0]
        scores: dict[str, float] = {}
        with torch.no_grad():
            for ui in candidate_set:
                indices = self._descriptor_surface_indices.get(ui, ())
                if not indices:
                    continue
                vectors = self._vectors[indices]
                similarities = torch.nn.functional.cosine_similarity(
                    vectors, query_vector.unsqueeze(0), dim=1
                )
                scores[ui] = max(0.0, min(1.0, (float(similarities.max()) + 1.0) / 2.0))
        return scores


@dataclass
class MeshLinker:
    lexicon: MeshLexicon
    semantic_scorer: Any = None
    min_score: float = 0.86
    semantic_min_score: float = 0.90
    ambiguity_margin: float = 0.05
    max_candidates: int = 5
    max_token_candidates: int = 200
    last_semantic_error: str | None = None

    def link(self, surface: str, *, context: str = "", top_k: int | None = None) -> MeshLinkResult:
        raw = str(surface or "").strip()
        normalized = normalize_text(raw)
        if not normalized:
            return MeshLinkResult(raw, "", "unlinked", reason="empty surface")
        if _CJK_RE.search(raw) and not re.search(r"[A-Za-z]", raw):
            return MeshLinkResult(raw, normalized, "skipped_no_english_surface", reason="中文表面形式没有审核英文/MeSH 产物；链接器不自动翻译")
        expanded_from = None
        abbreviations = extract_abbreviations(context) if context else {}
        abbreviation = abbreviations.get(raw)
        query_forms = [raw]
        if abbreviation:
            expanded_from = abbreviation
            query_forms.append(abbreviation)
        variants: list[str] = []
        for form in query_forms:
            variants.extend(normalized_variants(form))
        variants = list(dict.fromkeys(variants))

        rows = self.lexicon.exact_matches(variants)
        candidates: list[MeshCandidate] = []
        if rows:
            for ui, matched_surface, term_ui in rows:
                descriptor = self.lexicon.by_ui.get(ui)
                if descriptor is None:
                    continue
                if term_ui == _REVIEWED_ALIAS_MARKER:
                    method = "reviewed_alias"
                    matched_term_ui = None
                elif normalize_text(matched_surface) == normalize_text(descriptor.label):
                    method = "exact_label"
                    matched_term_ui = term_ui or None
                else:
                    method = "exact_entry_term"
                    matched_term_ui = term_ui or None
                candidates.append(MeshCandidate(
                    ui=ui, label=descriptor.label, score=1.0, method=method,
                    matched_surface=matched_surface, matched_term_ui=matched_term_ui,
                    tree_numbers=descriptor.tree_numbers,
                ))
        else:
            candidates = self._lexical_candidates(query_forms, variants)
            semantic_candidates = self._semantic_candidates(query_forms[-1], [candidate.ui for candidate in candidates] or list(self.lexicon.by_ui))
            if semantic_candidates:
                by_ui = {candidate.ui: candidate for candidate in candidates}
                for ui, score in semantic_candidates.items():
                    descriptor = self.lexicon.by_ui.get(ui)
                    if descriptor is None:
                        continue
                    if ui in by_ui:
                        existing = by_ui[ui]
                        if score > existing.score:
                            by_ui[ui] = MeshCandidate(
                                ui=ui, label=descriptor.label, score=score, method="semantic",
                                matched_surface=descriptor.label,
                                tree_numbers=descriptor.tree_numbers,
                            )
                    else:
                        by_ui[ui] = MeshCandidate(
                            ui=ui, label=descriptor.label, score=score, method="semantic",
                            matched_surface=descriptor.label,
                            tree_numbers=descriptor.tree_numbers,
                        )
                candidates = list(by_ui.values())

        candidates = _dedupe_candidates(candidates)
        candidates.sort(key=lambda item: (-item.score, item.ui))
        limit = self.max_candidates if top_k is None else max(1, int(top_k))
        candidates = candidates[:limit]
        if not candidates:
            return MeshLinkResult(raw, normalized, "unlinked", (), expanded_from, "没有可比较的 MeSH 候选")
        above = [
            candidate for candidate in candidates
            if candidate.score >= (self.semantic_min_score if candidate.method == "semantic" else self.min_score)
        ]
        if not above:
            return MeshLinkResult(
                raw, normalized, "unlinked", tuple(candidates), expanded_from,
                f"最高分 {candidates[0].score:.3f} 低于链接阈值",
            )
        best = above[0]
        second = above[1] if len(above) > 1 else None
        if second is not None and second.ui != best.ui and (best.score - second.score) <= self.ambiguity_margin:
            return MeshLinkResult(
                raw, normalized, "ambiguous", tuple(candidates), expanded_from,
                f"前两名分差 {best.score - second.score:.3f} ≤ {self.ambiguity_margin:.2f}",
            )
        return MeshLinkResult(raw, normalized, "linked", tuple(candidates), expanded_from, None)

    def _lexical_candidates(self, query_forms: Sequence[str], variants: Sequence[str]) -> list[MeshCandidate]:
        tokens: set[str] = set()
        for form in query_forms:
            tokens.update(token_features(form))
        query_tokens = {token for token in tokens if token not in _ENTITY_STOPWORDS}
        overlap: dict[str, int] = {}
        for token in query_tokens:
            for ui in self.lexicon.candidates_by_tokens((token,)):
                overlap[ui] = overlap.get(ui, 0) + 1
        if not overlap:
            return []
        candidate_uis = [
            ui for ui, _count in sorted(overlap.items(), key=lambda item: (-item[1], item[0]))[: self.max_token_candidates]
        ]
        rows: list[MeshCandidate] = []
        for ui in candidate_uis:
            descriptor = self.lexicon.by_ui.get(ui)
            if descriptor is None:
                continue
            best = None
            best_surface = ""
            for surface, term_ui in self.lexicon.surfaces(ui):
                for query in query_forms:
                    score = _surface_similarity(query, surface)
                    if best is None or score > best:
                        best = score
                        best_surface = surface
            if best is None or best <= 0.0:
                continue
            method = "token_overlap" if best >= 0.75 else "fuzzy"
            rows.append(MeshCandidate(
                ui=ui, label=descriptor.label, score=min(best, 0.99), method=method,
                matched_surface=best_surface, tree_numbers=descriptor.tree_numbers,
            ))
        rows.sort(key=lambda item: (-item.score, item.ui))
        return rows

    def _semantic_candidates(self, query: str, candidate_uis: Sequence[str]) -> dict[str, float]:
        if self.semantic_scorer is None:
            return {}
        available = getattr(self.semantic_scorer, "available", True)
        if not available:
            self.last_semantic_error = str(getattr(self.semantic_scorer, "unavailable_reason", "") or "语义打分器不可用")
            return {}
        try:
            result = dict(self.semantic_scorer.score(query, candidate_uis))
            self.last_semantic_error = None
            return result
        except Exception as exc:
            self.last_semantic_error = f"语义打分失败：{type(exc).__name__}"
            return {}

    def verify_mapping(self, concept: str, english: Sequence[str], mesh: Sequence[str]) -> dict[str, Any]:
        products = [str(item).strip() for item in (*english, *mesh) if str(item).strip()]
        if not products:
            return {
                "concept": concept, "status": "skipped_no_english_surface",
                "linked_uis": [], "products": {},
                "reason": "该概念只有中文表面形式；未自动翻译。",
            }
        results = {product: self.link(product) for product in dict.fromkeys(products)}
        linked = {product: result.best.ui for product, result in results.items() if result.status == "linked" and result.best}
        statuses = {result.status for result in results.values()}
        linked_uis = sorted(set(linked.values()))
        if "ambiguous" in statuses:
            status = "ambiguous"
            reason = "至少一个英文/MeSH 产物对应多个候选。"
        elif not linked_uis:
            status = "unlinked"
            reason = "没有任何产物达到链接阈值。"
        elif len(linked_uis) > 1:
            status = "conflict"
            reason = "英文与 MeSH 产物指向不同 MeSH 描述符。"
        else:
            status = "verified"
            reason = None
        return {
            "concept": concept,
            "status": status,
            "linked_uis": linked_uis,
            "products": {product: result.to_dict() for product, result in results.items()},
            "reason": reason,
        }

    def describe(self) -> dict[str, Any]:
        reason = getattr(self.semantic_scorer, "_load_error", None) if self.semantic_scorer is not None else None
        dependencies: list[str] = []
        if self.semantic_scorer is not None and reason is None:
            import importlib.util
            dependencies = [name for name in ("torch", "transformers") if importlib.util.find_spec(name) is None]
            if dependencies:
                reason = "依赖未安装：" + "、".join(dependencies)
        semantic = {
            "configured": self.semantic_scorer is not None,
            "name": getattr(self.semantic_scorer, "name", None),
            "surface_mode": getattr(self.semantic_scorer, "surface_mode", None),
            "surface_count": len(getattr(self.semantic_scorer, "_surface_rows", ())),
            "loaded": bool(getattr(self.semantic_scorer, "_model", None) is not None) if self.semantic_scorer is not None else False,
            "reason": reason,
        }
        return {
            "lexicon": self.lexicon.describe(),
            "min_score": self.min_score,
            "semantic_min_score": self.semantic_min_score,
            "ambiguity_margin": self.ambiguity_margin,
            "semantic": semantic,
        }


_METHOD_PRIORITY = {
    "exact_label": 0,
    "exact_entry_term": 1,
    "reviewed_alias": 2,
    "token_overlap": 3,
    "fuzzy": 4,
    "semantic": 5,
}


def _dedupe_candidates(candidates: Sequence[MeshCandidate]) -> list[MeshCandidate]:
    """Keep one candidate per descriptor; official matches beat alias/fuzzy."""
    unique: dict[str, MeshCandidate] = {}
    for candidate in candidates:
        existing = unique.get(candidate.ui)
        if existing is None:
            unique[candidate.ui] = candidate
            continue
        existing_priority = _METHOD_PRIORITY.get(existing.method, 99)
        candidate_priority = _METHOD_PRIORITY.get(candidate.method, 99)
        if (candidate.score, -candidate_priority) > (existing.score, -existing_priority):
            unique[candidate.ui] = candidate
    return list(unique.values())


def _surface_similarity(query: str, surface: str) -> float:
    q_tokens = set(_content_tokens(query))
    s_tokens = set(_content_tokens(surface))
    if not q_tokens or not s_tokens:
        return 0.0
    overlap = len(q_tokens & s_tokens)
    dice = 2.0 * overlap / (len(q_tokens) + len(s_tokens))
    ratio = difflib.SequenceMatcher(None, normalize_text(query), normalize_text(surface)).ratio()
    containment = 1.0 if (q_tokens <= s_tokens or s_tokens <= q_tokens) and min(len(q_tokens), len(s_tokens)) >= 2 else 0.0
    return max(0.0, min(1.0, 0.50 * dice + 0.35 * ratio + 0.15 * containment))


def apply_entity_linking(
    plan: Any,
    linker: MeshLinker,
    *,
    mode: str,
    context: str | None = None,
    max_entry_terms: int = 2,
) -> tuple[Any, dict[str, Any]]:
    """Verify/link plan terminology and optionally add MeSH entry terms.

    ``off`` leaves the plan untouched.  ``shadow`` records links in the trace
    only.  ``active`` adds verified descriptor labels and a small number of
    official entry terms to ``english_terms`` / ``mesh_terms`` and appends a
    new query variant.  Reviewed request/library rows keep precedence: their
    concepts are verified but never replaced.
    """
    from dataclasses import replace

    normalized_mode = str(mode or "off").strip().lower()
    if normalized_mode not in MESH_LINKING_MODES:
        raise ValueError("entity_linking_mode must be one of: " + ", ".join(MESH_LINKING_MODES))
    trace: dict[str, Any] = {
        "mode": normalized_mode,
        "applied": normalized_mode == "active",
        "lexicon": linker.lexicon.describe(),
        "linker": linker.describe(),
        "links": [],
        "mapping_verifications": [],
        "added_terms": [],
    }
    if normalized_mode == "off":
        return plan, trace
    context_text = context if context is not None else str(getattr(plan, "original_question", "") or "")
    conflict_uis: set[str] = set()
    for entry in getattr(plan, "term_mapping", ()):
        verification = linker.verify_mapping(entry.concept, entry.english, entry.mesh)
        trace["mapping_verifications"].append(verification)
        if verification.get("status") in {"conflict", "ambiguous"}:
            conflict_uis.update(str(ui) for ui in verification.get("linked_uis") or ())

    surfaces: list[str] = []
    for entry in getattr(plan, "term_mapping", ()):
        surfaces.extend(entry.english)
        surfaces.extend(entry.mesh)
    surfaces.extend(getattr(plan, "question_english_terms", ()))
    surfaces.extend(getattr(plan, "english_terms", ()))
    surfaces.extend(getattr(plan, "mesh_terms", ()))
    for concept in getattr(plan, "concepts", ()):
        if re.search(r"[A-Za-z]", str(concept)) and not _CJK_RE.search(str(concept)):
            surfaces.append(str(concept))
    surfaces = [
        surface for surface in dict.fromkeys(surface.strip() for surface in surfaces if surface and surface.strip())
        if surface and any(token not in _ENTITY_STOPWORDS for token in normalize_text(surface).split())
    ]

    linked_rows: list[dict[str, Any]] = []
    added_english: list[str] = []
    added_mesh: list[str] = []
    applied_labels: list[str] = []
    seen_uis: set[str] = set()
    for surface in surfaces:
        result = linker.link(surface, context=context_text)
        row = result.to_dict()
        row["origin"] = "entity_linker"
        linked_rows.append(row)
        if result.status != "linked" or result.best is None:
            continue
        descriptor = linker.lexicon.by_ui.get(result.best.ui)
        if descriptor is None or descriptor.ui in seen_uis:
            continue
        seen_uis.add(descriptor.ui)
        if descriptor.ui in conflict_uis:
            continue
        if normalized_mode == "active":
            applied_labels.append(descriptor.label)
            added_mesh.append(descriptor.label)
            entry_terms = [
                term.text for term in descriptor.terms
                if term.text and normalize_text(term.text) != normalize_text(descriptor.label)
                and len(_content_tokens(term.text)) >= 1
            ][:max_entry_terms]
            added_english.extend(entry_terms)
    trace["links"] = linked_rows
    if linker.last_semantic_error:
        trace["semantic_error"] = linker.last_semantic_error
    if normalized_mode != "active":
        return plan, trace

    english = list(dict.fromkeys((*getattr(plan, "english_terms", ()), *added_english)))
    mesh = list(dict.fromkeys((*getattr(plan, "mesh_terms", ()), *added_mesh)))
    trace["added_terms"] = [{"kind": "english", "terms": added_english}, {"kind": "mesh", "terms": added_mesh}]
    if not added_english and not added_mesh:
        return plan, trace
    variants = list(getattr(plan, "variants", ()))
    linked_variant = " ".join(dict.fromkeys((*added_mesh, *added_english)))
    if linked_variant and linked_variant not in variants:
        variants.append(linked_variant)
    mapping_trace = list(getattr(plan, "mapping_trace", ()))
    if added_mesh or added_english:
        mapping_trace.append({
            "concept": " + ".join(applied_labels),
            "english": added_english,
            "mesh": added_mesh,
            "source": "entity_linker",
            "origin": "entity_linker",
            "terms": list(dict.fromkeys((*added_mesh, *added_english))),
            "status": "expanded",
            "link_status": "linked",
        })
    return replace(plan, english_terms=tuple(english), mesh_terms=tuple(mesh), variants=tuple(variants), mapping_trace=tuple(mapping_trace)), trace


def load_linker(
    lexicon_path: str | Path,
    *,
    semantic: bool = False,
    semantic_cache_dir: str | None = None,
    semantic_surface_mode: str = "all",
    max_surfaces_per_descriptor: int = 0,
    max_token_candidates: int = 200,
    min_score: float = 0.86,
    ambiguity_margin: float = 0.05,
) -> MeshLinker:
    lexicon = MeshLexicon.load(lexicon_path)
    scorer = BgeM3SemanticScorer(
        lexicon,
        cache_dir=semantic_cache_dir,
        surface_mode=semantic_surface_mode,
        max_surfaces_per_descriptor=max_surfaces_per_descriptor,
    ) if semantic else None
    return MeshLinker(
        lexicon=lexicon,
        semantic_scorer=scorer,
        max_token_candidates=max_token_candidates,
        min_score=min_score,
        ambiguity_margin=ambiguity_margin,
    )
