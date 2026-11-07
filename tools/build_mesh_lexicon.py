"""Build a local MeSH lexicon snapshot from NLM's official linked-data service.

This is a real data build, not a fixture generator:

* descriptors are selected by MeSH tree prefix (default ``C16``: congenital,
  hereditary, and neonatal diseases) and by the MeSH labels already present in
  the project's reviewed term-mapping library;
* every descriptor label, entry term, term UI and tree number comes from the
  NLM MeSH 2026 SPARQL endpoint (``https://id.nlm.nih.gov/mesh/sparql``);
* labels that do not resolve exactly are looked up through the NLM lookup API
  and marked ``exact`` / ``top_match`` in the output provenance.

The output is a plain JSON file so the linker can run offline.  MeSH is
maintained by the U.S. National Library of Medicine; keep the ``source`` and
``retrieved_at`` fields with the snapshot when redistributing it.

Usage (from the repository root)::

    # neonatal demo snapshot (committed, 771 descriptors)
    python3 tools/build_mesh_lexicon.py
    python3 tools/build_mesh_lexicon.py --tree C16 --extra-label "Infant, Newborn"

    # full MeSH 2026 (31k descriptors / 267k terms; written outside the repo)
    python3 tools/build_mesh_lexicon.py --full --full-source xml
    python3 tools/build_mesh_lexicon.py --full --full-source sparql --page-size 500

The full output defaults to ``data/mesh/mesh_lexicon_2026_full.json``
and is not committed: it is a 47 MB generated asset.  The XML source is the
official NLM ``desc2026.xml`` (313 MB); the SPARQL source paginates the same
official linked-data endpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "rag" / "demo" / "mesh_lexicon_neonatal_2026.json"
DEFAULT_MAPPING_LIBRARY = REPO_ROOT / "rag" / "contracts" / "examples" / "term_mapping_library_neonatal.json"

SPARQL_ENDPOINT = "https://id.nlm.nih.gov/mesh/sparql"
LOOKUP_ENDPOINT = "https://id.nlm.nih.gov/mesh/lookup/descriptor"
MESH_YEAR = "2026"
MESH_XML_URL = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml"
DEFAULT_MESH_XML = REPO_ROOT / "data" / "mesh" / "desc2026.xml"
DEFAULT_FULL_OUTPUT = REPO_ROOT / "data" / "mesh" / "mesh_lexicon_2026_full.json"
DEFAULT_TREES = ("C16",)
DEFAULT_EXTRA_LABELS = (
    "Infant, Newborn",
    "Infant, Premature",
    "Gestational Age",
    "Phototherapy",
    "Pulmonary Surfactants",
    "Hyperbilirubinemia, Neonatal",
)

_SPARQL_PREFIX = """\
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
"""


def _http_json(url: str, *, timeout: float = 60.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ChinaLLM-mesh-lexicon/1.0 (research prototype)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _sparql(query: str, *, timeout: float = 90.0) -> list[Mapping[str, Any]]:
    params = urllib.parse.urlencode({"query": query, "format": "JSON", "inference": "false"})
    payload = _http_json(f"{SPARQL_ENDPOINT}?{params}", timeout=timeout)
    return list(payload.get("results", {}).get("bindings", ()))


def _tree_filter(trees: Sequence[str]) -> str:
    prefixes = " || ".join(
        f'STRSTARTS(STR(?tree), "http://id.nlm.nih.gov/mesh/{MESH_YEAR}/{tree}")'
        for tree in trees
    )
    return f"FILTER({prefixes})"


def fetch_tree_descriptors(trees: Sequence[str]) -> list[dict[str, Any]]:
    """Fetch labels, entry terms, term UIs and tree numbers for tree prefixes."""
    query = _SPARQL_PREFIX + f"""
SELECT ?d ?label
       (GROUP_CONCAT(DISTINCT CONCAT(REPLACE(STR(?t), "^.*/", ""), "::", ?termLabel); separator="|") AS ?terms)
       (GROUP_CONCAT(DISTINCT ?tree; separator="|") AS ?trees)
WHERE {{
  ?d meshv:treeNumber ?tree .
  {_tree_filter(trees)}
  ?d rdfs:label ?label .
  ?d meshv:preferredConcept ?c .
  {{ ?c meshv:term ?t . ?t meshv:prefLabel ?termLabel . }}
  UNION
  {{ ?d meshv:preferredTerm ?t . ?t meshv:prefLabel ?termLabel . }}
}}
GROUP BY ?d ?label
ORDER BY ?d
"""
    rows = _sparql(query)
    return [_descriptor_row(row, matched_by="tree") for row in rows]


def fetch_all_descriptors(*, page_size: int = 500, max_pages: int = 0) -> list[dict[str, Any]]:
    """Fetch every MeSH descriptor through paginated SPARQL (full lexicon)."""
    descriptors: list[dict[str, Any]] = []
    offset = 0
    pages = 0
    while True:
        query = _SPARQL_PREFIX + f"""
SELECT ?d ?label
       (GROUP_CONCAT(DISTINCT CONCAT(REPLACE(STR(?t), "^.*/", ""), "::", ?termLabel); separator="|") AS ?terms)
       (GROUP_CONCAT(DISTINCT ?tree; separator="|") AS ?trees)
WHERE {{
  ?d rdf:type ?type .
  FILTER(?type IN (meshv:TopicalDescriptor, meshv:PublicationType, meshv:CheckTag, meshv:GeographicDescriptor, meshv:GenreDescriptor))
  ?d rdfs:label ?label .
  ?d meshv:preferredConcept ?c .
  {{ ?c meshv:term ?t . ?t meshv:prefLabel ?termLabel . }}
  UNION
  {{ ?d meshv:preferredTerm ?t . ?t meshv:prefLabel ?termLabel . }}
  OPTIONAL {{ ?d meshv:treeNumber ?tree . }}
}}
GROUP BY ?d ?label
ORDER BY ?d
LIMIT {int(page_size)} OFFSET {int(offset)}
"""
        rows = _sparql(query, timeout=120.0)
        if not rows:
            break
        descriptors.extend(_descriptor_row(row, matched_by="full_mesh_sparql") for row in rows)
        pages += 1
        offset += len(rows)
        print(f"  full MeSH page {pages}: {len(descriptors)} descriptors", file=sys.stderr)
        if max_pages and pages >= max_pages:
            break
        if len(rows) < page_size:
            break
    return descriptors


def fetch_descriptor_by_ui(ui: str) -> dict[str, Any] | None:
    query = _SPARQL_PREFIX + f"""
SELECT ?label
       (GROUP_CONCAT(DISTINCT CONCAT(REPLACE(STR(?t), "^.*/", ""), "::", ?termLabel); separator="|") AS ?terms)
       (GROUP_CONCAT(DISTINCT ?tree; separator="|") AS ?trees)
WHERE {{
  VALUES ?d {{ <http://id.nlm.nih.gov/mesh/{MESH_YEAR}/{ui}> }}
  ?d rdfs:label ?label .
  ?d meshv:preferredConcept ?c .
  {{ ?c meshv:term ?t . ?t meshv:prefLabel ?termLabel . }}
  UNION
  {{ ?d meshv:preferredTerm ?t . ?t meshv:prefLabel ?termLabel . }}
  OPTIONAL {{ ?d meshv:treeNumber ?tree . }}
}}
GROUP BY ?label
"""
    rows = _sparql(query)
    if not rows:
        return None
    row = rows[0]
    return {
        "ui": ui,
        "label": _literal(row, "label"),
        "terms": _parse_terms(_literal(row, "terms")),
        "tree_numbers": _parse_trees(_literal(row, "trees")),
        "matched_by": "extra_label",
    }


def lookup_descriptor(label: str) -> dict[str, Any] | None:
    """Resolve one label through NLM's official lookup API."""
    for match in ("exact", "contains"):
        params = urllib.parse.urlencode({"label": label, "match": match, "limit": 5})
        try:
            rows = _http_json(f"{LOOKUP_ENDPOINT}?{params}", timeout=45.0)
        except Exception:
            continue
        for row in rows or ():
            resource = str(row.get("resource") or "")
            ui = resource.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[A-Z]\d{6,9}", ui):
                continue
            descriptor = fetch_descriptor_by_ui(ui)
            if descriptor is None:
                continue
            descriptor["lookup_label"] = label
            descriptor["lookup_match"] = "exact" if str(row.get("label") or "").strip().lower() == label.strip().lower() else "top_match"
            return descriptor
    return None


def mapping_library_mesh_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    labels: list[str] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        for value in row.get("mesh") or ():
            text = str(value).strip()
            if text:
                labels.append(text)
    return list(dict.fromkeys(labels))


def mapping_library_reviewed_aliases(path: Path) -> list[dict[str, str]]:
    """Aliases already reviewed in the project mapping library.

    These are *not* official MeSH entry terms.  They are kept in a separate
    top-level section with their own provenance so the linker can use them
    without mislabelling them as NLM data.
    """
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    aliases: list[dict[str, str]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        ui = str(metadata.get("mesh_ui") or "").strip()
        status = str(metadata.get("mesh_review_status") or "").strip()
        if not re.fullmatch(r"[A-Z]\d{6,9}", ui) or status not in {"mesh_grounded", "mesh_top_match"}:
            continue
        for alias in row.get("aliases") or ():
            text = str(alias).strip()
            if not text or text.casefold() == str(row.get("concept") or "").casefold():
                continue
            aliases.append({
                "surface": text,
                "descriptor_ui": ui,
                "concept": str(row.get("concept") or ""),
                "source": path.name,
                "review_status": str(metadata.get("review_status") or "needs_review"),
            })
    return list({(item["surface"], item["descriptor_ui"]): item for item in aliases}.values())


def _text(element: Any) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _literal(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value.get("value") or "") if isinstance(value, Mapping) else ""


def _parse_terms(raw: str) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    for item in str(raw or "").split("|"):
        if "::" not in item:
            continue
        ui, text = item.split("::", 1)
        text = text.strip()
        if text:
            terms.append({"ui": ui.strip(), "text": text})
    return terms


def _parse_trees(raw: str) -> list[str]:
    return [item.rsplit("/", 1)[-1] for item in str(raw or "").split("|") if item.strip()]


def _descriptor_row(row: Mapping[str, Any], *, matched_by: str) -> dict[str, Any]:
    uri = _literal(row, "d")
    return {
        "ui": uri.rsplit("/", 1)[-1],
        "label": _literal(row, "label"),
        "terms": _parse_terms(_literal(row, "terms")),
        "tree_numbers": _parse_trees(_literal(row, "trees")),
        "matched_by": matched_by,
    }


def build_lexicon(
    *,
    trees: Sequence[str] = DEFAULT_TREES,
    extra_labels: Sequence[str] = (),
    mapping_library: Path | None = DEFAULT_MAPPING_LIBRARY,
) -> dict[str, Any]:
    descriptors: dict[str, dict[str, Any]] = {}
    for descriptor in fetch_tree_descriptors(trees):
        descriptors[descriptor["ui"]] = descriptor

    wanted = list(dict.fromkeys((*((mapping_library_mesh_terms(mapping_library) if mapping_library else [])), *extra_labels)))
    unresolved: list[str] = []
    for label in wanted:
        # Skip labels already covered by a descriptor label or entry term.
        if any(_covers(descriptor, label) for descriptor in descriptors.values()):
            continue
        descriptor = lookup_descriptor(label)
        if descriptor is None:
            unresolved.append(label)
            continue
        descriptors.setdefault(descriptor["ui"], descriptor)

    ordered = sorted(descriptors.values(), key=lambda item: (item["label"].casefold(), item["ui"]))
    return {
        "source": "NLM MeSH 2026 linked data (SPARQL + lookup API)",
        "mesh_year": MESH_YEAR,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scope": {"trees": list(trees), "extra_labels": wanted},
        "unresolved_labels": unresolved,
        "reviewed_aliases": mapping_library_reviewed_aliases(mapping_library) if mapping_library else [],
        "descriptor_count": len(ordered),
        "term_count": sum(len(item["terms"]) for item in ordered),
        "descriptors": ordered,
    }


def _covers(descriptor: Mapping[str, Any], label: str) -> bool:
    target = str(label).strip().casefold()
    if str(descriptor.get("label") or "").strip().casefold() == target:
        return True
    return any(str(term.get("text") or "").strip().casefold() == target for term in descriptor.get("terms") or ())


def download_mesh_xml(path: Path = DEFAULT_MESH_XML) -> Path:
    """Download the official NLM MeSH 2026 descriptor XML if absent."""
    path = path.expanduser()
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {MESH_XML_URL} -> {path}")
    request = urllib.request.Request(
        MESH_XML_URL,
        headers={"User-Agent": "ChinaLLM-mesh-lexicon/1.0 (research prototype)"},
    )
    with urllib.request.urlopen(request, timeout=300) as response, path.open("wb") as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
    return path


def parse_mesh_xml(path: Path) -> list[dict[str, Any]]:
    """Parse the official MeSH ``DescriptorRecordSet`` XML into lexicon rows.

    Uses ``iterparse`` and clears each record so a 300 MB official file does not
    need to be held as one tree.  Terms include official entry terms and
    permuted forms; the ``IsPermutedTermYN`` flag is preserved in the term
    record so callers can filter if they want a narrower index.
    """
    descriptors: list[dict[str, Any]] = []
    for _event, element in ET.iterparse(str(path), events=("end",)):
        if element.tag != "DescriptorRecord":
            continue
        ui = _text(element.find("DescriptorUI"))
        label = _text(element.find("DescriptorName/String"))
        if not ui or not label:
            element.clear()
            continue
        trees = [_text(node) for node in element.findall("TreeNumberList/TreeNumber") if _text(node)]
        terms: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for term in element.findall(".//TermList/Term"):
            text = _text(term.find("String"))
            term_ui = _text(term.find("TermUI"))
            if not text:
                continue
            key = (term_ui, text)
            if key in seen:
                continue
            seen.add(key)
            terms.append({
                "ui": term_ui,
                "text": text,
                "permuted": str(term.get("IsPermutedTermYN") or "").upper() == "Y",
                "concept_preferred": str(term.get("ConceptPreferredTermYN") or "").upper() == "Y",
            })
        descriptors.append({
            "ui": ui,
            "label": label,
            "terms": terms,
            "tree_numbers": trees,
            "matched_by": "full_mesh_xml",
        })
        element.clear()
    descriptors.sort(key=lambda item: (item["label"].casefold(), item["ui"]))
    return descriptors


def build_full_lexicon(*, mesh_xml: Path = DEFAULT_MESH_XML, download: bool = True, source: str = "xml", page_size: int = 500, max_pages: int = 0, mapping_library: Path | None = DEFAULT_MAPPING_LIBRARY) -> dict[str, Any]:
    if source == "sparql":
        descriptors = fetch_all_descriptors(page_size=page_size, max_pages=max_pages)
        scope = {"mode": "full", "source": "sparql", "page_size": page_size}
        source_label = f"NLM MeSH {MESH_YEAR} linked data (full SPARQL pagination)"
    else:
        path = download_mesh_xml(mesh_xml) if download else mesh_xml.expanduser()
        if not path.exists():
            raise FileNotFoundError(f"MeSH XML not found: {path}; pass --download or --mesh-xml")
        descriptors = parse_mesh_xml(path)
        scope = {"mode": "full", "source": "xml", "xml_path": str(path)}
        source_label = f"NLM MeSH {MESH_YEAR} official desc{MESH_YEAR}.xml"
    return {
        "source": source_label,
        "mesh_year": MESH_YEAR,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scope": scope,
        "unresolved_labels": [],
        "reviewed_aliases": mapping_library_reviewed_aliases(mapping_library) if mapping_library else [],
        "descriptor_count": len(descriptors),
        "term_count": sum(len(item["terms"]) for item in descriptors),
        "descriptors": descriptors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", action="append", dest="trees", help="MeSH tree prefix (repeatable; default C16)")
    parser.add_argument("--extra-label", action="append", default=[], help="Additional MeSH descriptor label to include (repeatable)")
    parser.add_argument("--mapping-library", type=Path, default=DEFAULT_MAPPING_LIBRARY, help="Reviewed mapping library whose MeSH labels are included")
    parser.add_argument("--no-mapping-library", action="store_true", help="Do not read MeSH labels from the mapping library")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--full", action="store_true", help="Build the full MeSH 2026 lexicon")
    parser.add_argument("--full-source", choices=("xml", "sparql"), default="xml", help="Full lexicon source: official XML or paginated SPARQL")
    parser.add_argument("--page-size", type=int, default=500, help="SPARQL page size for --full-source sparql")
    parser.add_argument("--max-pages", type=int, default=0, help="Optional cap on SPARQL pages (smoke tests)")
    parser.add_argument("--mesh-xml", type=Path, default=DEFAULT_MESH_XML, help="Path to the official desc2026.xml (used with --full)")
    parser.add_argument("--no-download", action="store_true", help="Do not download the MeSH XML; fail if it is absent")
    args = parser.parse_args(argv)

    if args.full:
        output = args.output if args.output != DEFAULT_OUTPUT else DEFAULT_FULL_OUTPUT
        lexicon = build_full_lexicon(
            mesh_xml=args.mesh_xml,
            download=not args.no_download,
            source=args.full_source,
            page_size=args.page_size,
            max_pages=args.max_pages,
            mapping_library=None if args.no_mapping_library else args.mapping_library,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(lexicon, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {output} descriptors={lexicon['descriptor_count']} terms={lexicon['term_count']}")
        return 0

    trees = tuple(args.trees or DEFAULT_TREES)
    extra = tuple((*DEFAULT_EXTRA_LABELS, *args.extra_label))
    lexicon = build_lexicon(
        trees=trees,
        extra_labels=extra,
        mapping_library=None if args.no_mapping_library else args.mapping_library,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(lexicon, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {args.output} descriptors={lexicon['descriptor_count']} "
        f"terms={lexicon['term_count']} unresolved={len(lexicon['unresolved_labels'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
