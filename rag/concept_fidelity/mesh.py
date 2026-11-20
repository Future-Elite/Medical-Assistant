"""MeSH-backed concept index for sibling / hierarchy confusion probes.

The index reuses the project's MeSH 2026 snapshot (``MeshLexicon``) and adds
tree indexes.  It does not invent concept identities: every returned UI is a
descriptor present in the snapshot, and missing concepts are reported as
``unresolved`` rather than guessed.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable, Mapping, Sequence

from ..quality.mesh_linking import MeshDescriptor, MeshLexicon

_GENERIC_LABEL_TOKENS = frozenset({
    "the", "of", "and", "with", "without", "syndrome", "disease", "disorder",
    "neonatal", "newborn", "infant", "fetal", "primary", "secondary", "chronic",
    "acute", "congenital", "hereditary", "idiopathic", "other", "unspecified",
})


def _label_tokens(label: str) -> frozenset[str]:
    return frozenset(
        token for token in re.findall(r"[a-z][a-z0-9-]{2,}", label.lower())
        if token not in _GENERIC_LABEL_TOKENS
    )


class ConceptIndex:
    """Tree-aware index over one local MeSH snapshot."""

    def __init__(self, lexicon: MeshLexicon) -> None:
        self.lexicon = lexicon
        self.by_ui: dict[str, MeshDescriptor] = dict(lexicon.by_ui)
        self._prefix: dict[str, set[str]] = defaultdict(set)
        self._parent_children: dict[str, set[str]] = defaultdict(set)
        for descriptor in lexicon.descriptors:
            for tree in descriptor.tree_numbers:
                for prefix in self._tree_prefixes(tree):
                    self._prefix[prefix].add(descriptor.ui)
                parts = [part for part in tree.split(".") if part]
                if len(parts) >= 2:
                    self._parent_children[".".join(parts[:-1])].add(descriptor.ui)
        self._zh: dict[str, tuple[str, ...]] = defaultdict(tuple)
        for alias in lexicon.reviewed_aliases:
            ui = str(alias.get("descriptor_ui") or "").strip()
            surface = str(alias.get("surface") or "").strip()
            if ui in self.by_ui and surface and any(
                "\u4e00" <= char <= "\u9fff" for char in surface
            ):
                self._zh[ui] = tuple(dict.fromkeys((*self._zh[ui], surface)))

    @staticmethod
    def _tree_prefixes(tree: str) -> tuple[str, ...]:
        parts = [part for part in str(tree or "").split(".") if part]
        return tuple(".".join(parts[:index]) for index in range(1, len(parts) + 1))

    def label(self, ui: str) -> str:
        descriptor = self.by_ui.get(str(ui))
        if descriptor is None:
            raise KeyError(f"unknown MeSH descriptor: {ui}")
        return descriptor.label

    def tree_numbers(self, ui: str) -> tuple[str, ...]:
        return tuple(self.by_ui[str(ui)].tree_numbers)

    def chinese_surfaces(self, ui: str) -> tuple[str, ...]:
        return self._zh.get(str(ui), ())

    def descendants(self, ui: str) -> tuple[str, ...]:
        ui = str(ui)
        if ui not in self.by_ui:
            raise KeyError(f"unknown MeSH descriptor: {ui}")
        result: set[str] = set()
        for tree in self.tree_numbers(ui):
            prefix = tree + "."
            for candidate, members in self._prefix.items():
                if candidate.startswith(prefix):
                    result.update(members)
        result.discard(ui)
        return tuple(sorted(result))

    def siblings(self, ui: str) -> tuple[str, ...]:
        ui = str(ui)
        if ui not in self.by_ui:
            raise KeyError(f"unknown MeSH descriptor: {ui}")
        result: set[str] = set()
        for tree in self.tree_numbers(ui):
            parts = [part for part in tree.split(".") if part]
            if len(parts) >= 2:
                result.update(self._parent_children.get(".".join(parts[:-1]), set()))
        result.discard(ui)
        return tuple(sorted(result))

    def confusion_set(self, ui: str, *, size: int = 3) -> tuple[str, ...]:
        """Nearest descendants first, then siblings ranked by label overlap.

        Descendants model hyponym drift (``Hyperbilirubinemia, Neonatal`` vs
        ``Jaundice, Neonatal``); siblings model same-branch confusions
        (``RDS`` vs ``BPD``).  The order is deterministic.
        """
        if size < 1:
            return ()
        target_tokens = _label_tokens(self.label(ui))
        descendants = sorted(
            self.descendants(ui),
            key=lambda item: (self._tree_depth(item), -len(target_tokens & _label_tokens(self.label(item))), item),
        )
        siblings = sorted(
            self.siblings(ui),
            key=lambda item: (-len(target_tokens & _label_tokens(self.label(item))), self._tree_depth(item), item),
        )
        ordered = list(dict.fromkeys((*descendants, *siblings)))
        return tuple(ordered[:size])

    def _tree_depth(self, ui: str) -> int:
        return min((len(tree.split(".")) for tree in self.tree_numbers(ui)), default=0)

    def describe(self) -> dict[str, object]:
        return dict(self.lexicon.describe())


def load_concept_index(path: str) -> ConceptIndex:
    return ConceptIndex(MeshLexicon.load(path))


def validate_concept_ids(index: ConceptIndex, values: Iterable[str]) -> tuple[str, ...]:
    unknown = sorted({str(value).strip() for value in values if str(value).strip() not in index.by_ui})
    if unknown:
        raise ValueError("unknown MeSH descriptor(s): " + ", ".join(unknown))
    return tuple(str(value).strip() for value in values)


def concept_label_map(index: ConceptIndex, values: Sequence[str]) -> Mapping[str, str]:
    return {ui: index.label(ui) for ui in values}
