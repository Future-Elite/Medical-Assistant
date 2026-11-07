"""Build a reusable term-mapping library from neonatal dataset labels.

Generic over data location and domain: reads every ``*.json`` under
``--data-dir`` (default ``data``），从结构化标注（``disease_label`` /
``disease_label_en``）抽取概念清单，拆分多病组合、归并大小写变体、抽取括号
缩写做别名，再挂上候选 MeSH（一律标 ``needs_review``，须人工核验）。
输出 ``{"library": name, "generated_from": [...], "rows": [...]}``，可直接作为
``config.term_mapping_library_path``（机制本身对任意领域通用，本工具只是
新生儿实例的构建器）。

边界：
- 只产出**术语映射**（概念/别名/英文/MeSH），不含任何病例内容或患者标识；
- 英文/别名来自数据集标签（``dataset_derived``），不是人工金标准；MeSH 是
  候选（``needs_review``），使用前须人工核验；
- 对话式文件（``conversations`` schema）诊断串常无分隔符拼接，本版只计数跳过，
  不猜测切分；``未见明显异常`` 等阴性标签默认不入映射（非检索概念）。

用法（仓库根目录）：

    PYTHONPATH=. python3 tools/build_term_mapping_library.py \
        --data-dir data \
        --output rag/contracts/examples/term_mapping_library_neonatal.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rag.quality.term_mapping_library import TermMappingLibrary  # noqa: E402

# 候选 MeSH（人工核验前一律 needs_review）：取标准 MeSH 词形，不冒充已核验金标准。
MEZH_SEED = {
    "新生儿肺炎": ["Pneumonia"],
    "新生儿呼吸窘迫综合征": ["Respiratory Distress Syndrome, Newborn"],
    "湿肺": ["Transient Tachypnea of Newborn"],
    "支气管肺发育不良": ["Bronchopulmonary Dysplasia"],
    "气胸": ["Pneumothorax"],
    "胸腔积液": ["Pleural Effusion"],
    "肺不张": ["Atelectasis"],
}
# 数据面支撑的中文别名（结构化标签外的常见写法；其余缩写由英文括号自动抽取）。
ZH_ALIAS_SEED = {
    "湿肺": ["新生儿湿肺"],
    "新生儿呼吸窘迫综合征": ["呼吸窘迫综合征"],
}
NEGATIVE_LABELS = {"未见明显异常"}
_SPLIT_RE = re.compile(r"[，,]")
_ACRONYM_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9\-]{1,})\)")


def _split_pair(zh: str, en: str) -> list[tuple[str, str]]:
    zh_parts = [part.strip() for part in _SPLIT_RE.split(zh or "") if part.strip()]
    en_parts = [part.strip() for part in _SPLIT_RE.split(en or "") if part.strip()]
    if not zh_parts:
        return []
    if len(zh_parts) == len(en_parts):
        return list(zip(zh_parts, en_parts))
    return [(part, "") for part in zh_parts]  # 对不齐时英文留空，只收概念


def collect_from_rows(rows: list[dict], *, source_name: str, include_negative: bool) -> tuple[list[dict], dict]:
    english_surfaces: dict[str, Counter] = defaultdict(Counter)
    alias_surfaces: dict[str, Counter] = defaultdict(Counter)
    label_counts: Counter = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        zh_label = str(row.get("disease_label") or "").strip()
        en_label = str(row.get("disease_label_en") or "").strip()
        if not zh_label:
            continue
        for concept, en in _split_pair(zh_label, en_label):
            if not include_negative and concept in NEGATIVE_LABELS:
                continue
            label_counts[concept] += 1
            if en:
                english_surfaces[concept][en] += 1
                for acronym in _ACRONYM_RE.findall(en):
                    alias_surfaces[concept][acronym] += 1
    stats = {"source": source_name, "rows": len(rows), "label_pairs": sum(label_counts.values())}
    rows_out: list[dict] = []
    for concept in sorted(label_counts):
        en_counter = english_surfaces.get(concept, Counter())
        cleaned: dict[str, Counter] = defaultdict(Counter)  # fold -> 原写法计数
        for surface, count in en_counter.items():
            name = _ACRONYM_RE.sub("", surface).strip(" ,") or surface
            cleaned[name.casefold()][name] += count
        # 大小写变体归并：多数写法胜出，平票优先首字母大写（pneumothorax/Pneumothorax → Pneumothorax）。
        english = [
            sorted(casings.items(), key=lambda kv: (-kv[1], 0 if kv[0][:1].isupper() else 1, kv[0]))[0][0]
            for _, casings in sorted(cleaned.items())
        ]
        aliases: list[str] = []
        for alias, _ in alias_surfaces.get(concept, Counter()).most_common():
            if alias not in aliases:
                aliases.append(alias)
        for alias in ZH_ALIAS_SEED.get(concept, ()):
            if alias not in aliases:
                aliases.append(alias)
        row = {
            "concept": concept,
            "aliases": aliases,
            "english": english,
            "mesh": list(MEZH_SEED.get(concept, ())),
            "metadata": {
                "source": f"dataset_labels:{source_name}",
                "review_status": "dataset_derived",
                "mesh_review_status": "needs_review" if concept in MEZH_SEED else "absent",
                "label_count": label_counts[concept],
            },
        }
        rows_out.append(row)
    return rows_out, stats


def load_lexicon(path: Path) -> tuple[str, list[dict]]:
    """Load a term lexicon (dictionary source): concept → aliases/english/mesh 候选。

    形状：``{"lexicon": name, "entries": [...]}`` 或裸行数组。词表是调用方本地
    提供的术语补充源（如 CMeSH/ICD 中文导出；许可由调用方负责、受限词表不入
    仓库）——产出一律 needs_review，不是人工金标准。
    """
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"lexicon file not found: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"lexicon file unreadable: {path} ({type(exc).__name__})")
    if isinstance(payload, dict):
        name = str(payload.get("lexicon") or Path(path).name)
        entries = payload.get("entries") or payload.get("rows")
    else:
        name = Path(path).name
        entries = payload
    if not isinstance(entries, list) or not entries:
        raise SystemExit("lexicon requires a non-empty entries array")
    out = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise SystemExit(f"lexicon entries[{index}] must be an object")
        concept = str(item.get("concept") or "").strip()
        if not concept:
            raise SystemExit(f"lexicon entries[{index}] requires concept")
        aliases = [str(a).strip() for a in item.get("aliases") or () if str(a).strip()]
        english = [str(a).strip() for a in item.get("english") or () if str(a).strip()]
        mesh = [str(a).strip() for a in item.get("mesh") or () if str(a).strip()]
        if not (aliases or english or mesh):
            raise SystemExit(f"lexicon entries[{index}] needs at least one alias/english/mesh")
        out.append({"concept": concept, "aliases": aliases, "english": english, "mesh": mesh})
    return name, out


def merge_lexicon(rows: list[dict], lexicon_name: str, entries: list[dict]) -> tuple[list[dict], dict]:
    """词典扩展：给既有概念补简称/英文/MeSH 候选，表外概念建新行。

    补进来的一切都标 needs_review（provenance 记在 metadata.lexicon_additions /
    supplemented_by），照旧走人工核验；不覆盖数据集已有的任何词。
    """
    by_concept = {row["concept"]: row for row in rows}
    created, supplemented = 0, 0
    skipped_no_terms: list[str] = []
    for entry in entries:
        concept = entry["concept"]
        aliases = list(entry.get("aliases") or ())
        english = list(entry.get("english") or ())
        mesh = list(entry.get("mesh") or ())
        row = by_concept.get(concept)
        if row is None:
            if not (english or mesh):
                # 纯别名行无法独立成行（库校验要求英文或 MeSH 至少一项）：
                # 它只能给已有概念补词，记下跳过原因，不静默丢弃。
                skipped_no_terms.append(concept)
                continue
            row = {
                "concept": concept,
                "aliases": aliases,
                "english": english,
                "mesh": mesh,
                "metadata": {
                    "source": f"lexicon:{lexicon_name}",
                    "review_status": "needs_review",
                    "mesh_review_status": "needs_review" if mesh else "absent",
                    "label_count": 0,
                    "supplemented_by": [lexicon_name],
                },
            }
            rows.append(row)
            by_concept[concept] = row
            created += 1
            continue
        additions = {"aliases": [], "english": [], "mesh": []}
        for field, values in (("aliases", aliases), ("english", english), ("mesh", mesh)):
            existing = {str(v).casefold() for v in row[field]}
            for value in values:
                if value.casefold() not in existing:
                    row[field].append(value)
                    existing.add(value.casefold())
                    additions[field].append(value)
        if any(additions.values()):
            meta = row["metadata"]
            prior = dict(meta.get("lexicon_additions") or {})
            for field, values in additions.items():
                if values:
                    prior[field] = [*prior.get(field, ()), *values]
            meta["lexicon_additions"] = prior
            meta["supplemented_by"] = sorted({*meta.get("supplemented_by", ()), lexicon_name})
            supplemented += 1
    rows.sort(key=lambda item: item["concept"])
    return rows, {"lexicon": lexicon_name, "rows_created": created, "rows_supplemented": supplemented,
                  "rows_skipped_no_terms": skipped_no_terms}


MEZH_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
MEZH_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _default_mesh_fetch(url: str, params: dict) -> bytes:
    """真实取 NLM MeSH（NCBI E-utilities db=mesh，与 PubMed 同一礼貌规范）。"""
    import time
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(url + "?" + query, headers={"User-Agent": "chinallm_rag/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        payload = response.read(2_000_000)
    time.sleep(0.34)  # NCBI 礼貌限速：无 key 3 req/s
    return payload


def _mesh_search_variants(query: str) -> list[str]:
    """多词查询会被 MeSH 自动切词成 AND 而零命中：逐级放宽再试。"""
    generic = {"neonatal", "newborn", "newborns", "infant", "infants", "of", "the", "in", "and"}
    variants = [query]
    stripped = " ".join(tok for tok in query.split() if tok.casefold() not in generic).strip()
    if stripped and stripped.casefold() != query.casefold():
        variants.append(stripped)
    return variants


def parse_mesh_esummary(payload: bytes) -> list[dict]:
    """解析 MeSH esummary JSON：官方 Descriptor UI / 主题词与入口词 / ScopeNote。"""
    data = json.loads(payload.decode("utf-8"))
    result = data.get("result") or {}
    out = []
    for uid in result.get("uids") or ():
        rec = result.get(str(uid)) or {}
        if str(rec.get("ds_recordtype") or "") != "descriptor":
            continue
        raw_terms = rec.get("ds_meshterms") or []
        if isinstance(raw_terms, str):
            import ast
            try:
                raw_terms = ast.literal_eval(raw_terms)
            except Exception:
                raw_terms = [raw_terms]
        terms = [str(t).strip() for t in raw_terms if str(t).strip()]
        name = terms[0] if terms else ""
        ui = str(rec.get("ds_meshui") or "").strip()
        scope = str(rec.get("ds_scopenote") or "").strip()
        if name:
            out.append({"ui": ui, "name": name, "terms": terms, "scope": scope})
    return out


def _token_set(text: str) -> set[str]:
    return {tok for tok in re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).split() if len(tok) > 2}


def enrich_rows_with_mesh(rows: list[dict], fetch=None, *, email: str = "", max_aliases: int = 5) -> dict:
    """给映射行挂真实 MeSH：官方主题词 + Descriptor UI + 官方入口词（NLM 词库实取）。

    逐行 provenance 写入 metadata：``mesh_review_status`` ∈
    mesh_grounded（官方名/入口词与查询词精确一致）/ mesh_top_match（取词重合最高的
    候选，备选记 metadata.mesh_candidates）/ not_found / no_english / fetch_error。
    真实词库锚定不等于人工金标准，临床核验仍须医生过目。
    """
    fetch = fetch or _default_mesh_fetch
    statuses: Counter = Counter()
    for row in rows:
        english = [str(v).strip() for v in row.get("english") or () if str(v).strip()]
        meta = row["metadata"]
        prior_mesh = [str(v) for v in row.get("mesh") or ()]
        if not english:
            meta["mesh_review_status"] = "no_english"
            statuses["no_english"] += 1
            continue
        query = english[0]
        variants = _mesh_search_variants(query)

        def esearch_ids(term: str, retmax: int) -> list[str]:
            params = {"db": "mesh", "term": term, "retmode": "json", "retmax": str(retmax), "tool": "chinallm_rag"}
            if email:
                params["email"] = email
            search = json.loads(fetch(MEZH_ESEARCH, params).decode("utf-8"))
            return [str(v).strip() for v in search.get("esearchresult", {}).get("idlist", []) if str(v).strip()]

        try:
            # 主路径："[标签]"[Mesh] = 主题词精确命中（esearch 全文相关性排序会被亚型词
            # 挤掉主题词本身，例如 "Pneumonia" 前 20 里没有 Pneumonia）。命中即 descriptor 级。
            tag_ids: list[str] = []
            for variant in variants:
                for uid in esearch_ids(f'"{variant}"[Mesh]', 5):
                    if uid not in tag_ids:
                        tag_ids.append(uid)
            ids = tag_ids
            tag_hit = bool(tag_ids)
            if not tag_hit:
                # 兜底：合并放宽变体的普通检索候选（retmax 20），靠词重合打分选词。
                for variant in variants:
                    for uid in esearch_ids(variant, 20):
                        if uid not in ids:
                            ids.append(uid)
                    if len(ids) >= 20:
                        break
                ids = ids[:20]
        except Exception:
            meta["mesh_review_status"] = "fetch_error"
            statuses["fetch_error"] += 1
            continue
        if not ids:
            meta["mesh_review_status"] = "not_found"
            statuses["not_found"] += 1
            continue
        eparams = {"db": "mesh", "id": ",".join(ids), "retmode": "json", "tool": "chinallm_rag"}
        if email:
            eparams["email"] = email
        try:
            records = parse_mesh_esummary(fetch(MEZH_ESUMMARY, eparams))
        except Exception:
            meta["mesh_review_status"] = "fetch_error"
            statuses["fetch_error"] += 1
            continue
        if not records:
            meta["mesh_review_status"] = "not_found"
            statuses["not_found"] += 1
            continue
        # 精确匹配做归一化（去独立 the、压空格）："…of the newborn" 对上官方词 "…of newborn"。
        def norm(text: str) -> str:
            return " ".join(tok for tok in str(text).casefold().split() if tok != "the")
        variant_norms = {norm(v) for v in variants}
        english_fold = {str(v).casefold() for v in english}

        def tier(rec):
            names = {norm(rec["name"]), *(norm(t) for t in rec["terms"])}
            if names & variant_norms:
                return (2, 0.0)
            qt = _token_set(query)
            overlap = max((len(qt & _token_set(n)) / max(1, len(qt | _token_set(n))) for n in names), default=0.0)
            return (1, overlap)

        ranked = sorted(records, key=lambda r: (tier(r)[0], tier(r)[1]), reverse=True)
        chosen = ranked[0]
        grounded = tag_hit or tier(chosen)[0] == 2
        meta["mesh_ui"] = chosen["ui"]
        meta["mesh_source"] = "ncbi_mesh_esummary"
        meta["mesh_review_status"] = "mesh_grounded" if grounded else "mesh_top_match"
        if not grounded and len(ranked) > 1:
            meta["mesh_candidates"] = [r["name"] for r in ranked]
        if chosen["scope"]:
            meta["mesh_scope_note"] = chosen["scope"][:240]
        if prior_mesh:
            meta["mesh_before"] = prior_mesh
        row["mesh"] = [chosen["name"]]
        existing = {str(v).casefold() for v in row.get("aliases") or ()}
        added: list[str] = []
        for term in chosen["terms"]:
            key = term.casefold()
            if key in english_fold or key in existing:
                continue
            if len(added) >= max_aliases:
                break
            row["aliases"].append(term)
            existing.add(key)
            added.append(term)
        if added:
            meta["mesh_entry_terms"] = added
        statuses[meta["mesh_review_status"]] += 1
    rows.sort(key=lambda item: item["concept"])
    return {"source": "ncbi_mesh_esummary", "statuses": dict(statuses)}


def build(data_dir: Path, *, include_negative: bool = False, with_mesh: bool = True,
          lexicon_path: Path | None = None, mesh_enrich: bool = False,
          mesh_fetch=None, mesh_email: str = "") -> dict:
    all_rows: list[dict] = []
    generated_from: list[dict] = []
    skipped: list[str] = []
    for path in sorted(data_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{path.name}: unreadable ({type(exc).__name__})")
            continue
        if not isinstance(payload, list) or not payload:
            skipped.append(f"{path.name}: not a non-empty array")
            continue
        if "disease_label" not in payload[0]:
            skipped.append(f"{path.name}: conversations schema（诊断串无分隔，本版跳过）")
            continue
        rows, stats = collect_from_rows(payload, source_name=path.name, include_negative=include_negative)
        all_rows.extend(rows)
        generated_from.append(stats)
    merged: dict[str, dict] = {}
    for row in all_rows:
        concept = row["concept"]
        if concept not in merged:
            merged[concept] = row
            continue
        target = merged[concept]
        target["metadata"]["label_count"] += row["metadata"]["label_count"]
        for field in ("aliases", "english", "mesh"):
            for value in row[field]:
                if value not in target[field]:
                    target[field].append(value)
    out_rows = [merged[key] for key in sorted(merged)]
    lexicon_stats = None
    if lexicon_path is not None:
        lexicon_name, entries = load_lexicon(lexicon_path)
        out_rows, lexicon_stats = merge_lexicon(out_rows, lexicon_name, entries)
    mesh_stats = None
    if mesh_enrich and with_mesh:
        mesh_stats = enrich_rows_with_mesh(out_rows, mesh_fetch, email=mesh_email)
    if not with_mesh:
        for row in out_rows:
            row["mesh"] = []
            row["metadata"]["mesh_review_status"] = "omitted"
    if not out_rows:
        raise SystemExit(f"no mapping rows extracted from {data_dir}")
    result = {
        "library": "neonatal_term_mapping_v1",
        "generated_from": generated_from,
        "skipped_files": skipped,
        "lexicon": lexicon_stats,
        "mesh_enrichment": mesh_stats,
        "disclaimer": (
            "dataset_derived 英文/别名来自数据集标签，词表补充与 mesh 候选一律 needs_review，"
            "均不是人工金标准，使用前须人工核验；不含任何病例内容。"
        ),
        "rows": out_rows,
    }
    TermMappingLibrary.from_rows(result["rows"], source=result["library"])  # 出厂前自检
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="build a term-mapping library from dataset labels + optional term lexicon")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument(
        "--output", type=Path,
        default=REPO_ROOT / "rag" / "contracts" / "examples" / "term_mapping_library_neonatal.json",
    )
    parser.add_argument("--lexicon", type=Path, default=None,
                        help="术语词表文件（词典扩展源）：给概念补简称/英文/MeSH 候选并收表外概念；产出一律 needs_review")
    parser.add_argument("--mesh-enrich", action="store_true",
                        help="接真实术语源：实时查 NLM MeSH（NCBI E-utilities），回收官方主题词/Descriptor UI/入口词（联网，逐行留 provenance）")
    parser.add_argument("--email", default="", help="NCBI 礼貌访问邮箱（--mesh-enrich 时建议提供）")
    parser.add_argument("--include-negative", action="store_true", help="把“未见明显异常”等阴性标签也收进映射（默认排除）")
    parser.add_argument("--no-mesh", action="store_true", help="不输出候选 MeSH")
    args = parser.parse_args()
    result = build(args.data_dir, include_negative=args.include_negative, with_mesh=not args.no_mesh,
                   lexicon_path=args.lexicon, mesh_enrich=args.mesh_enrich, mesh_email=args.email)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    concepts = [row["concept"] for row in result["rows"]]
    print(f"library: {result['library']}  rows: {len(result['rows'])}")
    print("concepts:", "、".join(concepts))
    print(f"generated_from: {result['generated_from']}")
    if result.get("lexicon"):
        print(f"lexicon: {result['lexicon']}")
    if result.get("mesh_enrichment"):
        print(f"mesh_enrichment: {result['mesh_enrichment']}")
    if result["skipped_files"]:
        print(f"skipped: {result['skipped_files']}")
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
