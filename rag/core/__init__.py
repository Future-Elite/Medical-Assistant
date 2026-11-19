"""Core contracts: models, configuration and the read-only HIS snapshot."""
from .config import RAGConfig, get_config
from .his import HISAdapter, InMemoryHISAdapter, PatientRecord, PatientSnapshot, snapshot_from_dict
from .models import parse_iso_date

__all__ = [
    "HISAdapter",
    "InMemoryHISAdapter",
    "PatientRecord",
    "PatientSnapshot",
    "RAGConfig",
    "get_config",
    "parse_iso_date",
    "snapshot_from_dict",
]
