"""FHIR -> 8 张关系型表。

设计文档 §9 的表结构，一字不改地实现。每张表是一个函数，输入
:class:`~hisgen.export.loader.FhirData`，输出 ``(表头, 行)``。

几条贯穿全表的规矩：

* **行序稳定**：所有表都排序。同样的输入必须导出同样的文件，
  否则下游做增量加载时无法用 diff 判断变化。
* **外键用 Synthea 的原生 UUID**，不重新编号。这样 就诊ID / 诊断ID
  在不同批次之间天然不冲突，也不会因为重新编号而错位。
* **取不到的字段留空**，不填占位符。空值是"没有"，占位符是"有但不真"，
  两者在下游的含义完全不同。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import fields as F
from .derive import Deriver
from .loader import FhirData
from .lookups import Lookups


@dataclass
class Table:
    name: str
    headers: list[str]
    rows: list[list[str]]


#: 身份证号可能落在哪些 identifier system 上，按优先级。
#:
#: Synthea 原版只写 SSN 体系（``us-ssn``）；打上 Java 补丁后应当写第一个。
#: 导出层不靠 URI 判断，靠 GB 11643 校验位判断（见 Deriver.national_id），
#: 所以就算补丁改了 URI 也不用同步改这里。
NATIONAL_ID_SYSTEMS: tuple[str, ...] = (
    "urn:hisgen:unverified:CN_ID",
    "http://hl7.org/fhir/sid/us-ssn",
    "https://github.com/synthetichealth/synthea",
)


@dataclass
class Ctx:
    """导出上下文：FHIR 数据 + 派生器 + 映射表。"""

    data: FhirData
    derive: Deriver
    lookups: Lookups
    #: 是否对身份证号脱敏。默认脱敏 —— 先假设下游不该看到原值。
    mask_national_id: bool = True
    #: 身份证号候选 identifier system
    national_id_systems: tuple[str, ...] = NATIONAL_ID_SYSTEMS

    def __post_init__(self) -> None:
        self._encounter_costs: dict[str, tuple[float, float, float]] | None = None
        self._encounter_insurer: dict[str, str] = {}
        #: MedicationRequest id -> 该条医嘱的金额（元）
        self._med_amount: dict[str, float] = {}
        self._encounters_of_patient: dict[str, list[dict]] = {}
        self._encounter_order: dict[str, int] = {}
        self._by_encounter: dict[str, dict[str, list[dict]]] = {}
        self._practitioner_org: dict[str, str] = {}
        self._practitioner_dept: dict[str, str] = {}
        self._index()

    # ------------------------------------------------------------ 建索引

    def _index(self) -> None:
        """一次遍历建好所有需要的索引，避免每张表各扫一遍。

        费用与承保方在这里一次算完 —— 否则每张表、每个患者都去重扫
        全部 Claim / EOB，100 人份的数据就会退化成平方复杂度。
        """
        encounters = self.data.get("Encounter")
        # 就诊的先后顺序用 period.start 排，缺时间的排最后
        encounters_sorted = sorted(
            encounters,
            key=lambda e: (
                F.period_start(e.get("period")) or "9999",
                F.s(e.get("id")),
            ),
        )
        for i, enc in enumerate(encounters_sorted):
            eid = F.s(enc.get("id"))
            self._encounter_order[eid] = i
            pid = self.data.ref_id(F.obj(enc.get("subject")).get("reference"))
            self._encounters_of_patient.setdefault(pid, []).append(enc)

        self._index_clinicians(encounters, encounters_sorted)

        # 各临床资源按 encounter 分桶
        for rtype in ("Condition", "MedicationRequest", "Observation", "Procedure"):
            for res in self.data.get(rtype):
                eid = self.data.ref_id(F.obj(res.get("encounter")).get("reference"))
                bucket = self._by_encounter.setdefault(
                    rtype, {}
                )
                bucket.setdefault(eid, []).append(res)

        # EOB 先按它挂的 Claim 建索引 —— 处方单的**结算拆分**（医保支付/自付）
        # 只写在 EOB 上，Claim 那边只有一个光秃秃的 total。
        eob_of_claim: dict[str, dict] = {}
        for res in self.data.get("ExplanationOfBenefit"):
            cid = self.data.ref_id(_claim_of(res))
            if cid:
                eob_of_claim.setdefault(cid, res)

        # 药品处方单要先单独拎出来 —— 见 _is_pharmacy 的说明。
        # 一张处方单的钱既挂到那条医嘱上，也并进它所属的就诊。
        pharmacy_claims: set[str] = set()
        drug_amount: dict[str, float] = {}
        drug_cost: dict[str, tuple[float, float, float]] = {}
        for res in self.data.get("Claim"):
            if not _is_pharmacy(res):
                continue
            cid = F.s(res.get("id"))
            if cid:
                pharmacy_claims.add(cid)
            # 金额两边一致，但拆分只在 EOB 上 —— 照样「EOB 优先」
            amounts = _amounts(eob_of_claim.get(cid) or res)
            med_id = self.data.ref_id(
                F.obj(res.get("prescription")).get("reference")
            )
            if med_id:
                drug_amount[med_id] = amounts[0]
            for eid in _encounters_in_claim(self.data, res):
                drug_cost[eid] = _plus(drug_cost.get(eid, ZERO_AMOUNTS), amounts)

        # 就诊本身的结算：同样先 Claim 后 EOB，实现「EOB 优先」。
        # 药品处方单在这里**必须跳过**：它的 EOB 也在 data 里，不跳的话
        # 那 0.30 元的药费会盖掉整次就诊的费用（遍历顺序碰巧让它排后面时），
        # 或者被累加两遍。
        costs: dict[str, tuple[float, float, float]] = {}
        for rtype in ("Claim", "ExplanationOfBenefit"):
            for res in self.data.get(rtype):
                if _is_pharmacy(res) or (
                    self.data.ref_id(_claim_of(res)) in pharmacy_claims
                ):
                    continue
                insurer = _insurer_name(res)
                amounts = _amounts(res)
                for eid in _encounters_in_claim(self.data, res):
                    costs[eid] = amounts
                    if insurer:
                        self._encounter_insurer[eid] = insurer

        self._med_amount = drug_amount
        merged = {
            eid: _plus(amounts, drug_cost.get(eid, ZERO_AMOUNTS))
            for eid, amounts in costs.items()
        }
        # 有药费却没找到就诊结算单的就诊（罕见）也不能把药费丢了
        for eid, amounts in drug_cost.items():
            merged.setdefault(eid, amounts)
        self._encounter_costs = merged

    def _index_clinicians(
        self, encounters: list[dict], ordered: list[dict]
    ) -> None:
        """医生 -> 所属机构 / 科室，从就诊**投票**推出来。

        为什么要推：``PractitionerRole`` 只在 ``use_us_core_ig = true`` 时才产出，
        而且它的 ``specialty`` 在 ``FhirR4.java`` 里是**写死的**
        （``208D00000X / General Practice Physician``），开了也拿不到真科室。
        但 Synthea 生成就诊时本来就先挑机构、再从该机构名下的医生表里挑人
        （``Provider.chooseClinicianList``），所以「同一个医生反复出现在哪家机构、
        哪个科室的就诊上」就是那份真实归属的**投影**，不是猜。

        投票按就诊时间排序做，平票时先出现的赢 —— 保证同一份输入永远同一结果。
        """
        org_votes: dict[str, dict[str, int]] = {}
        dept_votes: dict[str, dict[str, int]] = {}

        for enc in ordered:
            org = self.data.ref_id(
                F.obj(enc.get("serviceProvider")).get("reference")
            )
            dept = self.lookups.dept_zh(
                F.code_display(F.first(enc.get("type")))
            )
            for part in F.arr(enc.get("participant")):
                doc = self.data.ref_id(
                    F.obj(F.obj(part).get("individual")).get("reference")
                )
                if not doc:
                    continue
                if org:
                    org_votes.setdefault(doc, {})
                    org_votes[doc][org] = org_votes[doc].get(org, 0) + 1
                if dept:
                    dept_votes.setdefault(doc, {})
                    dept_votes[doc][dept] = dept_votes[doc].get(dept, 0) + 1

        self._practitioner_org = {k: _top(v) for k, v in org_votes.items()}
        self._practitioner_dept = {k: _top(v) for k, v in dept_votes.items()}

    def org_of_practitioner(self, practitioner_id: str) -> str:
        """医生所属机构。取不到就返回空串 —— 不编一个。"""
        return self._practitioner_org.get(practitioner_id, "")

    def bucket(self, resource_type: str, encounter_id: str) -> list[dict]:
        return self._by_encounter.get(resource_type, {}).get(encounter_id, [])

    def encounters_of(self, patient_id: str) -> list[dict]:
        return self._encounters_of_patient.get(patient_id, [])

    def order_of(self, encounter_id: str) -> int:
        return self._encounter_order.get(encounter_id, 1 << 30)

    # ------------------------------------------------------------ 费用

    def cost_of(self, encounter_id: str) -> tuple[float, float, float]:
        """某次就诊的 ``(总费用, 医保支付, 个人自付)``。

        Synthea 对每次就诊同时产出 ``Claim`` 和 ``ExplanationOfBenefit``，
        两者金额相同 —— **只认 EOB**，两个都加会翻倍。EOB 是结算结果，
        语义上也更贴「医保结算表」。没有 EOB 才退回 Claim。

        **药费已并入**。Synthea 把药品单独开成一张处方单，但它把那张单的
        ``billablePeriod`` 设成这次就诊的 period、``item[].encounter`` 也指向
        这次就诊 —— 药费属于这次就诊本来就是 Synthea 的语义。不并的话，
        ``医嘱_药品.金额`` 之和与 ``就诊.总费用`` 对不上账，下游做费用归集时
        药费会凭空消失；而 2 型糖尿病门诊里药费恰恰是大头。
        """
        return (self._encounter_costs or {}).get(
            encounter_id, (0.0, 0.0, 0.0)
        )

    def insurer_of(self, encounter_id: str) -> str:
        """该次就诊的承保方名称（= 中文医保名）。"""
        return self._encounter_insurer.get(encounter_id, "")

    def amount_of_medication(self, medication_request_id: str) -> float:
        """一条医嘱的**金额**。

        取自该医嘱对应的药品处方单（``Claim.type = pharmacy``）的 ``total``。
        没有对应处方单就给 0，**不猜**。
        """
        return self._med_amount.get(medication_request_id, 0.0)

    def dept_of_practitioner(self, practitioner_id: str) -> str:
        """医生科室。取不到给空串，由调用方决定要不要兜底。"""
        return self._practitioner_dept.get(practitioner_id, "")


#: 金额三元组 ``(总费用, 医保支付, 个人自付)`` 的零元。
ZERO_AMOUNTS: tuple[float, float, float] = (0.0, 0.0, 0.0)

#: Synthea 写的药品处方单类型码（``FhirR4.medicationClaim``）。
PHARMACY_CLAIM_TYPE = "pharmacy"


def _plus(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    """两个金额三元组相加 —— 药费要并进它所属那次就诊的费用里。"""
    return (round(a[0] + b[0], 2), round(a[1] + b[1], 2), round(a[2] + b[2], 2))


def _is_pharmacy(res: dict) -> bool:
    """这张 Claim 是不是**药品处方单**。

    Synthea 不把药费算进就诊的 Claim，``HealthRecord.medicationStart`` 里
    写得明白（原文注释）："Do not add medications to the Encounter claim.
    Medications submit separate claims."。于是每条 ``MedicationRequest``
    都对应一张独立的 ``type = pharmacy`` 的 Claim（``FhirR4.java:1182``），
    由 ``prescription`` 指回去，钱在它的 ``total`` 上。

    不认这一条，医嘱的金额就全是 0.00 —— 因为**就诊的 EOB 里根本没有
    药品行**，把 item 翻遍也找不到，早先那版就是这么错的。
    """
    return F.code_value(F.first(res.get("type"))) == PHARMACY_CLAIM_TYPE


def _claim_of(res: dict) -> str:
    """这个资源（EOB）挂在哪张 Claim 上，返回**未解析的引用串**。

    ``EOB.claim`` 是 ``{reference: "urn:uuid:..."}``，没有斜杠 ——
    调用方要拿它比 id 的话得先过一遍 :meth:`FhirData.ref_id`。
    """
    return F.s(F.obj(res.get("claim")).get("reference"))


def _top(votes: dict[str, int]) -> str:
    """得票最高的键；平票时取**先出现**的（导入顺序稳定，结果就稳定）。"""
    best, best_n = "", -1
    for key, n in votes.items():
        if n > best_n:
            best, best_n = key, n
    return best


#: Synthea 对「这个患者此刻没有保险」写的哨兵值（``Payer.NO_INSURANCE``）。
#: 它不是一种医保类型，不能当承保方名写进产物。
NO_INSURANCE = "NO_INSURANCE"


def _insurer_name(res: dict) -> str:
    """承保方名称（= 中文医保名）。取不到（含未参保）给空串。

    ``ExplanationOfBenefit.insurer`` 是 ``{"display": "上海市城镇职工基本医疗保险基金"}``，
    没有 coding，``code_display`` 认这个形态。``Claim`` 没有顶层 ``insurer``，
    参保信息在 ``insurance[].coverage.display`` 上 —— 两条路都要走，
    否则 患者.医保类型 整列为空。

    未参保时 Synthea 写 ``NO_INSURANCE``；那是它内部的哨兵，不是一种险种。
    不滤掉的话，中途断保的患者会得到
    「上海市城镇职工基本医疗保险基金；NO_INSURANCE」这种四不像的医保类型。
    """
    name = F.code_display(res.get("insurer"))
    if not name:
        for entry in F.arr(res.get("insurance")):
            coverage = F.obj(F.obj(entry).get("coverage"))
            name = F.s(coverage.get("display")) or F.code_display(coverage)
            if name:
                break
    return "" if name == NO_INSURANCE else name


def _encounters_in_claim(data: FhirData, res: dict) -> list[str]:
    """从 Claim / EOB 里找出它对应的就诊 id。

    Synthea 把就诊引用放在 ``item[].encounter[]`` 上，顶层偶尔也有。
    **必须走 ``data.ref_id``**：这里的引用是 ``urn:uuid:...``，
    按 ``/`` 切分切出来的永远为空，于是所有就诊的费用都是 0.00。
    """
    ids: list[str] = []
    for item in F.arr(res.get("item")):
        for ref in F.arr(F.obj(item).get("encounter")):
            rid = data.ref_id(F.s(F.obj(ref).get("reference")))
            if rid:
                ids.append(rid)
    for ref in F.arr(res.get("encounter")):
        rid = data.ref_id(F.s(F.obj(ref).get("reference")))
        if rid:
            ids.append(rid)
    # 去重但保序
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _amounts(res: dict) -> tuple[float, float, float]:
    """``(总费用, 医保支付, 个人自付)``。

    Synthea 的 EOB 把金额放在两处，两处的形状还不一样：

    * ``total`` 是**列表** ``[{category: {text: "Submitted Amount"}, amount: ...}]``，
      不是单个 Money —— 直接 ``money_value(total)`` 会拿到 0
    * ``item[].adjudication[]`` 的 ``category.coding[0].code`` 是 Blue Button 的
      **完整 URL**（``https://bluebutton.cms.gov/resources/variables/line_prvdr_pmt_amt``），
      不是 ``benefit`` / ``copay`` 这种短码
    """
    items = F.arr(res.get("item"))

    total = _total_of(res)
    if not total:
        total = sum(F.money_value(F.obj(it).get("net")) for it in items)

    submitted = benefit = deductible = coinsurance = 0.0
    # 顶层 + 各 item 的 adjudication 都要累加
    for container in [res, *[F.obj(it) for it in items]]:
        for adj in F.arr(container.get("adjudication")):
            a = F.obj(adj)
            code = F.adjudication_code(a.get("category"))
            amount = F.money_value(a.get("amount"))
            if code in F.ADJUDICATION_SUBMITTED:
                submitted += amount
            elif code in F.ADJUDICATION_BENEFIT:
                benefit += amount
            elif code in F.ADJUDICATION_COPAY:
                coinsurance += amount
            elif code in F.ADJUDICATION_DEDUCTIBLE:
                deductible += amount

    if benefit == 0.0:
        # EOB 的医保支付写在 payment 上
        benefit = F.money_value(res.get("payment"))
    if not total:
        total = submitted

    return round(total, 2), round(benefit, 2), round(coinsurance + deductible, 2)


def _total_of(res: dict) -> float:
    """总额。**两种形状都要认**，Claim 与 EOB 在 R4 里就不一样：

    * ``Claim.total`` 是单个 ``Money`` —— ``{"value": 999.0}``
    * ``ExplanationOfBenefit.total`` 是列表
      —— ``[{category: {text: "Submitted Amount"}, amount: {"value": 300}}]``
    """
    entries = F.arr(res.get("total"))
    if not entries:
        return F.money_value(res.get("total"))
    for entry in entries:
        e = F.obj(entry)
        if F.adjudication_code(e.get("category")) in F.ADJUDICATION_SUBMITTED:
            return F.money_value(e.get("amount"))
    return sum(F.money_value(F.obj(e).get("amount")) for e in entries)


# --------------------------------------------------------------------- 患者


def patients(ctx: Ctx) -> Table:
    headers = [
        "patient_id",
        "姓名",
        "性别",
        "出生日期",
        "身份证号",
        "手机号",
        "民族",
        "医保类型",
        "医保号",
        "居住地址",
        "区县",
        "邮编",
    ]
    rows: list[list[str]] = []
    for p in sorted(ctx.data.get("Patient"), key=lambda x: F.s(x.get("id"))):
        pid = F.s(p.get("id"))
        address = F.obj(F.first(p.get("address")))
        district = F.s(address.get("city"))
        rows.append(
            [
                pid,
                F.human_name(F.first(p.get("name"))),
                _gender_zh(F.s(p.get("gender"))),
                F.date_of(p.get("birthDate")),
                _national_id(ctx, p),
                F.first_phone(p),
                ctx.derive.ethnicity(pid),
                _insurance_name(ctx, pid),
                ctx.derive.insurance_id(pid, district),
                _full_address(address),
                district,
                F.s(address.get("postalCode")),
            ]
        )
    return Table("患者", headers, rows)


def _gender_zh(gender: str) -> str:
    return {"male": "男", "female": "女", "other": "其他", "unknown": ""}.get(
        gender, gender
    )


def _full_address(address: dict) -> str:
    """完整地址。

    Synthea 把**完整地址**（含省市）写在 ``line`` 里，``city`` / ``state``
    只是从里面拆出来的副本：

        line: ["上海市宝山区长寿支路182弄23号701号"], city: "宝山区", state: "上海市"

    所以无条件拼接会得到「上海市宝山区上海市宝山区长寿支路…」。

    规则：``line`` 只要已经含 ``city`` 或 ``state`` 里的任一个，就说明
    它自带前缀部分，**原样返回**；两个都不含时才补前缀。
    不能靠拼 ``state`` 兜底 —— 机构的 ``state`` 是 ``Provider.state`` 的
    **缩写码** ``"SH"``（不是「上海市」），拼进去就是
    「SH上海市普陀区宛平南路414号」。:func:`_region` 负责把这种码丢掉。
    """
    line = F.address_lines(address)
    state, city = _region(address)
    if not line:
        return f"{state}{city}"
    if (city and city in line) or (state and state in line):
        return line
    return f"{state}{city}{line}"


def _region(address: dict) -> tuple[str, str]:
    """``(省/市, 区县)`` 前缀。

    ``state`` 若是 ``SH`` 这种两三个 ASCII 字母的 USPS 缩写码，说明它
    不是地名（患者的 ``state`` 才是「上海市」），丢掉不拼 —— 宁可少一段
    前缀，也不要在地址里写个 ``SH``。
    """
    state = F.s(address.get("state"))
    city = F.s(address.get("city"))
    if state and state.isascii() and state.isalpha() and len(state) <= 3:
        state = ""
    return state, city


def _national_id(ctx: Ctx, patient: dict) -> str:
    """身份证号，从候选 identifier 里挑出通过 GB 11643 校验的那个。

    校验不过就留空 —— 详见 :meth:`Deriver.national_id`。
    """
    candidates = [
        F.identifier_by_system(patient, system)
        for system in ctx.national_id_systems
    ]
    value = ctx.derive.national_id(candidates)
    if not value:
        return ""
    return ctx.derive.mask_national_id(value) if ctx.mask_national_id else value


def _insurance_name(ctx: Ctx, patient_id: str) -> str:
    """医保类型 = 该患者所有就诊的承保方名称（我们替换过的中文医保名）。

    正常情况下一个患者只有一种参保类型，取到第一个就够了 —— 但这里仍然
    把出现过的都列出来，万一中途换了参保类型（如职工转居民），
    单值会静默丢信息。
    """
    names: list[str] = []
    for enc in ctx.encounters_of(patient_id):
        name = ctx.insurer_of(F.s(enc.get("id")))
        if name and name not in names:
            names.append(name)
    return "；".join(names)


# --------------------------------------------------------------------- 机构


def organizations(ctx: Ctx) -> Table:
    headers = ["机构ID", "机构名称", "等级", "类型", "地址", "区县", "电话"]
    rows: list[list[str]] = []
    for org in sorted(ctx.data.get("Organization"), key=lambda x: F.s(x.get("id"))):
        name = F.s(org.get("name"))
        address = F.obj(F.first(org.get("address")))
        rows.append(
            [
                F.s(org.get("id")),
                name,
                ctx.lookups.org_level(name),
                ctx.lookups.org_type(name) or F.code_display(F.first(org.get("type"))),
                _full_address(address),
                F.s(address.get("city")),
                _first_contact(org),
            ]
        )
    return Table("机构", headers, rows)


def _first_contact(res: dict) -> str:
    for c in F.arr(res.get("telecom")):
        value = F.s(F.obj(c).get("value"))
        if value:
            return value
    return ""


# --------------------------------------------------------------------- 医生


def practitioners(ctx: Ctx) -> Table:
    """医生表。

    ``科室`` 与 ``所属机构ID`` **不能从 PractitionerRole 取** ——
    那个资源只在 ``use_us_core_ig = true`` 时才产出，而且 ``specialty`` 是写死的。
    这里从就诊反推（见 :meth:`Ctx._index_clinicians`）：

    * ``所属机构ID`` —— 该医生出诊次数最多的那家机构，是 Synthea
      挑选医生时那份归属的投影
    * ``科室`` —— 该医生出诊次数最多的科室；一次就诊都没有的医生
      （Synthea 的 ``Provider`` 会生成用不上的医生）按 ``practitioner_id``
      确定性派生一个，写进 ``Deriver.department`` 的说明
    """
    headers = ["医生ID", "姓名", "性别", "职称", "科室", "所属机构ID"]
    rows: list[list[str]] = []
    fallback_depts = ctx.lookups.department_names()

    for prac in sorted(ctx.data.get("Practitioner"), key=lambda x: F.s(x.get("id"))):
        pid = F.s(prac.get("id"))
        dept = ctx.dept_of_practitioner(pid)
        if not dept and fallback_depts:
            dept = ctx.derive.department(pid, fallback_depts)
        rows.append(
            [
                pid,
                F.human_name(F.first(prac.get("name"))),
                _gender_zh(F.s(prac.get("gender"))),
                ctx.derive.title(pid),
                dept,
                ctx.org_of_practitioner(pid),
            ]
        )
    return Table("医生", headers, rows)


# --------------------------------------------------------------------- 就诊


def encounters(ctx: Ctx) -> Table:
    headers = [
        "就诊ID",
        "patient_id",
        "就诊类型",
        "科室",
        "机构ID",
        "医生ID",
        "开始时间",
        "结束时间",
        "就诊原因",
        "总费用",
        "医保支付",
        "个人自付",
    ]
    rows: list[list[str]] = []

    ordered = sorted(
        ctx.data.get("Encounter"),
        key=lambda e: (
            F.period_start(e.get("period")) or "9999",
            F.s(e.get("id")),
        ),
    )
    for enc in ordered:
        eid = F.s(enc.get("id"))
        cls = F.code_value(enc.get("class")) or F.s(enc.get("class"))
        total, benefit, self_pay = ctx.cost_of(eid)
        rows.append(
            [
                eid,
                ctx.data.ref_id(F.obj(enc.get("subject")).get("reference")),
                F.ENCOUNTER_CLASS_ZH.get(cls.upper(), cls),
                F.code_display(F.first(enc.get("type"))),
                ctx.data.ref_id(
                    F.obj(enc.get("serviceProvider")).get("reference")
                ),
                _encounter_doctor(ctx, enc),
                F.period_start(enc.get("period")),
                F.period_end(enc.get("period")),
                F.code_display(F.first(enc.get("reasonCode"))),
                _money(total),
                _money(benefit),
                _money(self_pay),
            ]
        )
    return Table("就诊", headers, rows)


def _encounter_doctor(ctx: Ctx, enc: dict) -> str:
    for part in F.arr(enc.get("participant")):
        rid = ctx.data.ref_id(
            F.obj(F.obj(part).get("individual")).get("reference")
        )
        if rid:
            return rid
    return ""


def _money(value: float) -> str:
    return f"{value:.2f}"


# --------------------------------------------------------------------- 诊断


def diagnoses(ctx: Ctx) -> Table:
    headers = [
        "诊断ID",
        "就诊ID",
        "编码体系",
        "诊断编码",
        "诊断名称",
        "诊断类型",
        "确诊时间",
    ]
    rows: list[list[str]] = []
    for cond in sorted(
        ctx.data.get("Condition"),
        key=lambda c: (
            F.date_of(c.get("onsetDateTime") or c.get("recordedDate")) or "9999",
            F.s(c.get("id")),
        ),
    ):
        system, code, display = F.code_triple(cond.get("code"))
        if system.endswith("/icd-10") or "icd-10" in system:
            kind = "主要诊断"
        else:
            kind = "其他诊断"
        enc_id = ctx.data.ref_id(F.obj(cond.get("encounter")).get("reference"))
        rows.append(
            [
                F.s(cond.get("id")),
                enc_id,
                ctx.lookups.system_label(system),
                code,
                display,
                kind,
                F.datetime_of(cond.get("onsetDateTime") or cond.get("recordedDate")),
            ]
        )
    return Table("诊断", headers, rows)


# ----------------------------------------------------------------- 医嘱_药品


def medication_orders(ctx: Ctx) -> Table:
    headers = [
        "医嘱ID",
        "就诊ID",
        "药品编码",
        "药品通用名",
        "规格",
        "剂量",
        "频次",
        "给药途径",
        "开始时间",
        "数量",
        "单价",
        "金额",
    ]
    rows: list[list[str]] = []
    for med in sorted(
        ctx.data.get("MedicationRequest"),
        key=lambda m: (F.s(m.get("authoredOn")), F.s(m.get("id"))),
    ):
        enc_id = ctx.data.ref_id(F.obj(med.get("encounter")).get("reference"))
        _system, code, display = F.code_triple(med.get("medicationCodeableConcept"))
        quantity, unit_price, amount = _med_cost(ctx, med)
        instruction = F.obj(F.first(med.get("dosageInstruction")))
        dose, freq = _dosage_text(instruction)

        # 规格与给药途径在 FHIR 里没有位置（FhirR4 的 medicationRequest 只读
        # as_needed / dosage / instructions / refills），从药品字典回填。
        spec = ctx.lookups.drug(code)
        rows.append(
            [
                F.s(med.get("id")),
                enc_id,
                code,
                display,
                spec.spec_zh if spec else "",
                dose,
                freq,
                spec.route_zh if spec else "",
                F.datetime_of(med.get("authoredOn")),
                quantity,
                unit_price,
                amount,
            ]
        )
    return Table("医嘱_药品", headers, rows)


#: 给药周期单位（UCUM）-> 中文。Synthea 写的是 ``periodUnit: "d"``。
_PERIOD_UNIT_ZH = {
    "d": "日",
    "day": "日",
    "days": "日",
    "wk": "周",
    "week": "周",
    "weeks": "周",
    "mo": "月",
    "month": "月",
    "months": "月",
    "h": "小时",
    "hr": "小时",
    "hours": "小时",
}


def _dosage_text(instruction: dict) -> tuple[str, str]:
    """从 ``dosageInstruction[0]`` 取 ``(剂量, 频次)``。

    读的是**结构**，不是文本：

        timing.repeat: {frequency: 2, period: 1.0, periodUnit: "d"}
        doseAndRate[0].doseQuantity: {value: 1.0}

    ``doseQuantity`` 只有 ``value`` 没有 ``unit`` —— Synthea 把它当"每次
    几个最小给药单位"，真正的规格在药品字典里。所以这里出的是 ``1``
    而不是 ``0.5g``，两者不要混。

    结构读不出来才退回 ``timing.code.text``（Synthea 有时会带一行英文描述）。
    """
    repeat = F.obj(instruction.get("timing")).get("repeat")
    r = F.obj(repeat)
    freq_n = F.num(r.get("frequency"))
    period = F.num(r.get("period"))
    unit = _PERIOD_UNIT_ZH.get(F.s(r.get("periodUnit")).lower(), "")

    freq = ""
    if freq_n and unit:
        period_text = "" if period == 1 else f"{period:g}"
        freq = f"每{period_text}{unit}{freq_n:g}次"

    dose_qty = F.obj(F.first(F.arr(instruction.get("doseAndRate")))).get(
        "doseQuantity"
    )
    dose = F.quantity_value(dose_qty)

    if not freq:
        # 退回文本：Synthea 的 timing.code.text 形如 "q2d" / "Daily"
        freq = F.code_display(F.obj(instruction.get("timing")).get("code"))
    return dose, freq


def _med_cost(ctx: Ctx, med: dict) -> tuple[str, str, str]:
    """一条医嘱的 ``(数量, 单价, 金额)``。

    **只有金额有出处**，取自该医嘱对应的药品处方单（``Claim.type =
    pharmacy``，由 ``prescription`` 指回来）的 ``total``。Synthea 的药品成本是
    「一张处方一个总额」（``Costs.determineCostOfEntry`` 按 Medication 条目计），
    而且 ``MedicationRequest`` 里没有 ``dispenseRequest``、没有包装数量 ——
    「数量」和「单价」在合成数据里**没有出处**，所以留空。

    留空是**刻意的**：以前这三列一起写 0.00，读起来像「这个药不要钱」，
    比空值危险得多；按「一张处方」硬填数量 1 再除出单价，则是把一个
    约定的假设冒充成测量值。要开药价就改
    ``generate.costs.default_medication_cost``（见 ``shanghai/shanghai.properties``），
    那是 Synthea 唯一的药品计价入口。
    """
    amount = _money(ctx.amount_of_medication(F.s(med.get("id"))))
    return "", "", amount


#: 给药途径词表。与 ``terminology/medications.yaml`` 里 dosage 字符串的写法一致。
ROUTE_WORDS = (
    "口服",
    "皮下注射",
    "静脉注射",
    "静脉滴注",
    "肌肉注射",
    "外用",
    "舌下含服",
    "吸入",
    "眼用",
)

#: 频次词的起始字。``medications.yaml`` 里的频次一律以「每」开头。
FREQ_PREFIX = "每"


def split_dosage(text: str) -> tuple[str, str, str]:
    """把 ``"口服 0.5g 每日2次，随第一口主食嚼服"`` 拆成
    ``("口服", "0.5g", "每日2次")``。

    这些字符串是**我们自己**在 ``medications.yaml`` 里写的，格式可控，
    所以拆得动；不是去猜自由文本。拆不出来时整串进「剂量」列，
    另外两列留空 —— 信息不丢，只是没结构化。
    """
    text = (text or "").strip()
    if not text:
        return "", "", ""

    route = ""
    rest = text
    for word in ROUTE_WORDS:
        if rest.startswith(word):
            route = word
            rest = rest[len(word):].strip()
            break

    # 备注以中文逗号分隔，去掉它，剩下的才是「剂量 + 频次」
    for sep in ("，", ","):
        if sep in rest:
            rest = rest.split(sep, 1)[0].strip()
            break

    parts = rest.split()
    for i, token in enumerate(parts):
        if token.startswith(FREQ_PREFIX):
            return route, " ".join(parts[:i]).strip(), token
    return route, " ".join(parts).strip(), ""


# --------------------------------------------------------------------- 检验


def lab_results(ctx: Ctx) -> Table:
    headers = [
        "检验ID",
        "就诊ID",
        "项目编码",
        "项目名称",
        "结果值",
        "单位",
        "参考区间",
        "异常标志",
        "采样时间",
        "报告时间",
    ]
    rows: list[list[str]] = []
    for obs in sorted(
        ctx.data.get("Observation"),
        key=lambda o: (F.s(o.get("effectiveDateTime")), F.s(o.get("id"))),
    ):
        _system, code, display = F.code_triple(obs.get("code"))
        value = F.quantity_value(obs.get("valueQuantity")) or F.s(
            obs.get("valueString")
        )
        unit = F.quantity_unit(obs.get("valueQuantity"))
        low, high = _reference_range(ctx, obs, code)
        rows.append(
            [
                F.s(obs.get("id")),
                ctx.data.ref_id(F.obj(obs.get("encounter")).get("reference")),
                code,
                display,
                value,
                unit,
                _range_text(low, high),
                _abnormal_flag(value, low, high),
                F.datetime_of(obs.get("effectiveDateTime")),
                F.datetime_of(obs.get("issued")),
            ]
        )
    return Table("检验", headers, rows)


def _reference_range(
    ctx: Ctx, obs: dict, code: str
) -> tuple[float | None, float | None]:
    """参考区间。

    设计文档 §9.1 写的是取 ``Observation.referenceRange``，但**Synthea 不产出
    这个字段**（实测 1276 个 Observation 里一个都没有），照着写会让整列留空。
    真正的出处是 ``terminology/labs.yaml`` 的 ``reference_range`` —— 那是我们
    自己带着 ``source`` 写的白名单，比 FHIR 里有没有更靠得住。

    FHIR 里**恰好有** ``referenceRange`` 时仍然优先用它：那是这一份数据自己
    声明的区间，比字典里的通用值更贴。
    """
    rng = F.obj(F.first(obs.get("referenceRange")))
    if rng:
        low = F.obj(rng.get("low")).get("value")
        high = F.obj(rng.get("high")).get("value")
        if low is not None or high is not None:
            return (
                None if low is None else F.num(low),
                None if high is None else F.num(high),
            )
    spec = ctx.lookups.lab(code)
    return spec.reference_range if spec else (None, None)


def _range_text(low: float | None, high: float | None) -> str:
    if low is None and high is None:
        return ""
    if low is None:
        return f"≤{high:g}"
    if high is None:
        return f"≥{low:g}"
    return f"{low:g}-{high:g}"


def _abnormal_flag(value: str, low: float | None, high: float | None) -> str:
    """异常标志：高于上限 ``↑``，低于下限 ``↓``，正常留空。

    值是文字（如"阴性"）时无法比较，留空 —— 不要硬塞一个"正常"。
    """
    if not value:
        return ""
    try:
        v = float(value)
    except ValueError:
        return ""
    if high is not None and v > high:
        return "↑"
    if low is not None and v < low:
        return "↓"
    return ""


# ------------------------------------------------------------------ 手术操作


def procedures(ctx: Ctx) -> Table:
    headers = ["操作ID", "就诊ID", "编码", "名称", "时间"]
    rows: list[list[str]] = []
    for proc in sorted(
        ctx.data.get("Procedure"),
        key=lambda p: (F.s(p.get("performedPeriod") or p.get("performedDateTime")), F.s(p.get("id"))),
    ):
        _system, code, display = F.code_triple(proc.get("code"))
        performed = F.period_start(proc.get("performedPeriod")) or F.datetime_of(
            proc.get("performedDateTime")
        )
        rows.append(
            [
                F.s(proc.get("id")),
                ctx.data.ref_id(F.obj(proc.get("encounter")).get("reference")),
                code,
                display,
                performed,
            ]
        )
    return Table("手术操作", headers, rows)


# --------------------------------------------------------------------- 汇总

#: 表名 -> 构造函数。顺序即外键依赖顺序：被引用的表在前。
TABLES: dict[str, Callable[[Ctx], Table]] = {
    "患者": patients,
    "机构": organizations,
    "医生": practitioners,
    "就诊": encounters,
    "诊断": diagnoses,
    "医嘱_药品": medication_orders,
    "检验": lab_results,
    "手术操作": procedures,
}


#: 要检查的外键：``(表, 列)``。目标列由列名决定
#: （``patient_id`` -> 患者.patient_id，``就诊ID`` -> 就诊.就诊ID……）。
FK_CHECKS: tuple[tuple[str, str], ...] = (
    ("就诊", "patient_id"),
    ("就诊", "机构ID"),
    ("就诊", "医生ID"),
    ("诊断", "就诊ID"),
    ("医嘱_药品", "就诊ID"),
    ("检验", "就诊ID"),
    ("手术操作", "就诊ID"),
)

#: 必需外键 —— 一条记录不可能不挂在它上面，因此**不允许为空**。
#: 其余外键可以为空（Synthea 的 Lifecycle 体检就没有对应就诊），
#: 但不允许整列为空。
REQUIRED_FKS: frozenset[tuple[str, str]] = frozenset(
    {
        ("就诊", "patient_id"),
    }
)


def build_all(ctx: Ctx) -> list[Table]:
    return [fn(ctx) for fn in TABLES.values()]


def check_foreign_keys(tables: list[Table]) -> list[str]:
    """检查外键完整性，返回问题列表（空列表 = 通过）。

    两条规矩：

    **必需外键**（``REQUIRED_FKS``）必须 100% 非空且指得到目标。凡是"这条记录
    不可能不挂在某个东西上"的关系都在这里 —— 例如一次就诊必然属于某个患者。

    **可选外键**只检查**非空**的那些：空值表示"这个资源确实不挂在那上面"
    （Synthea 的 Lifecycle 模块开出的体检就没有对应就诊），不是错误。
    但可选外键**整列为空**同样报错 —— 那不是"允许为空"，是链接整个断了。
    这一条是踩过坑才加的：``urn:uuid:`` 引用切不出来时，
    就诊.patient_id 与四张表的 就诊ID **全为空**，而"空值跳过"的检查
    一个字都没报，8 张表照写不误。
    """
    by_name = {t.name: t for t in tables}
    problems: list[str] = []

    def rows_of(table: str) -> list[list[str]]:
        t = by_name.get(table)
        return t.rows if t else []

    def col_of(table: str, column: str) -> list[str]:
        t = by_name.get(table)
        if t is None or column not in t.headers:
            return []
        idx = t.headers.index(column)
        return [r[idx] for r in t.rows]

    def keys_of(table: str, column: str) -> set[str]:
        return {v for v in col_of(table, column) if v}

    patient_ids = keys_of("患者", "patient_id")
    enc_ids = keys_of("就诊", "就诊ID")
    org_ids = keys_of("机构", "机构ID")
    prac_ids = keys_of("医生", "医生ID")

    targets = {
        "patient_id": patient_ids,
        "机构ID": org_ids,
        "医生ID": prac_ids,
        "就诊ID": enc_ids,
    }

    for table, column in FK_CHECKS:
        values = col_of(table, column)
        if not values:
            continue
        filled = [v for v in values if v]
        valid = targets[column]

        if not filled:
            problems.append(
                f"{table}.{column} **整列为空**（{len(values)} 行）—— "
                "引用解析断了，不是「允许为空」"
            )
            continue

        orphans = sorted({v for v in filled if v not in valid})
        if orphans:
            problems.append(
                f"{table}.{column} 有 {len(orphans)} 个孤儿外键，例如 {orphans[:3]}"
            )
        if (table, column) in REQUIRED_FKS:
            missing = len(values) - len(filled)
            if missing:
                problems.append(
                    f"{table}.{column} 是必需外键，但有 {missing}/{len(values)} 行为空"
                )
    return problems