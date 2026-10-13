"""写 CSV。

编码固定 ``utf-8-sig``（UTF-8 带 BOM）—— 这批表的目标读者是 Excel 和医院里
的数据库导入工具，不带 BOM 的 UTF-8 在中文 Windows 上打开就是乱码。
BOM 只在文件头写一次，Python 的 ``utf-8-sig`` 编码器负责这件事。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .tables import Table


def write_table(table: Table, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{table.name}.csv"
    # newline="" 是 csv 模块的要求：否则 Windows 上会多出一堆空行
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(table.headers)
        writer.writerows(table.rows)
    return path


def write_all(tables: list[Table], out_dir: str | Path) -> list[Path]:
    return [write_table(t, out_dir) for t in tables]


def describe(tables: list[Table]) -> str:
    width = max((len(t.name) for t in tables), default=8)
    lines = []
    for t in tables:
        lines.append(
            f"  {t.name.ljust(width)}  {len(t.rows):>7} 行  {len(t.headers)} 列"
        )
    total = sum(len(t.rows) for t in tables)
    lines.append(f"  {'合计'.ljust(width)}  {total:>7} 行")
    return "\n".join(lines)