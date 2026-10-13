"""加载 terminology/*.yaml 并提供查表 API。

设计要点：**码值只在白名单里出现**。IR 给键名，这里给码。
任何查不到的键名都是硬错误（:class:`TerminologyError`），不是告警 ——
因为 IR 里的键名是 LLM 生成的，放过一个查不到的键，产物里就会出现
一个没有出处的编码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TERMINOLOGY_DIR = PROJECT_ROOT / "terminology"

#: 国标码缺失时用于承载中文 display 的占位体系
LOCAL_SYSTEM = "LOCAL_ZH"
#: 国标诊断码缺失时的兜底体系
FALLBACK_DIAGNOSIS_SYSTEM = "ICD10_WHO"
#: 国标药品码缺失时的兜底体系
FALLBACK_MEDICATION_SYSTEM = "ATC"


class TerminologyError(ValueError):
    """查表失败，或白名单文件本身有问题。"""


@dataclass(frozen=True)
class System:
    key: str
    name_zh: str
    standard: str
    uri: str
    verified: bool
    note: str = ""


@dataclass(frozen=True)
class Code:
    """一个可直接写进 GMF 的编码。"""

    system: str
    uri: str
    code: str
    display: str
    verified: bool
    source: str = ""
    #: 编译期提示：这条码为什么不是理想的码（例如国标缺失、走了兜底）
    caveat: str = ""

    def to_gmf(self) -> dict[str, str]:
        return {"system": self.uri, "code": self.code, "display": self.display}


@dataclass(frozen=True)
class LabTest:
    key: str
    name_zh: str
    name_en: str
    code: Code
    unit_zh: str
    reference_range: tuple[float, float]
    source: str


@dataclass(frozen=True)
class Dosage:
    """结构化用法用量。

    Synthea 的 ``prescription.dosage`` **必须**正好是这四个键：
    ``FhirR4.medicationRequest`` 无条件地取 ``frequency`` / ``period`` /
    ``unit`` / ``amount``，少一个就是 NPE，而且异常在患者线程里被 FutureTask
    吞掉 —— 表现为"返回码 0、患者文件少几个"，完全看不出是用法用量的问题。

    ``unit`` 是 UCUM 时间单位（``days`` / ``weeks``），Synthea 会过
    ``convertUcumCode``。``amount`` 是**每次给药的单位数**（1 片、1 支），
    不是规格 —— "0.5g" 那部分属于药品字典的 ``dosage_zh``，不进这里。
    """

    amount: float
    frequency: int
    period: float
    unit: str

    def to_gmf(self) -> dict:
        return {
            "amount": self.amount,
            "frequency": self.frequency,
            "period": self.period,
            "unit": self.unit,
        }


#: Synthea 认得的给药周期单位（UCUM 时间单位）。
#: 与 ``hisgen.ir.models.DosageUnit`` 同一份词表 —— 两边不一致时，
#: IR 写得进、术语表读不出，错误会推到编译期才现形。
DOSAGE_UNITS = frozenset({"days", "weeks", "months", "hours", "minutes", "seconds"})


@dataclass(frozen=True)
class Medication:
    key: str
    name_zh: str
    name_en: str
    code: Code
    dosage_zh: str
    source: str
    #: 结构化用法用量。可以为 None —— 那就**不开**这条医嘱的 prescription.dosage，
    #: 而不是塞一个空壳进去（空壳会让 Synthea 直接 NPE）。
    dosage: Dosage | None = None
    #: 规格，如 ``0.5g``。**FHIR 里没有这个位置** —— ``FhirR4`` 只读
    #: ``as_needed`` / ``dosage{4键}`` / ``instructions`` / ``refills``，
    #: 规格与给药途径都放不进去。它们是药品本身的属性，所以属于这张药品字典，
    #: 由导出层回填 ``医嘱_药品.规格`` / ``.给药途径``。
    spec_zh: str = ""
    #: 给药途径，如 ``口服`` / ``皮下注射``。
    route_zh: str = ""


@dataclass(frozen=True)
class Procedure:
    key: str
    name_zh: str
    name_en: str
    code: Code
    source: str


@dataclass
class Terminology:
    """一套已加载的术语表。"""

    systems: dict[str, System] = field(default_factory=dict)
    diagnoses: dict[str, Code] = field(default_factory=dict)
    labs: dict[str, LabTest] = field(default_factory=dict)
    medications: dict[str, Medication] = field(default_factory=dict)
    procedures: dict[str, Procedure] = field(default_factory=dict)
    #: 加载期发现的问题
    load_warnings: list[str] = field(default_factory=list)
    #: 编译期实际用到的未核实体系
    used_unverified: dict[str, System] = field(default_factory=dict)
    #: 编译期实际用到的兜底码（缺国标码）
    used_caveats: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 加载

    @classmethod
    def load(cls, root: Path | str | None = None) -> "Terminology":
        base = Path(root) if root is not None else TERMINOLOGY_DIR
        if not base.exists():
            raise TerminologyError(f"术语目录不存在：{base}")

        term = cls()

        raw_systems = _read_yaml(base / "systems.yaml", "systems")
        for key, spec in raw_systems.items():
            for required in ("name_zh", "standard", "uri", "verified"):
                if required not in spec:
                    raise TerminologyError(f"systems.yaml: {key} 缺少字段 {required}")
            term.systems[key] = System(
                key=key,
                name_zh=spec["name_zh"],
                standard=spec["standard"],
                uri=spec["uri"],
                verified=bool(spec["verified"]),
                note=(spec.get("note") or "").strip(),
            )

        for key, spec in _read_yaml(base / "diagnoses.yaml", "diagnoses").items():
            term.diagnoses[key] = _build_diagnosis_code(term, key, spec)

        for key, spec in _read_yaml(base / "labs.yaml", "labs").items():
            term.labs[key] = term._build_lab(key, spec)

        for key, spec in _read_yaml(base / "medications.yaml", "medications").items():
            term.medications[key] = _build_medication(term, key, spec)

        for key, spec in _read_yaml(base / "procedures.yaml", "procedures").items():
            term.procedures[key] = _build_procedure(term, key, spec)

        return term

    # ---------------------------------------------------------------- 查表

    def system(self, key: str) -> System:
        try:
            sysdef = self.systems[key]
        except KeyError:
            known = ", ".join(sorted(self.systems))
            raise TerminologyError(f"未知的编码体系 {key!r}。已知：{known}") from None
        self._note_system_use(sysdef)
        return sysdef

    def diagnosis(self, key: str) -> Code:
        try:
            code = self.diagnoses[key]
        except KeyError:
            known = ", ".join(sorted(self.diagnoses))
            raise TerminologyError(
                f"未知的诊断条目 {key!r}。terminology/diagnoses.yaml 中已知：{known}"
            ) from None
        self._note_code_use(code)
        return code

    def lab(self, key: str) -> LabTest:
        try:
            lab = self.labs[key]
        except KeyError:
            known = ", ".join(sorted(self.labs))
            raise TerminologyError(
                f"未知的检验项目 {key!r}。terminology/labs.yaml 中已知：{known}"
            ) from None
        self._note_code_use(lab.code)
        return lab

    def medication(self, key: str) -> Medication:
        try:
            med = self.medications[key]
        except KeyError:
            known = ", ".join(sorted(self.medications))
            raise TerminologyError(
                f"未知的药品 {key!r}。terminology/medications.yaml 中已知：{known}"
            ) from None
        self._note_code_use(med.code)
        return med

    def procedure(self, key: str) -> Procedure:
        try:
            proc = self.procedures[key]
        except KeyError:
            known = ", ".join(sorted(self.procedures))
            raise TerminologyError(
                f"未知的操作项目 {key!r}。terminology/procedures.yaml 中已知：{known}"
            ) from None
        self._note_code_use(proc.code)
        return proc

    def local(self, display: str) -> Code:
        """造一个 LOCAL_ZH 占位码，用于承载中文 display（科室名、照护计划名）。

        ``code`` 就是中文名本身 —— 刻意做得一眼能看出不是真实编码。
        """
        sysdef = self.systems[LOCAL_SYSTEM]
        return Code(
            system=LOCAL_SYSTEM,
            uri=sysdef.uri,
            code=display,
            display=display,
            verified=True,
            source="本轮暂无国标码，使用 LOCAL_ZH 占位",
            caveat=f"{display!r} 无国标码，以 LOCAL_ZH 占位码承载中文 display",
        )

    # ---------------------------------------------------------------- 报告

    def unverified_report(self) -> str:
        """生成本次编译用到的未核实编码体系清单（Markdown）。"""
        if not self.used_unverified and not self.used_caveats:
            return "# 待核实编码体系\n\n本次编译未使用任何未核实的编码体系。\n"

        lines = ["# 待核实编码体系", ""]
        if self.used_unverified:
            lines += [
                "以下编码体系的 URI **尚未确认权威出处**（见设计文档 §11.2），",
                "产物中这些 system 字段是占位符 `urn:hisgen:unverified:*`，",
                "投入生产前必须替换为真实 OID。",
                "",
                "| 体系 | 标准 | 当前 URI | 说明 |",
                "|---|---|---|---|",
            ]
            for sysdef in sorted(self.used_unverified.values(), key=lambda s: s.key):
                note = " ".join(sysdef.note.split())
                lines.append(
                    f"| {sysdef.name_zh} ({sysdef.key}) | {sysdef.standard} "
                    f"| `{sysdef.uri}` | {note} |"
                )
            lines.append("")

        if self.used_caveats:
            lines += [
                "## 缺国标码、已回退到兜底体系的条目",
                "",
                "这些条目在 `terminology/` 里没有填国标码，编译器回退到了",
                "WHO ICD-10 / ATC / LOCAL_ZH。产物里能看出用的是哪个体系，",
                "但若下游要求国标码，需要补齐码表后重新编译。",
                "",
            ]
            for c in sorted(set(self.used_caveats)):
                lines.append(f"- {c}")
            lines.append("")

        return "\n".join(lines)

    # ---------------------------------------------------------------- 内部

    def _note_system_use(self, sysdef: System) -> None:
        if not sysdef.verified:
            self.used_unverified[sysdef.key] = sysdef
        if sysdef.uri.startswith("urn:hisgen:"):
            self.used_unverified.setdefault(sysdef.key, sysdef)

    def _note_code_use(self, code: Code) -> None:
        if not code.verified:
            self.used_unverified.setdefault(
                code.system,
                self.systems.get(code.system)
                or System(code.system, code.system, "", code.uri, False),
            )
        if code.caveat:
            self.used_caveats.append(code.caveat)

    def _build_lab(self, key: str, spec: dict) -> LabTest:
        for required in ("name_zh", "name_en", "loinc", "unit_zh", "reference_range"):
            if required not in spec:
                raise TerminologyError(f"labs.yaml: {key} 缺少字段 {required}")
        rng = spec["reference_range"]
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            raise TerminologyError(f"labs.yaml: {key} 的 reference_range 必须是两个数")
        code = Code(
            system="LOINC",
            uri=self.systems["LOINC"].uri,
            code=str(spec["loinc"]),
            display=spec["name_zh"],
            verified=bool(spec.get("verified", False)),
            source=spec.get("source", ""),
            caveat=(
                ""
                if spec.get("verified")
                else f"检验 {spec['name_zh']} (LOINC {spec['loinc']}) 码值待 LOINC 官方检索复核"
            ),
        )
        return LabTest(
            key=key,
            name_zh=spec["name_zh"],
            name_en=spec["name_en"],
            code=code,
            unit_zh=spec["unit_zh"],
            reference_range=(float(rng[0]), float(rng[1])),
            source=spec.get("source", ""),
        )


# -------------------------------------------------------------------- 构造辅助


def _read_yaml(path: Path, top_key: str) -> dict:
    if not path.exists():
        raise TerminologyError(f"白名单文件不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or top_key not in raw:
        raise TerminologyError(f"{path.name} 顶层必须有一个 {top_key}: 映射")
    body = raw[top_key]
    if not isinstance(body, dict) or not body:
        raise TerminologyError(f"{path.name} 的 {top_key}: 不能为空")
    return body


def _build_diagnosis_code(term: Terminology, key: str, spec: dict) -> Code:
    gb = spec.get("gb14396")
    who = spec.get("icd10_who")
    if not gb and not who:
        raise TerminologyError(
            f"diagnoses.yaml: {key} 既没有 gb14396 也没有 icd10_who，无法产出编码"
        )

    if gb:
        sysdef = term.systems["ICD10_CN"]
        return Code(
            system="ICD10_CN",
            uri=sysdef.uri,
            code=str(gb),
            display=spec["name_zh"],
            verified=bool(spec.get("verified_gb14396", False)),
            source=spec.get("source", ""),
            caveat=(
                ""
                if spec.get("verified_gb14396")
                else f"诊断 {spec['name_zh']} 的 GB/T 14396-2016 码值 {gb} 尚未核对"
            ),
        )

    sysdef = term.systems[FALLBACK_DIAGNOSIS_SYSTEM]
    return Code(
        system=FALLBACK_DIAGNOSIS_SYSTEM,
        uri=sysdef.uri,
        code=str(who),
        display=spec["name_zh"],
        verified=bool(spec.get("verified_who", False)),
        source=spec.get("source", ""),
        caveat=(
            f"诊断 {spec['name_zh']} 缺国标码，回退到 WHO ICD-10 {who}"
            "（非 GB/T 14396-2016 六位码）"
        ),
    )


def _build_medication(term: Terminology, key: str, spec: dict) -> Medication:
    ypid = spec.get("ypid")
    atc = spec.get("atc")
    if not ypid and not atc:
        raise TerminologyError(
            f"medications.yaml: {key} 既没有 ypid 也没有 atc，无法产出编码"
        )

    if ypid:
        sysdef = term.systems["YPID"]
        verified = bool(spec.get("verified_ypid", False))
        code = Code(
            system="YPID",
            uri=sysdef.uri,
            code=str(ypid),
            display=spec["name_zh"],
            verified=verified,
            source=spec.get("source", ""),
            # 只有真的没核对过才提示 —— 填了 verified_ypid 就不该再报
            caveat=(
                "" if verified else f"药品 {spec['name_zh']} 的 YPID 码值 {ypid} 尚未核对"
            ),
        )
    else:
        sysdef = term.systems["ATC"]
        code = Code(
            system="ATC",
            uri=sysdef.uri,
            code=str(atc),
            display=spec["name_zh"],
            verified=bool(spec.get("verified_atc", True)),
            source=spec.get("source", ""),
            caveat=(
                f"药品 {spec['name_zh']} 缺 YPID 码，回退到 WHO ATC {atc}"
                "（非 WS/T 778-2021 码）"
            ),
        )

    return Medication(
        key=key,
        name_zh=spec["name_zh"],
        name_en=spec.get("name_en", ""),
        code=code,
        dosage_zh=spec.get("default_dosage_zh", ""),
        source=spec.get("source", ""),
        dosage=_build_dosage(spec.get("default_dosage"), spec["name_zh"]),
        spec_zh=str(spec.get("spec_zh") or "").strip(),
        route_zh=str(spec.get("route_zh") or "").strip(),
    )


def _build_dosage(spec: object, name_zh: str) -> Dosage | None:
    """解析 ``default_dosage``。缺失就是 None（不开 structured dosage）。"""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise TerminologyError(f"medications.yaml: {name_zh} 的 default_dosage 必须是映射")

    missing = [k for k in ("amount", "frequency", "period", "unit") if k not in spec]
    if missing:
        # 缺键在 Synthea 那边是 NPE，在这里必须挡住 —— 否则错误现场离原因太远
        raise TerminologyError(
            f"medications.yaml: {name_zh} 的 default_dosage 缺字段 {missing}；"
            "Synthea 的 prescription.dosage 要求 amount/frequency/period/unit 齐全"
        )

    unit = str(spec["unit"])
    if unit not in DOSAGE_UNITS:
        raise TerminologyError(
            f"medications.yaml: {name_zh} 的 default_dosage.unit={unit!r} 不是已知的 "
            f"给药周期单位（可用：{'、'.join(sorted(DOSAGE_UNITS))}）"
        )
    frequency = int(spec["frequency"])
    if frequency <= 0:
        raise TerminologyError(f"medications.yaml: {name_zh} 的 frequency 必须为正")

    return Dosage(
        amount=float(spec["amount"]),
        frequency=frequency,
        period=float(spec["period"]),
        unit=unit,
    )


def _build_procedure(term: Terminology, key: str, spec: dict) -> Procedure:
    icd9 = spec.get("icd9cm3")
    if icd9:
        sysdef = term.systems["ICD9CM3"]
        verified = bool(spec.get("verified_icd9cm3", False))
        code = Code(
            system="ICD9CM3",
            uri=sysdef.uri,
            code=str(icd9),
            display=spec["name_zh"],
            verified=verified,
            source=spec.get("source", ""),
            caveat=(
                ""
                if verified
                else f"操作 {spec['name_zh']} 的 ICD-9-CM-3 码值 {icd9} 尚未核对"
            ),
        )
    else:
        sysdef = term.systems[LOCAL_SYSTEM]
        code = Code(
            system=LOCAL_SYSTEM,
            uri=sysdef.uri,
            code=spec["name_zh"],
            display=spec["name_zh"],
            verified=True,
            source=spec.get("source", ""),
            caveat=f"操作 {spec['name_zh']} 无 ICD-9-CM-3 码，以 LOCAL_ZH 占位码承载中文 display",
        )

    return Procedure(
        key=key,
        name_zh=spec["name_zh"],
        name_en=spec.get("name_en", ""),
        code=code,
        source=spec.get("source", ""),
    )