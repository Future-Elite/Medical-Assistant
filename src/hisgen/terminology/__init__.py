"""术语与编码白名单。

IR 里不出现码值，只出现这里的键名。编译器负责把键名换成码，
并在过程中记录「用到哪些未核实的编码体系」，产出待核实清单。
"""

from .registry import (
    Code,
    LabTest,
    Medication,
    Procedure,
    System,
    Terminology,
    TerminologyError,
)

__all__ = [
    "Code",
    "LabTest",
    "Medication",
    "Procedure",
    "System",
    "Terminology",
    "TerminologyError",
]