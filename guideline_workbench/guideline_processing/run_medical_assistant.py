"""Run the copied Medical-Assistant RAG against exported guideline chunks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
RAG_ROOT = REPO_ROOT
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

from rag.agent import EvidenceRAG
from rag.connectors import ConnectorRegistry
from rag.custom_sources import CustomSource, CustomSourceConnector, load_custom_library
from rag.pipeline import EvidenceRetrievalPipeline


class FullCorpusConnector:
    """Let the RAG rank the complete small demo corpus, not its first 50 rows."""

    def __init__(self, source: CustomSource):
        self._connector = CustomSourceConnector(source)
        self.source_id = self._connector.source_id
        self.kinds = self._connector.kinds
        self._document_count = len(source.documents)

    def search(self, plan, *, limit):
        return self._connector.search(plan, limit=max(limit, self._document_count))


def build_rag(library_path: Path) -> EvidenceRAG:
    library = load_custom_library(library_path)
    sources = library.describe()
    if not sources:
        raise RuntimeError("No guideline sources found in the custom library.")
    registry = ConnectorRegistry()
    for source in sources:
        registry.register(FullCorpusConnector(library.get(source["source_id"])))
    return EvidenceRAG(EvidenceRetrievalPipeline(registry))


def run(library_path: Path, top_k: int) -> int:
    rag = build_rag(library_path)

    questions = (
        "新生儿感染性肺炎的早发型和晚发型如何分类？",
        "2025版初始NRS适应证：RDS风险早产儿应如何使用CPAP或同步NIPPV？",
        "新生儿复苏时，心率持续低于60次/min应采取哪些措施？",
    )
    for index, question in enumerate(questions, start=1):
        result = rag.retrieve_evidence({
            "request_id": f"guideline-demo-{index}",
            "question": question,
            "source_mode": "custom",
            "source_kinds": ["guideline"],
            "top_k": top_k,
        })
        ranked = result["ranked_documents"]
        if not ranked or not result["citations"]:
            raise RuntimeError(f"RAG returned no evidence for query: {question}\n{result['warnings']}")

        first = ranked[0]
        citation = rag.open_citation({
            "retrieval_id": result["retrieval_id"],
            "citation_id": first["citation_id"],
        })["citation"]
        if not citation.get("quote") or not citation.get("locator"):
            raise RuntimeError(f"Citation is missing quote or locator for query: {question}")

        print(f"Query {index}: {question}")
        print(f"  returned={len(ranked)} source={first['document']['source_id']}")
        print(f"  title={citation['title']}")
        print(f"  locator={citation['locator']}")
        print(f"  quote={citation['quote'][:240]}")
        print(f"  citation_open=ok retrieval_id={result['retrieval_id']}")

    print("Integration check passed: PDF chunks -> custom library -> BM25 retrieval -> citation open")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Medical-Assistant RAG on demo guideline chunks.")
    parser.add_argument("--library", type=Path, default=ROOT / "data" / "medical_assistant_guidelines.json")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    raise SystemExit(run(args.library, args.top_k))


if __name__ == "__main__":
    main()
