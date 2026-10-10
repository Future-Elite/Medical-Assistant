"""Test helpers: assemble the test fixtures through the *custom source interface*.

The product registers no built-in corpus. Tests therefore exercise the same
code path a real caller uses: build a ``CustomSourceLibrary`` payload from the
test fixtures and let ``server.create_tool("custom", ...)`` serve it.
"""
from __future__ import annotations

from rag.connectors import ConnectorRegistry, InMemoryConnector, VersionedGuidelineConnector
from rag.custom_sources import CustomSource, CustomSourceLibrary, CustomSourceConnector
from rag.pipeline import EvidenceRetrievalPipeline
from rag.retrieval_adapters import DeterministicSemanticStub  # noqa: F401  (re-export for tests)
from rag.tests.fixtures import SYNTHETIC_GUIDELINES, SYNTHETIC_HIS_SNAPSHOTS, SYNTHETIC_PUBMED


def fixture_library() -> CustomSourceLibrary:
    """A caller-style library holding the test fixtures (guideline + pubmed kinds)."""
    library = CustomSourceLibrary()
    library.register(CustomSource(
        source_id="test-guideline-library",
        kinds=("guideline",),
        documents=SYNTHETIC_GUIDELINES,
        description="测试用指南库（synthetic，仅测试）",
        version="test-2026-10-09",
    ))
    library.register(CustomSource(
        source_id="test-pubmed-library",
        kinds=("pubmed",),
        documents=SYNTHETIC_PUBMED,
        description="测试用文献库（synthetic，仅测试）",
        version="test-2026-10-09",
    ))
    return library


def fixture_registry() -> ConnectorRegistry:
    """Direct connector registry over the fixtures (for pipeline-level tests).

    Documents keep their fixture ``source_id`` (``guideline-fixture`` /
    ``pubmed-fixture``) so existing assertions on source ids stay valid; the
    *custom interface* path with its own source ids is covered by
    ``fixture_custom_registry`` / ``fixture_library``.
    """
    registry = ConnectorRegistry()
    registry.register(VersionedGuidelineConnector("guideline-fixture", SYNTHETIC_GUIDELINES))
    registry.register(InMemoryConnector("pubmed-fixture", ("pubmed",), SYNTHETIC_PUBMED))
    return registry


def fixture_pipeline(**kwargs) -> EvidenceRetrievalPipeline:
    return EvidenceRetrievalPipeline(fixture_registry(), **kwargs)


def fixture_custom_registry() -> ConnectorRegistry:
    """Registry built through ``CustomSourceLibrary.build_registry`` (interface path)."""
    registry, _warnings = fixture_library().build_registry()
    return registry


def fixture_tool(*, auditor=None, neural_retrievers=None, reranker=None):
    """An ``EvidenceRAG`` served through the custom-source path (no built-in corpus)."""
    from rag.agent import EvidenceRAG

    kwargs = {"neural_retrievers": dict(neural_retrievers or {}), "reranker": reranker}
    if auditor is not None:
        return EvidenceRAG(EvidenceRetrievalPipeline(fixture_custom_registry(), **kwargs), auditor=auditor)
    return EvidenceRAG(EvidenceRetrievalPipeline(fixture_custom_registry(), **kwargs))


__all__ = [
    "SYNTHETIC_GUIDELINES",
    "SYNTHETIC_HIS_SNAPSHOTS",
    "SYNTHETIC_PUBMED",
    "fixture_library",
    "fixture_registry",
    "fixture_pipeline",
    "fixture_custom_registry",
    "fixture_tool",
]
