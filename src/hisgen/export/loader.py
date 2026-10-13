"""读 Synthea 的 FHIR 输出目录。

目录里混着三类文件，统统都要读 —— 只读患者 Bundle 是拿不到机构和医生的：

* ``<patient_uuid>.json``          一个患者一个 Bundle
* ``hospitalInformation*.json``    机构（Organization + Location）
* ``practitionerInformation*.json`` 医生（Practitioner + PractitionerRole）

另外 ``exporter.fhir.bulk_data = true`` 时产出的是 ``*.ndjson``（每行一个资源），
所以两种形态都支持。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


class ExportError(RuntimeError):
    """FHIR 输入不可用。"""


@dataclass
class FhirData:
    """按资源类型索引的 FHIR 资源集合。"""

    #: resourceType -> 资源列表（保持文件读取顺序）
    by_type: dict[str, list[dict]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: (resourceType, id) -> 资源，用于解引用
    by_id: dict[tuple[str, str], dict] = field(default_factory=dict)
    #: (resourceType, system, value) -> 资源，用于解析按标识符写的引用
    by_identifier: dict[tuple[str, str, str], dict] = field(default_factory=dict)
    #: 裸 id -> 资源，用于解析 ``urn:uuid:`` 这种不带资源类型的引用
    by_uuid: dict[str, dict] = field(default_factory=dict)
    #: 实际读到的文件数，用于报告
    files_read: int = 0

    def add(self, resource: dict) -> None:
        rtype = resource.get("resourceType")
        if not rtype:
            return
        self.by_type[rtype].append(resource)
        rid = resource.get("id")
        if rid:
            self.by_id[(rtype, str(rid))] = resource
            self.by_uuid.setdefault(str(rid), resource)
        for ident in resource.get("identifier") or []:
            if not isinstance(ident, dict):
                continue
            system, value = ident.get("system"), ident.get("value")
            if system and value is not None:
                self.by_identifier[(rtype, str(system), str(value))] = resource

    def get(self, resource_type: str) -> list[dict]:
        return self.by_type.get(resource_type, [])

    def deref(self, reference: str | None) -> dict | None:
        """把引用解析成资源本身。三种形态都认，见 :meth:`ref_id`。"""
        if not reference:
            return None
        if reference.startswith("urn:uuid:"):
            return self.by_uuid.get(reference[len("urn:uuid:"):])
        if "?identifier=" in reference:
            rtype, ident = reference.split("?identifier=", 1)
            if "|" not in ident:
                return None
            system, value = ident.split("|", 1)
            return self.by_identifier.get((rtype, system, value))
        if "/" not in reference:
            return None
        rtype, rid = reference.split("/", 1)
        return self.by_id.get((rtype, rid))

    def ref_id(self, reference: str | None) -> str:
        """把引用解析成**目标资源的 id**。解析不出来就返回空串。

        Synthea 同时用着**三种**引用形态，一种不认就会静默丢外键：

        * ``Patient/abc`` —— 按资源 id。Synthea 几乎不用，但 FHIR 规范写法要认
        * ``urn:uuid:abc`` —— bundle 内部引用。``subject`` / ``encounter``
          走的就是这个，它是**没有斜杠的**
        * ``Organization?identifier=https://github.com/...|abc`` —— 按标识符。
          出现在 ``Encounter.serviceProvider``（机构）
          和 ``Encounter.participant`` / ``.requester``（医生用 NPI）上

        后两种都是踩过的坑：``urn:uuid:`` 按 ``Type/id`` 切分切不出东西，
        于是 就诊.patient_id 与 诊断/医嘱/检验/手术操作.就诊ID **整列为空**；
        ``?identifier=`` 切出来的是 ``/github.com/synthetichealth/synthea|abc``
        这串垃圾，1144 行假外键一个都对不上机构/医生表里的真 UUID。
        两者都不会报错，只会让导出的表静默地没有外键。
        """
        target = self.deref(reference)
        if target is not None and target.get("id"):
            return str(target["id"])
        # 解引用失败时退回按 id 切分：``Type/id`` 这种还是能切出来的。
        # ``urn:uuid:`` 没有斜杠，留空；``?identifier=`` 切出来是垃圾，
        # 与其往表里写假外键，不如留空让"缺少外键"这件事暴露出来。
        if (
            not reference
            or reference.startswith("urn:uuid:")
            or "?identifier=" in reference
            or "/" not in reference
        ):
            return ""
        return reference.split("/", 1)[1]

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in sorted(self.by_type.items())}


def load_dir(root: str | Path) -> FhirData:
    """把一个目录下的 FHIR 输出全部读进来。"""
    root = Path(root)
    if not root.is_dir():
        raise ExportError(f"FHIR 输出目录不存在：{root}")

    data = FhirData()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.suffix.lower() in (".json", ".ndjson") and p.is_file()
    )
    if not files:
        raise ExportError(
            f"{root} 下没有 .json / .ndjson 文件。"
            "请确认 Synthea 运行时 exporter.fhir.export = true。"
        )

    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".ndjson":
            n = _load_ndjson(text, data, path)
        else:
            n = _load_json(text, data, path)
        if n:
            data.files_read += 1

    if not data.by_type:
        raise ExportError(f"{root} 下解析出了 0 个资源，文件格式可能不对")
    return data


def _load_json(text: str, data: FhirData, path: Path) -> int:
    """一个 .json 文件可能是 Bundle，也可能是一个裸资源。"""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportError(f"{path.name} 不是合法 JSON：{exc}") from None

    if not isinstance(doc, dict):
        return 0

    if doc.get("resourceType") == "Bundle":
        n = 0
        for entry in doc.get("entry") or []:
            resource = entry.get("resource")
            if isinstance(resource, dict):
                data.add(resource)
                n += 1
        return n

    if doc.get("resourceType"):
        data.add(doc)
        return 1
    return 0


def _load_ndjson(text: str, data: FhirData, path: Path) -> int:
    n = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            resource = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExportError(f"{path.name}:{lineno} 不是合法 JSON：{exc}") from None
        if isinstance(resource, dict) and resource.get("resourceType"):
            data.add(resource)
            n += 1
    return n