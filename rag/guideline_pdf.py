"""Guideline full text from caller-supplied PDFs, parsed at request time.

The workbench ships three neonatal guidelines the demo needs: the European RDS
2025 update and two Chinese journal articles.  The PDFs are kept **outside**
the repository in a caller-owned directory and parsed fresh on every
retrieval:

- nothing is written to disk by this module;
- nothing is cached between requests;
- the manifest stores bibliographic / provenance fields only.

Per the project's current guideline-source policy (``AGENTS.md`` §4) every
registered guideline is searchable as full text; ``rights_basis`` is still
recorded for provenance and audit, but it no longer gates retrieval.

Expected caller-side layout (``config.guideline_pdf_dir``, default
``~/.chinallm/guideline_pdfs``)::

    <dir>/guideline_pdfs.json      # manifest, see ``load_manifest``
    <dir>/*.pdf                    # the caller's copies

Each manifest entry becomes page-located chunks with
``content_mode="licensed_full_text"`` and a ``locator`` (``<section> · p.<n>``)
so a reviewer can re-open the PDF and check the sentence.  Two-column journal
pages are reflowed into reading order; see :func:`extract_pdf_text`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .connectors import KnowledgeConnector
from .guideline_fulltext import _chunk_text, _query_terms
from .models import QueryPlan, SearchResult, SourceDocument

MANIFEST_FILE = "guideline_pdfs.json"
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_MAX_CHUNKS = 30
#: Guard against decompression bombs / absurd uploads.
MAX_PDF_BYTES = 80_000_000

#: Lines that repeat on every page and must not pollute the chunk text.
_BOILERPLATE = re.compile(
    r"(downloaded from|karger\.com|doi:\s*10\.|chin j contemp pediatr|"
    r"第\s*\d+\s*卷\s*第\s*\d+\s*期|中国当代儿科杂志|^\s*·\s*\d+\s*·\s*$|"
    r"^\s*\d{4}\s*年\s*\d+\s*月\s*$|european rds guidelines)",
    re.IGNORECASE,
)
_PAGE_NO = re.compile(r"^\s*(?:[·•]\s*)?\d{1,4}(?:\s*[·•])?\s*$")


class GuidelinePdfError(RuntimeError):
    """Raised when a caller-supplied PDF cannot be parsed."""


def load_manifest(directory: str | Path) -> dict[str, Any]:
    """Load and validate ``guideline_pdfs.json``; raises ``ValueError``.

    ``full_text_allowed`` is optional and defaults to ``true``: per the current
    policy every guideline is searchable as full text.  ``rights_basis`` is
    kept for provenance and audit.
    """
    path = Path(directory).expanduser() / MANIFEST_FILE
    if not path.is_file():
        raise ValueError(f"guideline PDF manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("guidelines")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest requires a non-empty 'guidelines' array")
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"guidelines[{index}] must be an object")
        document_id = str(entry.get("document_id") or "").strip()
        file_name = str(entry.get("file") or "").strip()
        if not document_id or document_id in seen:
            raise ValueError(f"guidelines[{index}] needs a unique document_id")
        seen.add(document_id)
        if not file_name:
            raise ValueError(f"guidelines[{index}] requires 'file'")
        if "full_text_allowed" in entry and not isinstance(entry["full_text_allowed"], bool):
            raise ValueError(f"guidelines[{index}].full_text_allowed must be a boolean")
    return payload


def _entry_full_text_allowed(entry: Mapping[str, Any]) -> bool:
    value = entry.get("full_text_allowed", True)
    return bool(value)


# ---------------------------------------------------------------------------
# PDF text extraction (reading order matters for citations)
# ---------------------------------------------------------------------------


def extract_pdf_text(path: Path) -> str:
    """Return the PDF text in **reading order**; ``pypdf`` as fallback.

    Plain ``pdftotext`` interleaves the left and right column of two-column
    journal pages, which shreds sentences and makes citations useless.  We
    therefore parse ``pdftotext -bbox-layout`` word boxes, detect the gutter
    and emit the left column before the right one.  Pages stay separated by
    form feeds so a locator can still name a page number.

    Raises :class:`GuidelinePdfError` with the exact stage instead of silently
    returning an empty document.
    """
    if not path.is_file():
        raise GuidelinePdfError(f"PDF not found: {path}")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise GuidelinePdfError(f"PDF is larger than {MAX_PDF_BYTES} bytes")
    if shutil.which("pdftotext"):
        try:
            completed = subprocess.run(
                ["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(path), "-"],
                capture_output=True, check=True, timeout=90,
            )
            pages = _pages_from_bbox(completed.stdout)
            if pages and any(page.strip() for page in pages):
                return "\f".join(pages)
        except (subprocess.SubprocessError, OSError) as exc:
            raise GuidelinePdfError(f"pdftotext failed: {type(exc).__name__}") from exc
    try:  # optional dependency; core package stays stdlib-only
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as exc:
        raise GuidelinePdfError(
            "no PDF extractor available: install poppler-utils (pdftotext) or pypdf"
        ) from exc
    try:
        reader = PdfReader(str(path))
        text = "\f".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise GuidelinePdfError(f"pypdf failed: {type(exc).__name__}") from exc
    if not text.strip():
        raise GuidelinePdfError("PDF contains no extractable text (scanned image?)")
    return text


@dataclass(frozen=True)
class _Word:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


#: The XML comes from our own ``pdftotext`` run, but we still refuse DTD
#: entity constructs before handing bytes to ElementTree (no entity expansion).
_XML_ENTITY_DECL = re.compile(rb"<!ENTITY\b", re.IGNORECASE)
_XML_DOCTYPE = re.compile(rb"<!DOCTYPE\b(?:[^>\[]|\[.*?\])*?>", re.DOTALL | re.IGNORECASE)
MAX_XML_BYTES = 60_000_000


def _safe_xml_root(raw: bytes):
    import xml.etree.ElementTree as ET

    if len(raw) > MAX_XML_BYTES:
        raise GuidelinePdfError("bbox XML exceeds size limit")
    if _XML_ENTITY_DECL.search(raw):
        raise GuidelinePdfError("bbox XML contains an entity declaration; refused")
    cleaned = _XML_DOCTYPE.sub(b"", raw, count=1)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError as exc:
        raise GuidelinePdfError(f"bbox XML parse failed: {exc}") from exc


def _pages_from_bbox(raw: bytes) -> list[str]:
    """Parse ``pdftotext -bbox-layout`` XHTML and rebuild reading order."""
    root = _safe_xml_root(raw)
    pages: list[str] = []
    for page in root.iter():
        if _local(page.tag) != "page":
            continue
        width = float(page.attrib.get("width") or 0) or 595.0
        words: list[_Word] = []
        for word in page.iter():
            if _local(word.tag) != "word":
                continue
            text = "".join(word.itertext()).strip()
            if not text:
                continue
            words.append(_Word(
                text=text,
                x0=float(word.attrib.get("xMin") or 0.0),
                x1=float(word.attrib.get("xMax") or 0.0),
                y0=float(word.attrib.get("yMin") or 0.0),
                y1=float(word.attrib.get("yMax") or 0.0),
            ))
        pages.append(_page_text(words, width))
    return pages


def _detect_gutter(words: Sequence["_Word"], width: float) -> float | None:
    """Return the x of the column gutter, or ``None`` for single-column pages."""
    if len(words) < 40:
        return None
    best: tuple[float, float] | None = None  # (score, split)
    for split in range(int(width * 0.36), int(width * 0.64), 3):
        left = sum(1 for word in words if word.x1 <= split)
        right = sum(1 for word in words if word.x0 >= split)
        crossing = len(words) - left - right
        if left < len(words) * 0.28 or right < len(words) * 0.28:
            continue
        score = min(left, right) - crossing * 4.0
        if best is None or score > best[0]:
            best = (score, float(split))
    return best[1] if best else None


def _page_text(words: Sequence["_Word"], width: float) -> str:
    if not words:
        return ""
    gutter = _detect_gutter(words, width)
    if gutter is None:
        return _block_text(words, width)
    left = [word for word in words if word.x1 <= gutter]
    right = [word for word in words if word.x0 >= gutter]
    middle = [word for word in words if word.x0 < gutter < word.x1]
    parts = [_block_text(left, gutter)]
    if middle:  # full-width title lines belong between the columns
        parts.append(_block_text(middle, width))
    parts.append(_block_text(right, width))
    return "\n\n".join(part for part in parts if part)


def _block_text(words: Sequence["_Word"], width: float) -> str:
    """Group words into lines (by baseline), then lines into paragraphs."""
    if not words:
        return ""
    ordered = sorted(words, key=lambda word: (word.y0, word.x0))
    lines: list[list["_Word"]] = []
    for word in ordered:
        if lines and abs(word.y0 - lines[-1][0].y0) <= 4.0:
            lines[-1].append(word)
        else:
            lines.append([word])
    rendered: list[str] = []
    for line in lines:
        line.sort(key=lambda word: word.x0)
        rendered.append(_join_words(line))
    right_edge = max((word.x1 for word in words), default=width)
    paragraphs: list[str] = []
    buffer: list[str] = []
    for index, line in enumerate(rendered):
        buffer.append(line)
        line_words = lines[index]
        # A line that stops well short of the column edge ends a paragraph.
        short = (right_edge - max(word.x1 for word in line_words)) > 18
        if short or not line.strip():
            paragraphs.append(" ".join(buffer).strip())
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer).strip())
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


_CJK_CHAR = re.compile(r"[\u4e00-\u9fff（）［］，。；：、]")


def _join_words(line: Sequence["_Word"]) -> str:
    parts: list[str] = []
    previous: "_Word | None" = None
    for word in line:
        if previous is not None:
            gap = word.x0 - previous.x1
            cjk = bool(_CJK_CHAR.search(previous.text[-1:]) or _CJK_CHAR.search(word.text[:1]))
            if gap > 1.2 and not cjk:
                parts.append(" ")
        parts.append(word.text)
        previous = word
    return "".join(parts)


def split_pages(text: str) -> list[str]:
    """Pages are separated by form feeds produced by the extractor."""
    pages = list(text.split("\f"))
    while pages and not pages[-1].strip():
        pages.pop()
    return pages


def clean_page(page: str) -> str:
    """Drop running heads, footers and page numbers; keep paragraph breaks."""
    kept: list[str] = []
    for line in page.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _PAGE_NO.match(stripped):
            continue
        if _BOILERPLATE.search(stripped):
            continue
        kept.append(line.rstrip())
    text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_SENTENCE_END = (".", "。", "；", ";", "：", ":", "，", ",", "）", ")", "”")
_CJK_RATIO = re.compile(r"[\u4e00-\u9fff]")
#: Running heads / affiliation fragments that survive reflow but are not sections.
_NON_SECTION = re.compile(
    r"(university|hospital|department|institute|school of|college|"
    r"medical association|medical doctor|editorial board|children.?s|"
    r"email|@|correspondence|affiliation|conflict of interest|"
    r"版权所有|copyright|开放获取|open access|收稿日期|通信作者|编辑委员会)",
    re.IGNORECASE,
)
#: Where the guideline body ends and the reference list begins.
_REFERENCES_HEADING = re.compile(
    r"^\s*[\[［(（]?\s*(?:参考文献|references|bibliography|literature cited|参\s*考\s*文\s*献)"
    r"\s*[\]］)）]?\s*$",
    re.IGNORECASE,
)
_CITATION_MARKER = re.compile(r"\[\s*\d{1,3}(?:\s*[-–,]\s*\d{1,3})*\s*\]")


def _looks_like_heading(block: str) -> bool:
    """A heading is a short, single-line block without sentence punctuation.

    Numbers and citations are excluded: two-column reflow produces fragments
    such as ``"mg/kg"`` or ``"(IISLAFE), University and Polytechnic"`` that
    must not be promoted to section names (they would pollute the locator).
    """
    if "\n" in block:
        return False
    text = block.strip()
    if not text or len(text) > 42:
        return False
    if text.endswith(_SENTENCE_END):
        return False
    if any(mark in text for mark in (".", "。", "；", ";", "：", ":", "，", ",")):
        return False
    if text[0].isdigit() or text[0] in "([":
        return False
    if _NON_SECTION.search(text):
        return False
    cjk = bool(_CJK_RATIO.search(text))
    if cjk:
        return len(text) <= 20
    return len(text.split()) <= 6


def _is_reference_block(block: str) -> bool:
    """Reference-list rows are citation markers plus bibliographic glue."""
    markers = _CITATION_MARKER.findall(block)
    if len(markers) < 2:
        return False
    marker_chars = sum(len(marker) for marker in markers)
    return marker_chars / max(1, len(block)) > 0.08


def page_chunks(page: str, *, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split one page into ``(heading, chunk_text)`` pairs."""
    heading = ""
    paragraphs: list[str] = []
    buffer: list[str] = []
    for block in re.split(r"\n\s*\n", page):
        block = block.strip()
        if not block:
            continue
        if _looks_like_heading(block):
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            heading = block
            continue
        buffer.append(" ".join(block.split()))
    if buffer:
        paragraphs.append(" ".join(buffer))
    chunks: list[tuple[str, str]] = []
    for paragraph in paragraphs:
        for piece in _chunk_text(paragraph, chunk_chars):
            chunks.append((heading, piece))
    return chunks


def _short_title(entry: Mapping[str, Any]) -> str:
    title = str(entry.get("short_title") or entry.get("title") or "指南").strip()
    return title[:80]


def _entry_topic_text(entry: Mapping[str, Any]) -> str:
    """Only provenance fields — never the body — decide guideline topic."""
    return " ".join(str(entry.get(key) or "") for key in ("title", "short_title", "population", "keywords"))


def _topic_text(plan: QueryPlan) -> str:
    """Question + English/MeSH terms; CJK bigrams come from the shared tokenizer."""
    return " ".join((plan.original_question or "", *plan.english_terms, *plan.mesh_terms))


def _select_on_topic(
    entries: Sequence[Mapping[str, Any]], plan: QueryPlan
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Split entries into (on-topic, skipped) using the manifest's ``topic_keys``.

    Neonatal guidelines share the words "新生儿/newborn", so a plain substring
    test cannot tell an RDS guideline from a pneumonia guideline.  Each manifest
    entry therefore declares ``topic_keys`` (English phrases and Chinese disease
    names, e.g. ``"respiratory distress syndrome"`` / ``"呼吸窘迫综合征"``); a
    guideline is on topic when the caller's question or reviewed terms contain
    one of its keys.

    Fallbacks, in order:

    1. no entry declares keys -> nothing is filtered (ranking decides);
    2. the question matches no key at all -> every entry is returned but a
       warning says the topic could not be determined.
    """
    if not entries:
        return [], []
    keyed = [entry for entry in entries if entry.get("topic_keys")]
    if not keyed:
        return list(entries), []
    haystack = _topic_text(plan).lower()
    matched: list[Mapping[str, Any]] = []
    unmatched: list[Mapping[str, Any]] = []
    for entry in entries:
        keys = [str(key).strip().lower() for key in (entry.get("topic_keys") or []) if str(key).strip()]
        if any(key in haystack for key in keys):
            matched.append(entry)
        else:
            unmatched.append(entry)
    if not matched:
        # No declared key matched.  A clinical question that clearly names a
        # disease gets nothing (better than an unrelated guideline); a generic
        # question keeps every guideline and lets ranking decide.
        return ([], list(entries)) if _names_specific_disease(plan) else (list(entries), [])
    return matched, unmatched


#: Disease-ish signals: Chinese disease suffixes plus English disease words.
_DISEASE_CJK = re.compile(
    r"[\u4e00-\u9fff]{1,8}(?:综合征|肺炎|黄疸|低血糖|高血糖|呼吸增快|发育不良|"
    r"肺动脉高压|感染|败血症|脑病|出血|气胸|贫血|发热|惊厥|窒息)"
)
_DISEASE_EN = re.compile(
    r"\b(?:syndrome|pneumonia|jaundice|hypoglyc\w*|sepsis|infection|"
    r"haemorrhage|hemorrhage|pneumothorax|anaemia|anemia|apnoea|apnea|"
    r"encephalopathy|dysplasia|hypertension)\b",
    re.IGNORECASE,
)


def _names_specific_disease(plan: QueryPlan) -> bool:
    text = _topic_text(plan)
    return bool(_DISEASE_CJK.search(text) or _DISEASE_EN.search(text))


def build_documents(
    entry: Mapping[str, Any],
    directory: Path,
    *,
    query_terms: Sequence[str] = (),
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    text_extractor: Callable[[Path], str] = extract_pdf_text,
) -> list[SourceDocument]:
    """Extract one manifest entry into ranked, page-located chunks.

    The reference list is dropped: citation rows score on author names and add
    noise without ever supporting a clinical claim.
    """
    pdf_path = directory / str(entry["file"])
    text = text_extractor(pdf_path)
    pages = split_pages(text)
    candidates: list[tuple[int, int, str, str]] = []
    in_references = False
    for page_number, raw_page in enumerate(pages, start=1):
        page = clean_page(raw_page)
        if not page:
            continue
        for offset, (heading, chunk) in enumerate(page_chunks(page, chunk_chars=chunk_chars)):
            if _REFERENCES_HEADING.match(heading or ""):
                in_references = True
                continue
            if in_references or _is_reference_block(chunk):
                continue
            haystack = f"{heading}\n{chunk}".lower()
            score = sum(1 for term in query_terms if str(term).strip().lower() in haystack)
            candidates.append((-score, page_number * 100 + offset, heading, chunk))
    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = candidates[:max_chunks]

    documents: list[SourceDocument] = []
    for rank, (_, order, heading, chunk) in enumerate(selected, start=1):
        page_number = order // 100
        # A chunk may sit under a running head rather than a real section name;
        # the locator then falls back to the chunk's own opening words.
        section = heading or f"正文：{chunk[:24].strip()}…"
        documents.append(SourceDocument(
            source_id=str(entry.get("source_id") or "guideline-pdf-local"),
            document_id=f"{entry['document_id']}:p{page_number:03d}-c{rank:02d}",
            kind="guideline",
            title=_short_title(entry),
            text=f"{section}\n{chunk}",
            url=entry.get("url"),
            published_at=entry.get("published_at"),
            updated_at=entry.get("updated_at"),
            locator=f"{section} · p.{page_number}",
            # DOI/PMID stay in metadata only: putting them on every chunk would
            # make the global dedup key (DOI > PMID > title) collapse all chunks
            # of one guideline into a single row.
            doi=None,
            pmid=None,
            authority=entry.get("publisher"),
            version_id=entry.get("version_id"),
            population=entry.get("population"),
            status=entry.get("status"),
            metadata={
                "synthetic": False,
                "content_mode": "licensed_full_text",
                "evidence_type": entry.get("evidence_type") or "clinical_practice_guideline",
                "full_text_allowed": True,
                "rights_basis": entry.get("rights_basis") or "caller_provided_pdf",
                "license": entry.get("license") or "unknown",
                "license_verified": bool(entry.get("license")),
                "live_source": False,
                "source": "caller_pdf",
                "language": entry.get("language") or "en",
                "pmcid": entry.get("pmcid"),
                "doi": entry.get("doi"),
                "pmid": entry.get("pmid"),
                "journal": entry.get("publisher"),
                "page": page_number,
                "section": heading,
                "chunk_rank": rank,
                "source_file": entry["file"],
                "oriented": entry.get("oriented") or "",
            },
        ))
    return documents


@dataclass
class LocalGuidelinePdfConnector(KnowledgeConnector):
    """Serve caller-supplied guideline PDFs as section chunks (read-only)."""

    source_id: str = "guideline-pdf-local"
    kinds: tuple[str, ...] = ("guideline",)
    directory: Path = field(default_factory=lambda: Path("~/.chinallm/guideline_pdfs").expanduser())
    manifest: Mapping[str, Any] | None = None
    #: Upper bound for one guideline; keeps a single PDF from filling top_k.
    max_chunks_per_guideline: int = 8
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    text_extractor: Callable[[Path], str] = extract_pdf_text
    last_query: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).expanduser()
        if self.manifest is None:
            self.manifest = load_manifest(self.directory)
        if self.max_chunks_per_guideline <= 0 or self.chunk_chars <= 0:
            raise ValueError("guideline PDF limits must be positive")

    @property
    def entry_count(self) -> int:
        return len((self.manifest or {}).get("guidelines") or [])

    def describe_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "document_id": entry["document_id"],
                "file": entry["file"],
                "title": entry.get("title"),
                "doi": entry.get("doi"),
                "pmid": entry.get("pmid"),
                "license": entry.get("license"),
                "rights_basis": entry.get("rights_basis"),
                "evidence_type": entry.get("evidence_type"),
                "full_text_allowed": _entry_full_text_allowed(entry),
            }
            for entry in (self.manifest or {}).get("guidelines") or []
        ]

    def search(self, plan: QueryPlan, *, limit: int) -> SearchResult:
        allowed = set(plan.filters.get("source_kinds") or ())
        if allowed and "guideline" not in allowed:
            return SearchResult(self.source_id, ())
        requested_version = str(plan.filters.get("guideline_version") or "").strip() or None
        terms = _query_terms(plan)
        self.last_query = " ".join(terms[:8])
        entries = list((self.manifest or {}).get("guidelines") or [])
        # Retrieval must not answer an RDS question with the pneumonia guideline
        # just because both are neonatal; the drop is always reported.
        on_topic, skipped = _select_on_topic(entries, plan)
        warnings: list[str] = [
            f"指南《{entry.get('short_title') or entry.get('title')}》与本问题主题不符，已跳过。"
            for entry in skipped
        ]
        documents: list[SourceDocument] = []
        for entry in on_topic:
            if not _entry_full_text_allowed(entry):
                warnings.append(
                    f"指南《{entry.get('short_title') or entry.get('title')}》标记 full_text_allowed=false，按调用方配置跳过。"
                )
                continue
            if requested_version and str(entry.get("version_id") or "") != requested_version:
                continue
            try:
                produced = build_documents(
                    entry, self.directory, query_terms=terms,
                    max_chunks=self.max_chunks_per_guideline,
                    chunk_chars=self.chunk_chars, text_extractor=self.text_extractor,
                )
            except GuidelinePdfError as exc:
                self.last_error = str(exc)
                warnings.append(f"指南《{entry.get('short_title') or entry.get('title')}》解析失败：{exc}")
                continue
            documents.extend(produced)
        if not documents:
            if skipped and not on_topic:
                warnings.append(
                    "已登记的指南 PDF 中没有与本问题主题相符的指南（问题涉及的主题未登记）；未返回无关指南。"
                )
            else:
                warnings.append("未从指南 PDF 目录解析出可用正文；检查 manifest 与文件。")
            return SearchResult(self.source_id, (), tuple(dict.fromkeys(warnings)))
        return SearchResult(self.source_id, tuple(documents[:limit]), tuple(dict.fromkeys(warnings)))
