"""层 2：确定性编译器，IR -> Synthea GMF。

这里**不含大模型**。是整套方案中唯一负责 GMF 正确性的地方。
"""

from .compile import compile_module, compile_to_file
from .conditions import describe, to_logic
from .errors import CompileError
from .validate import validate_gmf, validate_terminology

__all__ = [
    "CompileError",
    "compile_module",
    "compile_to_file",
    "describe",
    "to_logic",
    "validate_gmf",
    "validate_terminology",
]