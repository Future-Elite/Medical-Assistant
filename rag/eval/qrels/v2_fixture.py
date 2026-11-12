"""The four-guideline fixture plus the AAP table-lookup capability.

This is a guideline-capability fixture, not a disease-specific test subset.
"""
from __future__ import annotations

from pathlib import Path

from .gold_rules import load_criteria_fixture

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "criteria_fixture" / "registry_scope_criteria_v2.json"


def load_v2_fixture() -> dict:
    return load_criteria_fixture(FIXTURE_PATH)
