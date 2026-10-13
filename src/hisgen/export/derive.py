"""导出层自己生成、FHIR 里没有的字段。

设计文档 §9.1 列了三个字段不能直接从 FHIR 取：**医保类型**、**医保号**、**民族**。

其中医保类型能取到（``Claim.insurer.display``，替换 ``payers/*.csv`` 后自然是
中文医保名）；另外两个 Synthea 根本不产出，只能在这里造：

* **医保号** —— 中国医保没有国际标准号，实务上是「统筹区 + 个人编号」。
  这里用 ``SH + GB/T 2260 区划码(6位) + 序号(6位)``，共 14 位。
  这是**本方案新增的合成数据**，不是 Synthea 产出，落库时必须标注。

* **民族** —— Synthea 的 ``race`` 是 OMB 种族分类（white/black/asian/…），
  与中国的"民族"不是一回事，照搬会得到"亚洲人"这种填不进户口本的答案。
  这里按可配置分布生成，默认按上海实际构成（汉族约 98.5%）。

两个字段都**由 patient_id 派生**，不用 ``random`` —— 同一个人重复导出必须
得到同一个值，否则每次重跑都会把已经进库的外键洗掉。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

#: GB/T 2260 上海市各区行政区划代码。
#: 用作 Synthea FIPS 的替代 —— 上海没有 FIPS 县码，区划码是本地对应物。
SHANGHAI_DISTRICTS: dict[str, str] = {
    "黄浦区": "310101",
    "徐汇区": "310104",
    "长宁区": "310105",
    "静安区": "310106",
    "普陀区": "310107",
    "虹口区": "310109",
    "杨浦区": "310110",
    "闵行区": "310112",
    "宝山区": "310113",
    "嘉定区": "310114",
    "浦东新区": "310115",
    "金山区": "310116",
    "松江区": "310117",
    "青浦区": "310118",
    "奉贤区": "310120",
    "崇明区": "310151",
}

#: 上海市行政区划代码（GB/T 2260）
SHANGHAI_CODE = "310000"

#: 默认民族分布。键是民族名，值是权重（不必和为 1，会归一化）。
#: 依据第七次全国人口普查上海市数据，汉族占 98.4% 左右；其余按上海
#: 常住少数民族的实际构成给量级 —— 这是**近似分布**，权威数字见
#: ``shanghai/SOURCES.md``，投产前应替换。
DEFAULT_ETHNICITY_WEIGHTS: dict[str, float] = {
    "汉族": 98.4,
    "回族": 0.5,
    "满族": 0.3,
    "蒙古族": 0.2,
    "壮族": 0.15,
    "朝鲜族": 0.1,
    "苗族": 0.1,
    "土家族": 0.1,
    "维吾尔族": 0.08,
    "彝族": 0.07,
}


#: GB 11643-1999 身份证号校验码：前 17 位的加权因子
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
#: 加权和除以 11 的余数 -> 校验字符
_ID_CHECK_CHARS = "10X98765432"


def is_cn_id(value: str) -> bool:
    """是不是一个校验通过的 18 位居民身份证号（GB 11643-1999）。

    只做校验位与格式检查，**不校验**行政区划码是否真实存在 ——
    本轮用的区划码来自 GB/T 2260 的上海清单，但历史号码可能落在已撤销的区。
    """
    value = value.strip().upper()
    if len(value) != 18:
        return False
    body, check = value[:17], value[17]
    if not body.isdigit():
        return False
    total = sum(int(c) * w for c, w in zip(body, _ID_WEIGHTS))
    return _ID_CHECK_CHARS[total % 11] == check


def _stable_digest(*parts: str) -> bytes:
    """由输入派生的稳定摘要。同样的输入永远得到同样的字节。"""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.digest()


def _stable_fraction(*parts: str) -> float:
    """派生一个 [0, 1) 的稳定小数（取摘要前 8 字节当整数）。"""
    return int.from_bytes(_stable_digest(*parts)[:8], "big") / float(1 << 64)


@dataclass
class Deriver:
    """生成医保号与民族。

    默认值面向上海，但分布可换 —— 换城市只要换 ``ethnicity_weights`` 和
    ``insurance_prefix``，不用改代码。
    """

    #: 医保号前缀。上海用 ``SH``；其他统筹区换掉即可。
    insurance_prefix: str = "SH"
    #: 民族分布权重
    ethnicity_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_ETHNICITY_WEIGHTS)
    )
    #: 区名 -> 区划码；查不到时回退到上海市码
    districts: dict[str, str] = field(
        default_factory=lambda: dict(SHANGHAI_DISTRICTS)
    )

    # ------------------------------------------------------------- 医保号

    def district_code(self, district_name: str) -> str:
        """区名 -> GB/T 2260 区划码。查不到给上海市码，不抛异常。

        宁可全给 ``310000`` 也不要因为一个区名对不上就整个导出失败 ——
        区名对不上本身会在 ``区县`` 列里暴露出来，比中断更容易发现。
        """
        return self.districts.get(district_name.strip(), SHANGHAI_CODE)

    def insurance_id(self, patient_id: str, district_name: str) -> str:
        """生成医保号：``SH`` + 区划码(6) + 序号(6)，共 14 位。

        序号由 patient_id 派生，所以同一个人永远是同一个号。
        """
        code = self.district_code(district_name)
        seq = int.from_bytes(_stable_digest(patient_id, "insurance")[:4], "big") % 1_000_000
        return f"{self.insurance_prefix}{code}{seq:06d}"

    # --------------------------------------------------------------- 民族

    def ethnicity(self, patient_id: str) -> str:
        """按权重分布抽一个民族。用 patient_id 派生，不用全局随机数。"""
        return self._weighted_pick(
            self.ethnicity_weights, patient_id, "ethnicity"
        ) or "汉族"

    # --------------------------------------------------------------- 职称

    #: 卫生技术职称分布。Synthea 不产出职称，这里按常见金字塔结构造。
    #: 与民族一样由 practitioner_id 派生，保证可重现。
    title_weights: dict[str, float] = field(
        default_factory=lambda: {
            "主任医师": 15.0,
            "副主任医师": 25.0,
            "主治医师": 40.0,
            "住院医师": 20.0,
        }
    )

    def title(self, practitioner_id: str) -> str:
        """生成医师职称。合成数据，非 Synthea 产出。"""
        return self._weighted_pick(self.title_weights, practitioner_id, "title")

    # --------------------------------------------------------------- 科室

    #: 科室权重。**只在推断不出科室时才用** —— 首选是从该医生的就诊反推
    #: （见 ``tables.Ctx._index_clinicians``）。会走到这里的医生是 Synthea
    #: 生成了但一次都没出诊过的，就诊记录里没有他的任何痕迹。
    #: 权重按上海三级医院内分泌相关科室的常见配置给量级，是**近似分布**。
    department_weights: dict[str, float] = field(
        default_factory=lambda: {
            "内分泌科": 30.0,
            "内科": 25.0,
            "全科医学科": 20.0,
            "肾内科": 10.0,
            "眼科": 10.0,
            "心血管内科": 5.0,
        }
    )

    def department(self, practitioner_id: str, available: list[str]) -> str:
        """派生医师科室。合成数据，非 Synthea 产出。

        ``available`` 是 ``shanghai/department_map.csv`` 里登记的中文科室 ——
        只用权重表里**同时也登记过**的那些，免得派生出一个映射表里
        根本不存在的科室名，那样 医生.科室 与 就诊.科室 会对不上。
        """
        weights = {
            k: v for k, v in self.department_weights.items() if k in available
        }
        if not weights:
            weights = {k: 1.0 for k in available}
        if not weights:
            return ""
        return self._weighted_pick(weights, practitioner_id, "department")

    def _weighted_pick(
        self, weights: dict[str, float], *parts: str
    ) -> str:
        names = list(weights)
        if not names:
            return ""
        total = sum(weights.values())
        if total <= 0:
            return names[0]
        roll = _stable_fraction(*parts) * total
        acc = 0.0
        for name in names:
            acc += weights[name]
            if roll < acc:
                return name
        return names[-1]

    # ------------------------------------------------------- 身份证号（占位）

    def national_id(self, candidates: list[str]) -> str:
        """从候选 identifier 值里挑出真正的身份证号。

        只认**通过 GB 11643 校验**的 18 位号。这不是多此一举 —— 我们要在
        没打 Java 补丁的 Synthea 上也能安全跑：那时 SSN 生出来的是
        ``123-45-6789`` 这种九位数，校验不过，于是这一列留空。
        把美国 SSN 当中国身份证号填进去，是比空值严重得多的错误。
        """
        for raw in candidates:
            if is_cn_id(raw):
                return raw
        return ""

    def mask_national_id(self, raw: str) -> str:
        """身份证号脱敏：保留前 6 位（地区）与后 4 位，中间打星。

        身份证号是真实敏感字段。第一轮产出的号是合成的，但导出层不该假设
        下游一定知道这件事 —— 默认脱敏，需要原值请显式关掉。
        """
        raw = raw.strip()
        if len(raw) < 12:
            return raw
        return raw[:6] + "*" * (len(raw) - 10) + raw[-4:]