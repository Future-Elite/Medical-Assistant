"""Query-side medical entity fidelity audit for cross-lingual retrieval (research, v0).

This module implements the *object layer* of the query fidelity research mechanism: it
treats the products of Chinese->English query conversion (reviewed terminology
mapping, machine-translated query variants, explicit terms sent to multilingual
encoders) as first-class audit objects, and checks — slot by slot — whether the
medical entities of the original Chinese question survive the conversion.

Design boundaries (documented in the paper's method section):

- The auditor is a deterministic lexical baseline (suffix/marker rules and an
  optional caller-provided zh<->en term lexicon). It is NOT a medical NER and
  NOT an entailment model. Its verdicts are *hypotheses* whose quality is
  measured against human annotation in the experiments; verdict names are
  deliberately distinct from the human annotation labels used in the experiments.
- No corpus is built into the package. The zh<->en lexicon is injectable and
  owned by the caller (same policy as custom sources: the caller is responsible
  for content licensing).
- The audit never modifies the query plan or the reviewer-supplied terms. The
  companion ``FidelityGate`` (``gate.py``) decides whether a route's signal may
  enter fusion, and every decision is traced.

Automatic verdict vocabulary (NOT identical to annotation labels):

- ``preserved`` — evidence for the slot found in the conversion product;
- ``suspected_drop`` — the expected English for a slot is absent;
- ``unexpected_entity`` — a product mentions a lexicon entity that no question
  slot accounts for (fabrication/contamination proxy);
- ``numeric_loss`` / ``numeric_added`` — question numbers missing/added;
- ``suspected_negation_loss`` — the question carries a negation/absence marker
  but the product has no English negation expression;
- ``suspected_temporal_loss`` — the question carries a temporal marker but the
  product has no English time expression;
- ``unverifiable`` — no expected English is available (no reviewed mapping, no
  lexicon hit); recorded as a coverage gap, never escalated;
- ``no_explicit_conversion`` — route has no explicit conversion product to
  audit (e.g. a multilingual encoder that only receives the Chinese original).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Verdicts, levels, routes
# ---------------------------------------------------------------------------

VERDICT_PRESERVED = "preserved"
VERDICT_UNVERIFIABLE = "unverifiable"
VERDICT_SUSPECTED_DROP = "suspected_drop"
VERDICT_UNEXPECTED_ENTITY = "unexpected_entity"
VERDICT_NUMERIC_LOSS = "numeric_loss"
VERDICT_NUMERIC_ADDED = "numeric_added"
VERDICT_NEGATION_LOSS = "suspected_negation_loss"
VERDICT_TEMPORAL_LOSS = "suspected_temporal_loss"
VERDICT_NO_CONVERSION = "no_explicit_conversion"

LEVEL_ORDER = ("L0", "L1", "L2", "L3")

PRIMARY_SLOT_TYPES = ("disease", "drug")

ROUTES = ("reviewed_mapping", "translate", "multilingual_encoding")

ROUTE_LABELS = {
    "reviewed_mapping": "审核术语映射",
    "translate": "机器翻译",
    "multilingual_encoding": "多语言编码",
}

_STATUS_OK = "ok"
_STATUS_ABSENT = "absent"
_STATUS_NOT_CONFIGURED = "not_configured"
_STATUS_NO_PRODUCT = "no_product"
_STATUS_FINDINGS = "findings"

# ---------------------------------------------------------------------------
# Lexicon (injectable zh<->en terminology resource; caller-owned licensing)
# ---------------------------------------------------------------------------

_ASCII_LETTER_RE = re.compile(r"[a-z0-9]", re.I)


@dataclass(frozen=True)
class TermEntry:
    zh: str
    en: tuple[str, ...]
    kind: str = "medical"  # "disease" / "drug" / "medical"

    def to_dict(self) -> dict[str, Any]:
        return {"zh": self.zh, "en": list(self.en), "kind": self.kind}


def _phrase_in(text_lower: str, phrase: str) -> bool:
    needle = phrase.lower().strip()
    if not needle:
        return False
    if _ASCII_LETTER_RE.search(needle):
        return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text_lower) is not None
    return needle in text_lower


class FidelityLexicon:
    """Static zh<->en term list used by the auditor.

    The product package ships no lexicon; callers load their own file
    (``from_json_file``) or build entries programmatically. Entries are matched
    by exact Chinese surface containment (longest first) and by English phrase
    boundaries (case-insensitive).
    """

    def __init__(self, entries: Sequence[TermEntry] = ()) -> None:
        cleaned = [
            TermEntry(
                zh=str(entry.zh).strip(),
                en=tuple(str(item).strip() for item in entry.en if str(item).strip()),
                kind=str(entry.kind or "medical").strip() or "medical",
            )
            for entry in entries
        ]
        self.entries = tuple(entry for entry in cleaned if entry.zh and entry.en)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FidelityLexicon":
        raw_entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
            raise ValueError("lexicon payload must be an object with an 'entries' array")
        entries = []
        for item in raw_entries:
            if not isinstance(item, Mapping):
                raise ValueError("lexicon entries must be objects")
            zh = str(item.get("zh") or "").strip()
            en_raw = item.get("en")
            en = [str(value) for value in en_raw] if isinstance(en_raw, Sequence) and not isinstance(en_raw, (str, bytes)) else [str(en_raw or "")]
            if not zh or not any(part.strip() for part in en):
                raise ValueError("lexicon entry requires 'zh' and non-empty 'en'")
            entries.append(TermEntry(zh=zh, en=tuple(en), kind=str(item.get("kind") or "medical")))
        return cls(entries)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "FidelityLexicon":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    def match_zh(self, question: str) -> list[tuple[TermEntry, int, int]]:
        """Longest-first, non-overlapping Chinese surface matches."""
        matches: list[tuple[TermEntry, int, int]] = []
        taken: list[tuple[int, int]] = []
        for entry in sorted(self.entries, key=lambda e: (-len(e.zh), e.zh)):
            start = question.find(entry.zh)
            while start != -1:
                end = start + len(entry.zh)
                if not any(start < t_end and end > t_start for t_start, t_end in taken):
                    matches.append((entry, start, end))
                    taken.append((start, end))
                start = question.find(entry.zh, start + 1)
        matches.sort(key=lambda item: (item[1], -item[2]))
        return matches

    def match_english(self, text: str) -> list[tuple[TermEntry, str]]:
        """Entries whose English phrase occurs in ``text`` (word boundaries)."""
        lowered = text.lower()
        out: list[tuple[TermEntry, str]] = []
        for entry in self.entries:
            for phrase in entry.en:
                if phrase and _phrase_in(lowered, phrase):
                    out.append((entry, phrase))
                    break
        return out


# ---------------------------------------------------------------------------
# Slot extraction (deterministic baseline; quality measured by experiments)
# ---------------------------------------------------------------------------

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(月龄|个月|周龄|小时|分钟|天|日|岁|年|mg/kg|mg|μg|g|kg|ml|mL|L|℃|°C|%|mmHg|IU|次/分)?")

_DISEASE_SUFFIXES = (
    "综合征", "心律失常", "冠心病", "高血压", "糖尿病", "白血病", "骨质疏松",
    "红斑狼疮", "阿尔茨海默", "帕金森", "银屑病", "青光眼", "白内障", "中耳炎",
    "鼻炎", "咽炎", "胃炎", "肠炎", "肝炎", "胰腺炎", "胆囊炎", "阑尾炎",
    "关节炎", "皮炎", "湿疹", "痴呆", "癫痫", "哮喘", "肺炎", "肾炎", "脑梗",
    "心梗", "栓塞", "贫血", "黄疸", "发热", "咳嗽", "腹泻", "水肿", "积液",
    "甲亢", "甲减", "痛风", "肥胖", "溃疡", "结石", "骨折", "创伤", "休克",
    "衰竭", "硬化", "梗死", "狭窄", "畸形", "感染", "中毒", "病", "症", "癌",
    "炎", "瘤", "征", "颤", "疸",
)
_DRUG_SUFFIXES = (
    "阿司匹林", "氯吡格雷", "布洛芬", "对乙酰氨基酚", "二甲双胍", "胰岛素",
    "免疫球蛋白", "注射液", "胶囊", "单抗", "沙坦", "洛尔", "普利", "他汀",
    "霉素", "西林", "环素", "沙星", "列汀", "列净", "糖肽", "头孢", "肝素",
    "疫苗", "激素", "片", "素", "酸", "碱", "胺", "醇", "钠", "钾", "钙",
    "铁", "锌", "胍",
)

_DISEASE_RE = re.compile(r"[\u4e00-\u9fff]{1,4}(?:" + "|".join(_DISEASE_SUFFIXES) + r")")
_DRUG_RE = re.compile(r"[\u4e00-\u9fff]{1,4}(?:" + "|".join(_DRUG_SUFFIXES) + r")")

# 常见功能/动词/标记字（v0 词面启发式）：它们把 CJK 串分成语义片段
# （"该患者高血压使用二甲双胍" → ["高血压", "二甲双胍"]），
# 避免后缀规则把相邻功能字吞进实体表面。
_SEGMENT_BREAKERS = frozenset(
    "的了和与及或在是有为用使服吃患者于对把被该此其个名均需应可等后前中内外上下并且但而就都也还"
    "未无没非不"
) | frozenset(("既往", "曾经", "最近", "近期", "目前", "现在", "当前", "记录", "出现", "服用"))

# 后缀规则的已知非实体结尾（避免 "光疗指征" 之类被当作疾病）。
_NON_ENTITY_SUFFIXES = ("指征", "适应症", "禁忌症")

# 数值单位（数字紧邻）：当 CJK 串紧跟在数字之后且以这些单位开头时，
# 把单位从串首剥离（"2月龄婴儿发热" → 串 "月龄婴儿发热" → 剥离 "月龄" → "婴儿发热"），
# 否则疾病后缀规则会把单位吞进实体表面（曾把 "月龄婴儿发热" 整段当作疾病）。
_NUMERIC_UNIT_PREFIXES = ("个月", "月龄", "周龄", "小时", "分钟", "mg/kg", "月", "周", "岁", "天", "日", "年")


def _semantic_segments(question: str) -> list[tuple[str, int]]:
    """Split CJK runs into segments at filler/marker characters.

    Returns (segment_text, absolute_start) pairs. Latin/numeric text between
    runs is not affected; numbers are extracted by their own rule. A CJK run
    immediately preceded by a digit has a leading numeric unit stripped
    (``2月龄婴儿发热`` → segment ``婴儿发热``).
    """
    segments: list[tuple[str, int]] = []
    for run in _CJK_RUN_RE.finditer(question):
        text = run.group(0)
        base = run.start()
        if base > 0 and question[base - 1].isdigit():
            for unit in _NUMERIC_UNIT_PREFIXES:
                if text.startswith(unit):
                    text = text[len(unit):]
                    base += len(unit)
                    break
        current: list[str] = []
        current_start = base
        for offset, char in enumerate(text):
            if char in _SEGMENT_BREAKERS:
                if current:
                    segments.append(("".join(current), current_start))
                    current = []
                current_start = base + offset + 1
            else:
                if not current:
                    current_start = base + offset
                current.append(char)
        if current:
            segments.append(("".join(current), current_start))
    return segments


_NEGATION_MARKERS = (
    "未记录", "未使用", "未服用", "未出现", "未见", "没有", "否认", "阴性",
    "排除", "无",
)
_TEMPORAL_MARKERS = (
    "既往", "曾经", "最近", "近期", "目前", "现在", "当前", "入院时",
    "出院后", "术后", "术前", "长期", "短期",
)
_EN_NEGATION_RE = re.compile(r"\b(not|no|without|denies|denied|negative|absent|none|unrecorded|never)\b", re.I)
_EN_TEMPORAL_TERMS = (
    "prior", "previous", "previously", "recent", "recently", "current",
    "currently", "history", "initial", "at admission", "admission",
    "postoperative", "preoperative", "ongoing", "now",
)


@dataclass(frozen=True)
class FidelitySlot:
    slot_id: str
    slot_type: str  # disease / drug / concept / numeric / negation / temporal
    surface: str
    start: int
    end: int
    source: str  # lexicon / reviewed_mapping_key / suffix_rule / numeric_rule / marker_rule
    primary: bool
    expected_en: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id, "slot_type": self.slot_type, "surface": self.surface,
            "start": self.start, "end": self.end, "source": self.source,
            "primary": self.primary, "expected_en": list(self.expected_en),
        }


def _number_key(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if number.is_integer() else ("%g" % number)


def _numbers_in(text: str) -> set[str]:
    return {_number_key(value) for value in re.findall(r"\d+(?:\.\d+)?", text)}


def extract_slots(
    question: str,
    *,
    reviewed: Mapping[str, Sequence[str]] | None = None,
    lexicon: FidelityLexicon | None = None,
) -> tuple[FidelitySlot, ...]:
    """Extract auditable slots from the Chinese question.

    Priority order (non-overlapping for entity slots): lexicon hits ->
    reviewed-mapping keys present in the question -> suffix-rule candidates.
    Numeric slots skip numbers inside entity slots. Negation/temporal markers
    are extracted independently. ``reviewed`` maps Chinese surfaces to their
    reviewed English terms (reconstructed from ``plan.expansion_trace``).
    """
    reviewed = {str(k): tuple(str(v) for v in vals) for k, vals in (reviewed or {}).items()}
    candidates: list[tuple[str, str, int, int, str]] = []  # type, surface, start, end, source

    if lexicon is not None:
        for entry, start, end in lexicon.match_zh(question):
            kind = entry.kind if entry.kind in ("disease", "drug") else "concept"
            candidates.append((kind, entry.zh, start, end, "lexicon"))
    lowered_question = question.lower()
    for key in sorted(reviewed, key=lambda value: (-len(value), value)):
        if key and key.lower() in lowered_question:
            start = lowered_question.find(key.lower())
            candidates.append(("concept", question[start:start + len(key)], start, start + len(key), "reviewed_mapping_key"))
    for pattern, kind in ((_DRUG_RE, "drug"), (_DISEASE_RE, "disease")):
        for segment, base in _semantic_segments(question):
            for m in pattern.finditer(segment):
                surface = m.group(0)
                if any(surface.endswith(blocked) for blocked in _NON_ENTITY_SUFFIXES):
                    continue
                start = base + m.start()
                candidates.append((kind, surface, start, start + len(surface), "suffix_rule"))

    # Greedy non-overlap: prefer longer surface, then earlier start, then drug over disease.
    candidates.sort(key=lambda item: (-(item[3] - item[2]), item[2], 0 if item[0] == "drug" else 1))
    entity_slots: list[tuple[str, str, int, int, str]] = []
    for candidate in candidates:
        start, end = candidate[2], candidate[3]
        if any(start < taken_end and end > taken_start for _, _, taken_start, taken_end, _ in entity_slots):
            continue
        entity_slots.append(candidate)
    entity_slots.sort(key=lambda item: (item[2], -(item[3] - item[2])))

    raw_slots: list[tuple[str, str, int, int, str, tuple[str, ...]]] = []
    for kind, surface, start, end, source in entity_slots:
        if source == "lexicon" and lexicon is not None:
            expected = next((entry.en for entry in lexicon.entries if entry.zh == surface), ())
        elif source == "reviewed_mapping_key":
            expected = reviewed.get(surface, ())
        else:
            expected = next((entry.en for entry in (lexicon.entries if lexicon else ()) if entry.zh == surface), ())
        raw_slots.append((kind, surface, start, end, source, tuple(expected)))

    for m in _NUM_RE.finditer(question):
        start, end = m.start(), m.end()
        if any(start < slot_end and end > slot_start for _, _, slot_start, slot_end, _, _ in raw_slots):
            continue
        raw_slots.append(("numeric", question[start:end], start, end, "numeric_rule", ()))

    for marker in _NEGATION_MARKERS:
        start = question.find(marker)
        if start != -1:
            raw_slots.append(("negation", marker, start, start + len(marker), "marker_rule", ()))
    for marker in _TEMPORAL_MARKERS:
        start = question.find(marker)
        if start != -1:
            raw_slots.append(("temporal", marker, start, start + len(marker), "marker_rule", ()))

    raw_slots.sort(key=lambda slot: (slot[2], slot[3]))
    return tuple(
        FidelitySlot(
            slot_id=f"s{position:02d}", slot_type=slot_type, surface=surface,
            start=start, end=end, source=source,
            primary=slot_type in PRIMARY_SLOT_TYPES, expected_en=expected,
        )
        for position, (slot_type, surface, start, end, source, expected) in enumerate(raw_slots, 1)
    )


# ---------------------------------------------------------------------------
# Findings / route audits / report
# ---------------------------------------------------------------------------


def _drop_level(primary: bool) -> str:
    return "L3" if primary else "L2"


def _unexpected_level(kind: str) -> str:
    return "L3" if kind in PRIMARY_SLOT_TYPES else "L2"


@dataclass(frozen=True)
class FidelityFinding:
    route: str
    verdict: str
    level: str
    evidence: str
    slot_id: str | None = None
    slot_type: str | None = None
    surface: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route, "verdict": self.verdict, "level": self.level,
            "evidence": self.evidence, "slot_id": self.slot_id,
            "slot_type": self.slot_type, "surface": self.surface,
        }


@dataclass(frozen=True)
class RouteAudit:
    route: str
    status: str  # ok / findings / no_explicit_conversion / absent / not_configured / no_product
    level: str  # max actionable level (L0 when none)
    findings: tuple[FidelityFinding, ...] = ()
    checked_products: int = 0
    unverifiable_count: int = 0
    notes: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return self.level != "L0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route, "status": self.status, "level": self.level,
            "findings": [finding.to_dict() for finding in self.findings],
            "checked_products": self.checked_products,
            "unverifiable_count": self.unverifiable_count,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class FidelityReport:
    slots: tuple[FidelitySlot, ...]
    routes: tuple[RouteAudit, ...]
    auditor: str = "lexical_fidelity_auditor_v0"

    def route(self, name: str) -> RouteAudit | None:
        return next((audit for audit in self.routes if audit.route == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "auditor": self.auditor,
            "slots": [slot.to_dict() for slot in self.slots],
            "routes": [audit.to_dict() for audit in self.routes],
        }


def _max_level(findings: Sequence[FidelityFinding]) -> str:
    level = "L0"
    for finding in findings:
        if LEVEL_ORDER.index(finding.level) > LEVEL_ORDER.index(level):
            level = finding.level
    return level


def _route(route: str, findings: Sequence[FidelityFinding], checked: int, unverifiable: int, notes: Sequence[str] = ()) -> RouteAudit:
    level = _max_level(findings)
    status = _STATUS_FINDINGS if level != "L0" else _STATUS_OK
    return RouteAudit(
        route=route, status=status, level=level, findings=tuple(findings),
        checked_products=checked, unverifiable_count=unverifiable, notes=tuple(notes),
    )


def _coverage_findings(slots: Sequence[FidelitySlot], products_text: str, route: str) -> tuple[list[FidelityFinding], int]:
    """Shared entity-coverage check over a joined products text."""
    findings: list[FidelityFinding] = []
    unverifiable = 0
    lowered = products_text.lower()
    for slot in slots:
        if slot.slot_type not in ("disease", "drug", "concept"):
            continue
        if not slot.expected_en:
            unverifiable += 1
            findings.append(FidelityFinding(
                route=route, verdict=VERDICT_UNVERIFIABLE, level="L0",
                evidence=f"'{slot.surface}' 没有可用的期望英文（user_terms 未提供该键且词表未命中），未纳入自动判断。",
                slot_id=slot.slot_id, slot_type=slot.slot_type, surface=slot.surface,
            ))
            continue
        if any(_phrase_in(lowered, phrase) for phrase in slot.expected_en):
            findings.append(FidelityFinding(
                route=route, verdict=VERDICT_PRESERVED, level="L0",
                evidence=f"'{slot.surface}' 的期望英文 {slot.expected_en} 出现在转换产物中。",
                slot_id=slot.slot_id, slot_type=slot.slot_type, surface=slot.surface,
            ))
        else:
            findings.append(FidelityFinding(
                route=route, verdict=VERDICT_SUSPECTED_DROP, level=_drop_level(slot.primary),
                evidence=f"转换产物中未找到与 '{slot.surface}' 对应的英文表述（期望：{slot.expected_en}）。",
                slot_id=slot.slot_id, slot_type=slot.slot_type, surface=slot.surface,
            ))
    return findings, unverifiable


def _numeric_findings(slots: Sequence[FidelitySlot], products_text: str, route: str, *, check_added: bool) -> list[FidelityFinding]:
    findings: list[FidelityFinding] = []
    source_numbers = {key: slot.surface for slot in slots if slot.slot_type == "numeric" for key in _numbers_in(slot.surface)}
    product_numbers = _numbers_in(products_text)
    for key, surface in sorted(source_numbers.items()):
        if key not in product_numbers:
            findings.append(FidelityFinding(
                route=route, verdict=VERDICT_NUMERIC_LOSS, level="L2",
                evidence=f"问题数值 '{surface}' 未出现在转换产物中。", slot_type="numeric", surface=surface,
            ))
    if check_added:
        for key in sorted(product_numbers - set(source_numbers)):
            findings.append(FidelityFinding(
                route=route, verdict=VERDICT_NUMERIC_ADDED, level="L2",
                evidence=f"转换产物新增了原问题没有的数值 {key}。", slot_type="numeric", surface=key,
            ))
    return findings


def _fabrication_findings(
    slots: Sequence[FidelitySlot], products_text: str, lexicon: FidelityLexicon | None, route: str, text_label: str
) -> list[FidelityFinding]:
    if lexicon is None:
        return []
    findings: list[FidelityFinding] = []
    slot_zh = {slot.surface for slot in slots}
    for entry, phrase in lexicon.match_english(products_text):
        if entry.zh in slot_zh:
            continue
        if any(_phrase_in(phrase.lower(), expected) for slot in slots for expected in slot.expected_en):
            continue
        findings.append(FidelityFinding(
            route=route, verdict=VERDICT_UNEXPECTED_ENTITY, level=_unexpected_level(entry.kind),
            evidence=f"{text_label}出现问题未提及的实体 '{entry.zh}'（词：{phrase}）；可能是转换串换/伪造。",
            slot_type=entry.kind if entry.kind in PRIMARY_SLOT_TYPES else "concept", surface=entry.zh,
        ))
    return findings


def audit_reviewed_mapping(slots: Sequence[FidelitySlot], products: Sequence[str]) -> RouteAudit:
    if not products:
        return RouteAudit(route="reviewed_mapping", status=_STATUS_ABSENT, level="L0")
    text = " ".join(products)
    findings, unverifiable = _coverage_findings(slots, text, "reviewed_mapping")
    findings.extend(_numeric_findings(slots, text, "reviewed_mapping", check_added=False))
    return _route("reviewed_mapping", findings, len(products), unverifiable)


def audit_translation(slots: Sequence[FidelitySlot], translations: Sequence[str], lexicon: FidelityLexicon | None) -> RouteAudit:
    text = " ".join(translations)
    lowered = text.lower()
    findings, unverifiable = _coverage_findings(slots, text, "translate")
    for slot in slots:
        if slot.slot_type == "negation" and not _EN_NEGATION_RE.search(text):
            findings.append(FidelityFinding(
                route="translate", verdict=VERDICT_NEGATION_LOSS, level="L3",
                evidence=f"问题含否定/缺失表述 '{slot.surface}'，译文中未检出任何英文否定表达。",
                slot_id=slot.slot_id, slot_type="negation", surface=slot.surface,
            ))
        elif slot.slot_type == "temporal" and not any(term in lowered for term in _EN_TEMPORAL_TERMS):
            findings.append(FidelityFinding(
                route="translate", verdict=VERDICT_TEMPORAL_LOSS, level="L2",
                evidence=f"问题含时间表述 '{slot.surface}'，译文中未检出英文时间表达。",
                slot_id=slot.slot_id, slot_type="temporal", surface=slot.surface,
            ))
    findings.extend(_numeric_findings(slots, text, "translate", check_added=True))
    findings.extend(_fabrication_findings(slots, text, lexicon, "translate", "译文"))
    notes = () if lexicon is not None else ("未提供词表：实体串换/伪造（unexpected_entity）无法检测。",)
    return _route("translate", findings, len(translations), unverifiable, notes)


def audit_multilingual(slots: Sequence[FidelitySlot], explicit_terms: Sequence[str], lexicon: FidelityLexicon | None) -> RouteAudit:
    """Audit the explicit terms appended to the multilingual encoder input.

    The Chinese original stays first in the encoder input, so entity *drops*
    are not applicable on this route; the auditable failure is an explicit term
    that introduces an entity no question slot accounts for (contamination).
    """
    if not explicit_terms:
        return RouteAudit(
            route="multilingual_encoding", status=VERDICT_NO_CONVERSION, level="L0",
            notes=("编码器输入为原问题（无显式转换产物）：本路线无查询侧可审计对象；"
                   "这本身是一个可报告的审计性事实。",),
        )
    findings = _fabrication_findings(slots, " ".join(explicit_terms), lexicon, "multilingual_encoding", "编码器输入附加术语")
    notes = () if lexicon is not None else ("未提供词表：实体污染无法检测。",)
    return _route("multilingual_encoding", findings, len(explicit_terms), 0, notes)


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class QueryFidelityAuditor:
    """Deterministic, offline auditor over the query-conversion products.

    The auditor never touches the network or any model. It is invoked by the
    pipeline after the plan is built (and after translation products are
    prepared, when the translate route was requested).
    """

    def __init__(self, lexicon: FidelityLexicon | None = None) -> None:
        self.lexicon = lexicon

    def audit(
        self,
        plan: Any,
        *,
        translations: tuple[str, ...] | None = None,
        translate_requested: bool = False,
        translate_configured: bool = True,
        multilingual_methods: Sequence[str] = (),
    ) -> FidelityReport:
        reviewed: dict[str, tuple[str, ...]] = {}
        for entry in getattr(plan, "expansion_trace", ()):
            if isinstance(entry, Mapping) and entry.get("source") == "reviewed_user_terms":
                reviewed[str(entry.get("concept"))] = tuple(str(item) for item in (entry.get("terms") or ()))
        slots = extract_slots(plan.original_question, reviewed=reviewed, lexicon=self.lexicon)

        products_reviewed = (*plan.english_terms, *plan.mesh_terms)
        routes: list[RouteAudit] = [audit_reviewed_mapping(slots, products_reviewed)]

        if not translate_requested:
            routes.append(RouteAudit(route="translate", status=_STATUS_ABSENT, level="L0"))
        elif not translate_configured:
            routes.append(RouteAudit(route="translate", status=_STATUS_NOT_CONFIGURED, level="L0"))
        elif not translations:
            routes.append(RouteAudit(route="translate", status=_STATUS_NO_PRODUCT, level="L0"))
        else:
            routes.append(audit_translation(slots, translations, self.lexicon))

        if multilingual_methods:
            routes.append(audit_multilingual(slots, products_reviewed, self.lexicon))
        else:
            routes.append(RouteAudit(route="multilingual_encoding", status=_STATUS_ABSENT, level="L0"))

        return FidelityReport(slots=slots, routes=tuple(routes))
