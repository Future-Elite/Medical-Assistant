"""层 4：调用 run_synthea。"""

from .run import RunSpec, SyntheaNotFound, find_synthea, run_synthea

__all__ = ["RunSpec", "SyntheaNotFound", "find_synthea", "run_synthea"]