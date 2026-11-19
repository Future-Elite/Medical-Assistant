"""Compatibility import for the Medical-Assistant custom-source exporter."""
from __future__ import annotations

from .medical_assistant_export import MAX_DOCUMENTS_PER_SOURCE, export_custom_source

__all__ = ["MAX_DOCUMENTS_PER_SOURCE", "export_custom_source"]
