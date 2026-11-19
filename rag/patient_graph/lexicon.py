"""Patient-fact vocabulary and query resolution for the patient graph.

``patient_fact_lexicon.json`` is a *retrieval vocabulary* over the structured
HIS/discharge records.  It maps surface forms (Chinese, English, abbreviations)
to patient-fact concepts and to question intents.  It is not a clinical rule
set: a concept says where a fact may be recorded, not what the fact means for
treatment.

Two resolution layers are kept separate on purpose:

* ``question_intents`` answer "which record is being asked about" (sex, GA,
  discharge diagnosis, phototherapy, ...).  Intent aliases are matched longest
  first so ``入院诊断`` does not also select ``出院诊断``.
* ``concepts`` answer "which clinical fact appears in this record".  Concept
  aliases are matched against field names and string values during graph
  construction, and against the user query for free-text retrieval.

Unresolved text is returned as ``unresolved_terms`` instead of being forced
onto the closest concept.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_LEXICON_PATH = Path(__file__).resolve().parent / "patient_fact_lexicon.json"
_SUBJECT_MOTHER = ("母亲", "母体", "产妇", "孕妇", "妈妈", "maternal", "mother")
_LATEST_CUES = ("最新", "最近", "最后一次", "当前", "现在", "latest", "most recent", "current")
_HISTORY_CUES = ("既往", "以前", "之前", "历史", "history", "previous", "prior")
_UNCERTAIN_CUES = ("待排", "可能", "疑似", "可疑", "不确定", "?", "？")
_NEGATION_RE = re.compile(r"(?:^|[，。；;、\s])(?:无|未|否认|不伴)(?![创法])|没有(?=记录|做|用|出现|发生)")
_ASCII_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9 .+\-]*$")


class LexiconError(ValueError):
    """Raised when the patient-fact lexicon is missing or malformed."""


@dataclass(frozen=True)
class ConceptSpec:
    concept_id: str
    label: str
    kind: str
    aliases: tuple[str, ...]
    collections: tuple[str, ...]
    fields: tuple[str, ...]
    exclude_aliases: tuple[str, ...] = ()
    field_values: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "label": self.label,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "collections": list(self.collections),
            "fields": list(self.fields),
            "exclude_aliases": list(self.exclude_aliases),
            "field_values": {key: list(values) for key, values in self.field_values},
        }


@dataclass(frozen=True)
class IntentSpec:
    intent: str
    labels: tuple[str, ...]
    concept_ids: tuple[str, ...]
    collections: tuple[str, ...]
    fields: tuple[str, ...]
    aggregate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "labels": list(self.labels),
            "concept_ids": list(self.concept_ids),
            "collections": list(self.collections),
            "fields": list(self.fields),
            "aggregate": self.aggregate,
        }


@dataclass(frozen=True)
class QueryResolution:
    input_queries: tuple[str, ...]
    intents: tuple[IntentSpec, ...] = ()
    concepts: tuple[ConceptSpec, ...] = ()
    matched_aliases: tuple[tuple[str, str], ...] = ()
    subject: str | None = None
    temporal: str | None = None
    negated: bool = False
    uncertain: bool = False
    unresolved_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": list(self.input_queries),
            "intents": [spec.intent for spec in self.intents],
            "concepts": [spec.concept_id for spec in self.concepts],
            "matched_aliases": [
                {"alias": alias, "resolved_to": target}
                for alias, target in self.matched_aliases
            ],
            "subject": self.subject,
            "temporal": self.temporal,
            "negated": self.negated,
            "uncertain": self.uncertain,
            "unresolved_terms": list(self.unresolved_terms),
        }


class PatientRecordLexicon:
    def __init__(
        self,
        concepts: Sequence[ConceptSpec],
        intents: Sequence[IntentSpec],
        *,
        schema_version: str = "",
        source_path: str | None = None,
    ) -> None:
        if not concepts:
            raise LexiconError("patient-fact lexicon has no concepts")
        concept_ids = [spec.concept_id for spec in concepts]
        if len(concept_ids) != len(set(concept_ids)):
            raise LexiconError("duplicate concept_id in patient-fact lexicon")
        intent_ids = [spec.intent for spec in intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise LexiconError("duplicate intent in patient-fact lexicon")
        known = set(concept_ids)
        for spec in intents:
            missing = [concept_id for concept_id in spec.concept_ids if concept_id not in known]
            if missing:
                raise LexiconError(
                    f"intent {spec.intent} references unknown concepts: {', '.join(missing)}"
                )
        for spec in concepts:
            if not spec.aliases:
                raise LexiconError(f"concept {spec.concept_id} has no aliases")
        self.concepts = tuple(concepts)
        self.intents = tuple(intents)
        self.schema_version = schema_version
        self.source_path = source_path
        self._concept_by_id = {spec.concept_id: spec for spec in self.concepts}
        # Longest alias first: 入院诊断 must win over a shorter overlapping alias.
        self._concept_aliases = sorted(
            (
                (alias, spec)
                for spec in self.concepts
                for alias in (_normalize(item) for item in spec.aliases)
                if alias
            ),
            key=lambda pair: (-len(pair[0]), pair[1].concept_id),
        )
        self._intent_aliases = sorted(
            (
                (alias, spec)
                for spec in self.intents
                for alias in (_normalize(item) for item in spec.labels)
                if alias
            ),
            key=lambda pair: (-len(pair[0]), pair[1].intent),
        )
        # field name -> concept ids, grouped by normalized field
        field_index: dict[str, list[str]] = {}
        for spec in self.concepts:
            for field_name in spec.fields:
                field_index.setdefault(_normalize(field_name), []).append(spec.concept_id)
        self._field_index = {
            key: tuple(dict.fromkeys(value)) for key, value in field_index.items()
        }

    # -- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "PatientRecordLexicon":
        target = Path(path) if path else DEFAULT_LEXICON_PATH
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise LexiconError(f"patient-fact lexicon not found: {target}") from None
        except json.JSONDecodeError as exc:
            raise LexiconError(f"patient-fact lexicon is not valid JSON: {exc}") from None
        if not isinstance(raw, Mapping):
            raise LexiconError("patient-fact lexicon must be a JSON object")
        concepts = []
        for row in raw.get("concepts") or ():
            if not isinstance(row, Mapping):
                raise LexiconError("concepts must be objects")
            concept_id = str(row.get("concept_id") or "").strip()
            if not concept_id:
                raise LexiconError("concept_id is required")
            raw_values = row.get("field_values") or {}
            if raw_values and not isinstance(raw_values, Mapping):
                raise LexiconError(f"concept {concept_id} field_values must be an object")
            concepts.append(ConceptSpec(
                concept_id=concept_id,
                label=str(row.get("label") or concept_id),
                kind=str(row.get("kind") or "fact"),
                aliases=tuple(_strings(row.get("aliases"))),
                collections=tuple(_strings(row.get("collections"))),
                fields=tuple(_strings(row.get("fields"))),
                exclude_aliases=tuple(_strings(row.get("exclude_aliases"))),
                field_values=tuple(
                    (str(key), tuple(_strings(values)))
                    for key, values in raw_values.items()
                ),
            ))
        intents = []
        for row in raw.get("question_intents") or ():
            if not isinstance(row, Mapping):
                raise LexiconError("question_intents must be objects")
            intent = str(row.get("intent") or "").strip()
            if not intent:
                raise LexiconError("question intent id is required")
            intents.append(IntentSpec(
                intent=intent,
                labels=tuple(_strings(row.get("labels"))),
                concept_ids=tuple(_strings(row.get("concept_ids"))),
                collections=tuple(_strings(row.get("collections"))),
                fields=tuple(_strings(row.get("fields"))),
                aggregate=bool(row.get("aggregate")),
            ))
        return cls(
            concepts,
            intents,
            schema_version=str(raw.get("schema_version") or ""),
            source_path=str(target),
        )

    # -- lookups ---------------------------------------------------------
    def concept(self, concept_id: str) -> ConceptSpec | None:
        return self._concept_by_id.get(concept_id)

    def concept_for_field(self, field_name: str) -> tuple[str, ...]:
        return self._field_index.get(_normalize(field_name), ())

    def concepts_for_record(self, collection: str, fields: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Return concept -> provenance for one structured record.

        Provenance carries the field name (when the link came from a known
        column) or the matched literal value.  The status is the field-level
        status when the record stores one (``*_status``), otherwise the record
        ``data_status``.  A ``not_recorded`` field still produces an edge: the
        graph must be able to say "this is the record that would hold the
        fact, and it is not recorded" instead of silently returning nothing.
        """
        found: dict[str, dict[str, Any]] = {}
        for spec in self.concepts:
            if spec.collections and collection not in spec.collections:
                continue
            if not _field_values_ok(spec, fields):
                continue
            for field_name in spec.fields:
                value = _field_value(fields, field_name)
                if value is _MISSING:
                    continue
                status = _field_status(fields, field_name, value)
                found.setdefault(spec.concept_id, {
                    "concept_id": spec.concept_id,
                    "field": field_name,
                    "status": status,
                    "value_present": _present(value),
                    "match": "field",
                })
        for key, value in fields.items():
            if not _scannable_field(key, value):
                continue
            text = str(value)
            for concept_id in self.match_concept_ids(text):
                spec = self._concept_by_id[concept_id]
                if spec.collections and collection not in spec.collections:
                    continue
                entry = found.get(concept_id)
                # A known structured field is stronger provenance than a
                # literal mention inside narrative text; keep the field entry.
                if entry and entry.get("match") == "field":
                    continue
                found.setdefault(concept_id, {
                    "concept_id": concept_id,
                    "field": key,
                    "status": _field_status(fields, key, value),
                    "value_present": _present(value),
                    "match": "value",
                })
        return found

    def match_concept_ids(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        if not normalized:
            return ()
        hits: list[str] = []
        consumed: list[tuple[int, int]] = []
        excluded: set[str] = set()
        for alias, spec in self._concept_aliases:
            if spec.exclude_aliases and any(
                _find(normalized, _normalize(exclude)) >= 0
                for exclude in spec.exclude_aliases
            ):
                excluded.add(spec.concept_id)
                continue
            start = _find(normalized, alias)
            if start < 0:
                continue
            end = start + len(alias)
            if any(start < used_end and end > used_start for used_start, used_end in consumed):
                # Overlapping aliases resolve longest-first; do not double-count
                # a shorter alias inside a longer one.
                continue
            consumed.append((start, end))
            hits.append(spec.concept_id)
        return tuple(concept_id for concept_id in dict.fromkeys(hits) if concept_id not in excluded)

    # -- query resolution ------------------------------------------------
    def resolve_queries(self, queries: Iterable[str]) -> QueryResolution:
        texts = tuple(str(query) for query in queries if str(query or "").strip())
        joined = " ".join(texts)
        normalized = _normalize(joined)
        intents: list[IntentSpec] = []
        matched: list[tuple[str, str]] = []
        consumed: list[tuple[int, int]] = []
        seen_intents: set[str] = set()
        for alias, spec in self._intent_aliases:
            start = _find(normalized, alias)
            if start < 0:
                continue
            end = start + len(alias)
            if any(start < used_end and end > used_start for used_start, used_end in consumed):
                continue
            consumed.append((start, end))
            if spec.intent in seen_intents:
                continue
            seen_intents.add(spec.intent)
            intents.append(spec)
            matched.append((alias, f"intent:{spec.intent}"))
        concepts = [
            self._concept_by_id[concept_id]
            for concept_id in self.match_concept_ids(joined)
            if self._concept_by_id.get(concept_id)
        ]
        for spec in concepts:
            label = _normalize(spec.label)
            if label and _find(normalized, label) >= 0:
                matched.append((label, f"concept:{spec.concept_id}"))
        # A concept already selected by an intent is not a second resolution.
        intent_concepts = {cid for spec in intents for cid in spec.concept_ids}
        concepts = [spec for spec in concepts if spec.concept_id not in intent_concepts]
        subject = "mother" if any(cue in normalized for cue in _SUBJECT_MOTHER) else None
        temporal = None
        if any(cue in normalized for cue in _LATEST_CUES):
            temporal = "latest"
        elif any(cue in normalized for cue in _HISTORY_CUES):
            temporal = "history"
        negated = bool(_NEGATION_RE.search(normalized)) and not any(
            cue in normalized for cue in ("有没有", "是否")
        )
        uncertain = any(cue in normalized for cue in _UNCERTAIN_CUES)
        return QueryResolution(
            input_queries=texts,
            intents=tuple(intents),
            concepts=tuple(concepts),
            matched_aliases=tuple(matched),
            subject=subject,
            temporal=temporal,
            negated=negated,
            uncertain=uncertain,
            unresolved_terms=_leftover_terms(normalized, consumed),
        )


_MISSING = object()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split("|") if item.strip()]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return re.sub(r"\s+", " ", value).strip()


def _find(text: str, alias: str) -> int:
    if not alias:
        return -1
    if _ASCII_ALIAS_RE.match(alias):
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text)
        return match.start() if match else -1
    return text.find(alias)


def _field_value(fields: Mapping[str, Any], field_name: str) -> Any:
    if field_name in fields:
        return fields[field_name]
    wanted = _normalize(field_name)
    for key, value in fields.items():
        if _normalize(key) == wanted:
            return value
    return _MISSING


def _field_values_ok(spec: ConceptSpec, fields: Mapping[str, Any]) -> bool:
    """Check specimen/method constraints before a concept may claim a record.

    A concept with ``field_values`` (for example blood culture) is blocked when
    the record carries a conflicting specimen.  When the constraint field is
    absent, the concept is allowed only if one of its own field names already
    encodes the value (``blood_culture_status``); this prevents a generic
    ``culture_status`` record with no specimen from being silently merged into
    a specimen-specific concept.
    """
    for constraint_field, allowed in spec.field_values:
        if not allowed:
            continue
        value = _field_value(fields, constraint_field)
        if value is not _MISSING and _present(value):
            text = _normalize(value)
            if not any(_normalize(item) in text for item in allowed):
                return False
            continue
        if not any(
            any(_normalize(item) in field_name.lower() for item in allowed)
            for field_name in spec.fields
        ):
            return False
    return True


def _field_status(fields: Mapping[str, Any], field_name: str, value: Any) -> str:
    companion = fields.get(f"{field_name}_status")
    if isinstance(companion, str) and companion.strip():
        return companion.strip()
    data_status = str(fields.get("data_status") or "").strip()
    if data_status:
        if data_status == "observed_negative" and not _present(value):
            return "observed_negative"
        if data_status == "not_recorded" and value is not None and not _present(value):
            return "not_recorded"
        return data_status
    if value is None:
        return "not_recorded"
    if isinstance(value, bool):
        return "observed_positive" if value else "observed_negative"
    return "observed_positive" if _present(value) else "not_recorded"


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text in {"none", "null", "nan"}:
            return False
        return not any(
            marker in text
            for marker in ("无提及", "未提及", "未记录", "无记录", "not recorded", "no record")
        )
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _scannable_field(key: str, value: Any) -> bool:
    lowered = key.lower()
    if not isinstance(value, str) or not value.strip():
        return False
    if any(token in lowered for token in (
        "status", "unit", "precision", "source", "derivation", "subject_role",
        "mapping", "file", "path", "time", "date",
    )):
        return False
    return len(value) <= 4000


def _leftover_terms(normalized: str, consumed: Sequence[tuple[int, int]]) -> tuple[str, ...]:
    """Return CJK/ASCII tokens not covered by any matched alias.

    This is diagnostic only: it explains which query text did not resolve, so a
    caller can ask the user for clarification instead of assuming a concept.
    """
    if not normalized:
        return ()
    covered = [False] * len(normalized)
    for start, end in consumed:
        for index in range(max(0, start), min(len(normalized), end)):
            covered[index] = True
    terms = []
    for match in re.finditer(r"[a-z0-9]+(?:[-./][a-z0-9]+)*|[\u4e00-\u9fff]{2,}", normalized):
        start, end = match.start(), match.end()
        if not all(covered[start:end]):
            terms.append(match.group(0))
    return tuple(dict.fromkeys(terms))


_DEFAULT: PatientRecordLexicon | None = None


def default_lexicon() -> PatientRecordLexicon:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PatientRecordLexicon.load()
    return _DEFAULT


def load_lexicon(path: str | Path | None = None) -> PatientRecordLexicon:
    if path is None:
        return default_lexicon()
    return PatientRecordLexicon.load(path)
