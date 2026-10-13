"""层 5：FHIR -> 8 张关系型 CSV。

设计文档 §9。这一层**不做任何翻译**（不联网、不调模型），
只做结构转换 + 少量 FHIR 里根本没有的字段的规则化生成
（医保号、民族、职称 —— 见 :mod:`.derive`，均为合成数据，已标注）。
"""

from .csvio import describe, write_all, write_table
from .derive import Deriver
from .loader import ExportError, FhirData, load_dir
from .lookups import Lookups
from .tables import (
    TABLES,
    Ctx,
    Table,
    build_all,
    check_foreign_keys,
)

__all__ = [
    "Ctx",
    "Deriver",
    "ExportError",
    "FhirData",
    "Lookups",
    "TABLES",
    "Table",
    "build_all",
    "check_foreign_keys",
    "describe",
    "load_dir",
    "write_all",
    "write_table",
]