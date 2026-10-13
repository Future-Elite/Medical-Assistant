"""层 3 的补充映射表 —— 合成 FHIR 里表达不了的中国字段。

三样东西**在 FHIR 里根本没有位置**，不该在 Java 里硬编码，也不该由导出层凭空编，
所以放到数据文件里由数据说了算：

* 机构的「等级」「类型」 —— Synthea 的 ``Organization`` 只有 ``type``
  （而且是 ``Healthcare Provider`` 这种英文），没有等级
* 医生的「科室」「所属机构」 —— ``PractitionerRole`` **只在
  ``exporter.fhir.use_us_core_ig = true`` 时才产出**，而且它的 ``specialty``
  在 ``FhirR4.java`` 里被写死成 ``General Practice Physician``，
  开了也拿不到真科室
* 药品的「规格」「给药途径」、检验的「参考区间」 ——
  ``FhirR4`` 的 ``MedicationRequest`` 只读 ``as_needed`` / ``dosage`` /
  ``instructions`` / ``refills``；``Observation`` 不带 ``referenceRange``。
  这两样都在 ``terminology/`` 的白名单里，是**我们自己带 source 写的**，
  比 Synthea 的产出更可信，所以从那里回填。

CSV 文件（UTF-8，可以带 BOM）：

* ``shanghai/organization_levels.csv`` —— ``机构名称,等级,类型``
  按名称匹配 Synthea 的 ``Organization.name``（也就是我们 providers CSV 的 name）。
* ``shanghai/department_map.csv`` —— ``源科室名,中文科室``
  把 ``Encounter.type`` 的英文名映射成中文科室。

术语表（``terminology/*.yaml``）由 :meth:`Lookups.load_terminology` 单独加载，
它**不是必需的** —— 缺了只是回到"这两列留空"，导出照样跑完。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..terminology.registry import Terminology, TerminologyError


@dataclass(frozen=True)
class DrugSpec:
    """药品字典里 FHIR 表达不了的那部分。"""

    name_zh: str
    #: 规格，如 ``0.5g``
    spec_zh: str
    #: 给药途径，如 ``口服``
    route_zh: str


@dataclass(frozen=True)
class LabSpec:
    """检验项目字典里 FHIR 表达不了的那部分。"""

    name_zh: str
    unit_zh: str
    reference_range: tuple[float, float]


@dataclass
class Lookups:
    #: 机构名称 -> (等级, 类型)
    organization: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: 源科室名（大写归一）-> 中文科室
    department: dict[str, str] = field(default_factory=dict)
    #: 药品码值（ATC / YPID，与 FHIR 里写的一致）-> 规格与途径
    drugs: dict[str, DrugSpec] = field(default_factory=dict)
    #: LOINC 码值 -> 参考区间
    labs: dict[str, LabSpec] = field(default_factory=dict)
    #: 编码体系 URI -> (中文标准标签, 是否已核实)
    systems: dict[str, tuple[str, bool]] = field(default_factory=dict)
    #: 读过的文件，用于在报告里说明数据来源
    loaded_files: list[str] = field(default_factory=list)
    #: 加载期发现的问题（术语表坏了之类），不中断导出
    load_warnings: list[str] = field(default_factory=list)

    def org_level(self, name: str) -> str:
        return self.organization.get(name.strip(), ("", ""))[0]

    def org_type(self, name: str) -> str:
        return self.organization.get(name.strip(), ("", ""))[1]

    def dept_zh(self, source: str) -> str:
        """映射科室名。查不到就原样返回，不丢信息。"""
        key = source.strip().upper()
        if not key:
            return ""
        return self.department.get(key, source.strip())

    def department_names(self) -> list[str]:
        """已登记的中文科室名（去重、保序）。给"分不出科室"时的兜底挑选用。"""
        seen: dict[str, None] = {}
        for value in self.department.values():
            if value:
                seen.setdefault(value, None)
        return list(seen)

    def drug(self, code: str) -> DrugSpec | None:
        """按 FHIR 里的药品编码取规格/途径。取不到给 None。"""
        return self.drugs.get(code.strip()) if code else None

    def lab(self, code: str) -> LabSpec | None:
        """按 LOINC 码取参考区间。取不到给 None。"""
        return self.labs.get(code.strip()) if code else None

    def system_label(self, uri: str) -> str:
        """编码体系 URI -> 中文标签。查不到就原样返回 URI。

        ``verified: false`` 的体系（以及 ``urn:hisgen:local:`` 这种占位体系）
        会带上 ``【待核实】`` 前缀 —— 产物里必须一眼能看出
        「这不是真实编码」，否则那个码值会被下游当成真的用。
        """
        uri = uri.strip()
        if not uri:
            return ""
        entry = self.systems.get(uri)
        if entry is None:
            return uri
        label, verified = entry
        return label if verified else f"【待核实】{label}"

    # ------------------------------------------------------------- 加载

    def load_terminology(self, root: str | Path | None) -> None:
        """加载 ``terminology/`` 白名单，建按码值的反查索引。

        索引按 **FHIR 里实际写出的码值**建（药品是 ATC，检验是 LOINC）——
        导出层手里只有码值，没有术语表的 key。
        """
        if root is None:
            return
        base = Path(root)
        if not base.is_dir():
            return
        try:
            term = Terminology.load(base)
        except TerminologyError as exc:
            # 术语表坏了不该让整个导出失败：这三列留空比拿不到其余七张表好
            self.load_warnings.append(f"术语表未加载（{exc}）")
            return

        for med in term.medications.values():
            self.drugs[med.code.code] = DrugSpec(
                name_zh=med.name_zh,
                spec_zh=med.spec_zh,
                route_zh=med.route_zh,
            )
        for lab in term.labs.values():
            self.labs[lab.code.code] = LabSpec(
                name_zh=lab.name_zh,
                unit_zh=lab.unit_zh,
                reference_range=lab.reference_range,
            )
        for sysdef in term.systems.values():
            self.systems[sysdef.uri] = (
                _system_label(sysdef.name_zh, sysdef.standard),
                sysdef.verified,
            )
        self.loaded_files.append(str(base))


def _system_label(name_zh: str, standard: str) -> str:
    """编码体系标签。

    用 ``标准名`` 而不是 URI：``编码体系`` 这一列是给人建库时对照用的，
    ``urn:hisgen:unverified:ICD10_CN`` 读了也不知道是什么，
    ``GB/T 14396-2016`` 一眼就知道该去查哪份标准。
    URI 本身不会丢 —— 导出报告里会打一张 URI ↔ 标签的对照表。
    """
    standard = (standard or "").strip()
    return standard if standard and standard != "无" else (name_zh or "")


def load(shanghai_dir: str | Path | None) -> Lookups:
    """读 ``shanghai/`` 下的映射表。目录不存在就返回空的。"""
    out = Lookups()
    if shanghai_dir is None:
        return out
    root = Path(shanghai_dir)
    if not root.is_dir():
        return out

    org = root / "organization_levels.csv"
    if org.is_file():
        for row in _rows(org):
            name = row.get("机构名称", "").strip()
            if name:
                out.organization[name] = (
                    row.get("等级", "").strip(),
                    row.get("类型", "").strip(),
                )
        out.loaded_files.append(str(org))

    dept = root / "department_map.csv"
    if dept.is_file():
        for row in _rows(dept):
            src = row.get("源科室名", "").strip().upper()
            if src:
                out.department[src] = row.get("中文科室", "").strip()
        out.loaded_files.append(str(dept))

    return out


def _rows(path: Path) -> list[dict[str, str]]:
    # utf-8-sig：文件可能是 Excel 存的，带 BOM；不带 BOM 也读得动
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]