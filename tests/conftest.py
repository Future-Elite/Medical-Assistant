"""公共 fixture。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hisgen import PROJECT_ROOT
from hisgen.terminology import Terminology

IR_DIR = PROJECT_ROOT / "ir"
TERMINOLOGY_DIR = PROJECT_ROOT / "terminology"


@pytest.fixture(scope="session")
def terminology() -> Terminology:
    return Terminology.load(TERMINOLOGY_DIR)


@pytest.fixture(scope="session")
def t2dm_ir_path() -> Path:
    path = IR_DIR / "sh_t2dm.yaml"
    if not path.is_file():
        pytest.skip(f"缺少 IR 文件 {path}")
    return path


def write_bundle(directory: Path, name: str, *resources: dict) -> Path:
    """把若干资源写成一个 Bundle 文件 —— 模拟 Synthea 的每患者一个 Bundle。"""
    directory.mkdir(parents=True, exist_ok=True)
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"resource": r} for r in resources],
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return path


def write_resource(directory: Path, name: str, resource: dict) -> Path:
    """写一个裸资源文件 —— 模拟 hospitalInformation*.json。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
    return path