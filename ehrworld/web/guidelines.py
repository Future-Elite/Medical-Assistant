"""Reviewed, page-addressable summaries of the two USER-PROVIDED neonatal PDFs.

Rules below are application interpretations. No unrelated PDFs are ingested into
prompts and no run-time text search can accidentally select the pituitary paper.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

from ehrworld.io import ROOT

SOURCES = {
    "pneumonia-2025": {
        "id": "pneumonia-2025", "title": "基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）",
        "filename": "基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）.pdf",
        "doi": "10.7499/j.issn.1008-8830.2412164", "year": 2025, "first_printed_page": 1027,
        "reviewed_sha256": "6d4437ccf511dcbdeb1a61b30c6575df6f1e2e75a0e7ad1357a6997c68d61cb0",
        "scope": "新生儿期起病的疑似或确诊感染性肺炎；综合危险因素、病史、影像、实验室及病原学评估。",
    },
    "rds-2025-interpretation": {
        "id": "rds-2025-interpretation", "title": "《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读",
        "filename": "《欧洲新生儿呼吸窘迫综合征管理共识指南2025版》解读.pdf",
        "doi": "10.7499/j.issn.1008-8830.2603060", "year": 2026, "first_printed_page": 529,
        "reviewed_sha256": "fcf1e5dd5e308466bc6e06509edc6d99de8896352cb737f09ff3e44b940e2ca5",
        "scope": "2025 版欧洲指南的中文解读，主要适用于早产儿 RDS；产房复苏与稳定后治疗分开处理。",
    },
}

# (source, PDF page, section, paraphrase, applicability)
RULES = {
    "PN-ASSESS": ("pneumonia-2025", 3, "推荐3–6", "结合围产感染风险、临床呼吸表现和灌注等情况综合评估，不能沿用成人肺炎画像。", "新生儿感染性肺炎"),
    "PN-LABS": ("pneumonia-2025", 4, "推荐9及说明", "血常规、CRP、PCT仅辅助观察炎症过程，不能单独确诊肺炎或决定抗感染方案。", "疑似感染"),
    "PN-CULTURE": ("pneumonia-2025", 4, "推荐9及说明", "按临床情况选取病原学样本；区分感染与定植，不对非重症患儿常规做气管抽吸。", "疑似感染，采样与报告时间必须区分"),
    "PN-IMAGING": ("pneumonia-2025", 4, "推荐10；第5页补充说明", "使用肺超声或胸部X线辅助诊断；胸部CT不是常规检查。", "需要影像评估"),
    "PN-DIFFERENTIAL": ("pneumonia-2025", 5, "推荐11–12", "综合危险因素、症状、影像及实验室结果，并鉴别RDS、暂时性呼吸增快、胎粪吸入及先心病等。", "新生儿呼吸窘迫"),
    "PN-ANTIBIOTIC": ("pneumonia-2025", 5, "推荐13及说明", "怀疑细菌性肺炎时按发病时间及流行病史启动经验性抗菌治疗，取得病原和药敏结果后调整。", "怀疑细菌感染；剂量及具体方案需个体化"),
    "PN-NEGATIVE-CULTURE": ("pneumonia-2025", 6, "抗菌治疗续文", "临床和影像仍支持细菌性肺炎时，培养阴性不等于排除肺炎；持续评估，不套用RDS排除败血症后的早停药规则。", "培养阴性但肺炎仍有临床依据"),
    "PN-RESPIRATORY": ("pneumonia-2025", 6, "推荐16–17", "低氧或呼吸衰竭给予氧疗/呼吸支持，监测脉搏血氧并按需要结合血气；呼吸支持方式按个体需求选择。", "低氧或呼吸支持中"),
    "PN-ESCALATE": ("pneumonia-2025", 6, "推荐16说明", "nCPAP压力>6 cmH2O、FiO2>0.30仍不能维持SpO2在93%以上时，可考虑有创支持评估。", "严格大于边界，仅用于支持失败评估"),
    "PN-REFERRAL": ("pneumonia-2025", 7, "推荐18", "严重感染、生命体征不稳定、需要高级生命支持或治疗效果不佳时评估及时转诊。", "结合所在机构能力"),
    "RDS-CPAP": ("rds-2025-interpretation", 4, "表5", "有RDS风险且无需插管维持稳定的早产儿采用经鼻CPAP或同步NIPPV，CPAP常用起始压力6–8 cmH2O。", "早产儿，非产房分钟级复苏参数"),
    "RDS-PS": ("rds-2025-interpretation", 3, "表3", "胎龄>28周、CPAP压力≥6 cmH2O、FiO2≥0.30且符合早期RDS救援治疗情境时评估PS；自主呼吸患儿优先细导管LISA。", "不能把该数值阈值外推到所有胎龄"),
    "RDS-EARLY-PS": ("rds-2025-interpretation", 3, "表3", "胎龄<28周、稳定复苏后有RDS早期征象者，可在生后第1小时内考虑选择性预防PS。", "<28周并且生后<1小时"),
    "RDS-INTUBATED-PS": ("rds-2025-interpretation", 3, "表3", "胎龄<32周、需插管维持稳定的早产儿给予PS；再次给药须复核持续需氧及其他原因。", "<32周并且已需插管支持"),
    "RDS-OXYGEN": ("rds-2025-interpretation", 4, "表4", "解读将90%–94%的稳定后氧疗SpO2目标人群限定为胎龄<28周；其他胎龄需个体化，不能统一套用。", "稳定复苏之后；不混入产房最初几分钟的氧浓度"),
    "RDS-VENTILATION": ("rds-2025-interpretation", 5, "表6", "非侵入支持失败时评估机械通气，采用肺保护策略；避免将HFNC作为RDS的一线CPAP替代方案。", "支持失败，需有专科能力"),
    "RDS-CAFFEINE": ("rds-2025-interpretation", 5, "表6", "<32周早产儿可考虑预防性标准剂量咖啡因，避免每步重复负荷给药。", "<32周早产儿"),
    "RDS-SUPPORT": ("rds-2025-interpretation", 5, "表7", "维持体温36.5–37.5℃，根据电解质、尿量和体重个体化调整液体与营养。", "早产儿支持治疗"),
    "RDS-ANTIBIOTIC": ("rds-2025-interpretation", 5, "第7节及表7", "RDS不等同于感染；经验性抗生素仅在感染评估需要时采用，培养阴性且无确诊败血症实验室证据时评估24–36小时内停药。", "须同时满足感染排除条件；不用于仍疑似细菌肺炎者"),
}


class GuidelineSourceError(ValueError):
    pass


def citation(rule_id):
    source_id, page, section, text, population = RULES[rule_id]
    source = SOURCES[source_id]
    return {"rule_id": rule_id, "source_id": source_id, "title": source["title"], "pdf_page": page,
            "printed_page": source["first_printed_page"] + page - 1, "section": section,
            "summary": text, "applicability": population,
            "url": f"/api/guidelines/{source_id}/document#page={page}"}


def source_path(source_id):
    if source_id not in SOURCES:
        raise KeyError(source_id)
    return ROOT / "guidelines" / SOURCES[source_id]["filename"]


@lru_cache(maxsize=8)
def _digest(path, size, mtime):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest():
    sources = []
    for source_id, metadata in SOURCES.items():
        path = source_path(source_id)
        stat = path.stat() if path.is_file() else None
        digest = _digest(path, stat.st_size, stat.st_mtime_ns) if stat else None
        sources.append({**metadata, "available": bool(stat), "reviewed": digest == metadata["reviewed_sha256"],
                        "sha256": digest,
                        "url": f"/api/guidelines/{source_id}/document"})
    return {"adapter_version": "neonatal-v1", "sources": sources,
            "rules": [citation(key) for key in RULES],
            "excluded_files": [{"filename": "11102_2025_Article_1505.pdf", "reason": "垂体肿瘤研究，与本次新生儿适配无关。"}],
            "scope": "指南约束的合成研究；规则实现及固定观察步长是工程选择，未经临床验证。"}


def guidance_for(scenario_id):
    prefix = "PN-" if scenario_id == "neonatal_pneumonia" else "RDS-"
    return {"version": "neonatal-v1", "rules": [citation(k) for k in RULES if k.startswith(prefix) or scenario_id == "neonatal_rds" and k in {"PN-LABS", "PN-CULTURE", "PN-IMAGING", "PN-ANTIBIOTIC", "PN-REFERRAL"}],
            "simulation_contract": "仅生成合成观测。病程文本须结合本步实际提供的医生动作，不得新增未下达的给药或检查；培养采样不等于即刻获得最终结果。不得把十步结束视为治愈、疗程完成或出院。不要照搬成人生命体征。"}
