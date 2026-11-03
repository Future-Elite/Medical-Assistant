"""A bounded, auditable simulated-physician policy based ONLY on available history.

Fixed sampling cadence, initial culture availability and numeric escalation guards
are engineering interpretations, not a substitute for individual clinical judgment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ehrworld.schema import Action, PatientState
from .guidelines import citation
from .models import GenerationRequest
from .neonatal import LABS, VITALS


@dataclass
class PhysicianPlan:
    timestamp: object
    actions: tuple[Action, ...]
    record: dict


def compact_ref(rule_id):
    ref = citation(rule_id)
    return {k: ref[k] for k in ("rule_id", "source_id", "pdf_page", "printed_page", "section", "url")}


def observed_state(request, state):
    initial = request.initial_state
    result = {"spo2": initial.spo2, "respiratory_rate": initial.respiratory_rate,
              "fio2": initial.fio2, "cpap": initial.cpap_cm_h2o if initial.respiratory_support in {"cpap", "nippv"} else 0,
              "support_mode": initial.respiratory_support, "apnea": "present" if initial.recurrent_apnea else "absent",
              "culture": "not_collected", "sepsis_evidence": "suspected" if initial.infection_suspected else "unknown"}
    result.update({k: getattr(initial, k) for k in ('heart_rate','temperature','ph','paco2','glucose') if getattr(initial,k) is not None})
    for step in state.history:
        for event in step.events:
            if event.action.kind == "inquiry" and event.action.concept.startswith("web:"):
                result[event.action.concept[4:]] = event.value
    return result


def has_order(state, key):
    return any(e.action.concept == "web:" + key for s in state.history for e in s.events)


def antibiotics_active(state):
    active = False
    for step in state.history:
        for event in step.events:
            if event.action.concept == "web:abx_start": active = True
            if event.action.concept == "web:abx_stop": active = False
    return active


def hours_since_order(state, key, timestamp):
    times = [s.timestamp for s in state.history for e in s.events if e.action.concept == "web:" + key]
    return (timestamp - max(times)).total_seconds() / 3600 if times else None


def plan_step(request: GenerationRequest, state: PatientState, index: int, *, preview=False):
    timestamp = request.start_time + timedelta(hours=index * request.interval_hours)
    elapsed = index * request.interval_hours
    age_hours = request.patient.postnatal_age_hours + elapsed
    gestation = request.patient.gestational_weeks + request.patient.gestational_days / 7
    rds = request.scenario_id == "neonatal_rds"
    rule = "RDS-CPAP" if rds else "PN-RESPIRATORY"
    snapshot = observed_state(request, state)
    actions, decisions, warnings = [], [], []

    def add(key, name, kind, rationale, rule_ids, *, unit=None, value_type=None, parameters=None):
        refs = [compact_ref(r) for r in rule_ids]
        action = Action(action_id=f"step-{index+1:02d}:{key}", name=name, concept="web:" + key, kind=kind,
                        source="guideline-physician", unit=unit, value_type=value_type,
                        parameters={"actor": "simulated_physician", "indication": rationale,
                                    "guideline_refs": refs, **(parameters or {})})
        actions.append(action)
        decisions.append({"order_id": action.action_id, "kind": kind, "name": name, "rationale": rationale,
                          "status": "planned" if preview else "performed_in_simulation", "guideline_refs": refs,
                          "parameters": parameters or {}})

    def exam(metric, why, ids):
        add(metric["key"], metric["name"] + "检查/监测", "inquiry", why, ids,
            value_type="number", unit=metric["unit"], parameters={"numeric_bounds": metric["bounds"]})

    for metric in VITALS:
        exam(metric, "本步无创观察生命体征并复核当前呼吸支持参数，供下一步决策使用。", [rule])
    add("support_mode", "核对呼吸支持方式", "inquiry", "记录实际支持方式，避免把给氧、CPAP和机械通气混为一谈。", [rule],
        value_type="label", parameters={"allowed_values": ["room_air", "oxygen", "cpap", "nippv", "invasive"]})
    add("apnea", "评估呼吸暂停", "inquiry", "呼吸暂停及自主呼吸情况关系到支持方式与转诊评估。", [rule],
        value_type="label", parameters={"allowed_values": ["present", "absent", "unknown"]})

    # Preview cannot invent future observations or assert which later interventions execute.
    if not (preview and index > 0):
        infection = request.initial_state.infection_suspected or snapshot["sepsis_evidence"] in {"suspected", "confirmed"} or not rds
        if infection:
            lab_due = hours_since_order(state, "crp", timestamp)
            if lab_due is None or lab_due >= 24:
                for key in ("crp", "pct", "wbc"):
                    exam(LABS[key], "辅助评估感染变化，不能单独作为确诊或停抗菌药的依据。", ["PN-LABS"])
            sample_elapsed = hours_since_order(state, "culture", timestamp)
            if sample_elapsed is None:
                add("culture", "血培养采样送检并记录待报状态", "inquiry", "按感染背景获取病原学资料；本步采样不等于立即取得最终培养结果。", ["PN-CULTURE"],
                    value_type="label", parameters={"allowed_values": ["pending"], "specimen_phase": "collection", "report_assumption": "本研究模板最早24小时后允许出现后续报告，不是指南规定的统一周转时间"})
            elif has_order(state, "culture"):
                # Earliest specimen time rather than the last report review time.
                collected = min(s.timestamp for s in state.history for e in s.events if e.action.concept == "web:culture")
                available = (timestamp - collected).total_seconds() >= 24 * 3600
                add("culture", "复核已送检血培养报告", "inquiry", "复核原标本的报告状态，不重复采血；未到假定可用时间仅可待报。", ["PN-CULTURE"],
                    value_type="label", parameters={"allowed_values": ["pending", "negative", "positive"] if available else ["pending"], "specimen_phase": "report_review"})
        if infection or rds:
            add("sepsis_evidence", "综合感染/败血症证据复核", "inquiry", "综合病史、临床和已返回检查，未知不能当作排除感染。", ["RDS-ANTIBIOTIC" if rds else "PN-DIFFERENTIAL"],
                value_type="label", parameters={"allowed_values": ["unknown", "absent", "suspected", "confirmed"]})
        gas_elapsed = hours_since_order(state, "ph", timestamp)
        if index == 0 or gas_elapsed is not None and gas_elapsed >= 6 and (snapshot["spo2"] < 93 or snapshot["fio2"] >= .30):
            for key in ("ph", "paco2"):
                exam(LABS[key], "呼吸支持中按需检查血气，结合氧合情况评价通气，不把每个时点都设为重复采血。", ["RDS-VENTILATION" if rds else "PN-RESPIRATORY"])
        if index == 0:
            add("lung_ultrasound", "床旁肺超声检查", "inquiry", "结合影像与临床鉴别肺炎、RDS等病因，不使用常规胸部CT。", ["PN-IMAGING", "PN-DIFFERENTIAL"] if not rds else ["RDS-PS"], value_type="label")

        if rds:
            exam(LABS["urine_output"], "复核液体平衡，为个体化支持提供资料。", ["RDS-SUPPORT"])
            sodium_elapsed = hours_since_order(state, "sodium", timestamp)
            if sodium_elapsed is None or sodium_elapsed >= 24:
                exam(LABS["sodium"], "结合尿量和体重复核液体支持，避免没有电解质资料就假定调整。", ["RDS-SUPPORT"])
            if request.care_setting == "nicu":
                mode = snapshot["support_mode"]
                if mode in {"room_air", "oxygen"} and request.initial_state.spontaneous_breathing and snapshot["apnea"] != "present":
                    add("cpap_start", "启动经鼻 CPAP 呼吸支持", "intervention", "早产儿RDS需要无创支持，先结合已有自主呼吸状态实施。", ["RDS-CPAP"],
                        parameters={"initial_pressure_cm_h2o": 6, "fio2_strategy": "按本患儿氧合反应个体化调整"})
                else:
                    add("support_review", "复核并调整现有呼吸支持", "intervention", f"前步支持方式为{mode}，结合前步氧合及呼吸暂停复核接口和支持，不能无条件升级。", ["RDS-CPAP", "RDS-VENTILATION"])
                ps_threshold = gestation > 28 and mode == "cpap" and snapshot["cpap"] >= 6 and snapshot["fio2"] >= .30
                early = gestation < 28 and age_hours < 1 and mode in {"cpap", "nippv"}
                intubated = gestation < 32 and mode == "invasive"
                spontaneous = request.initial_state.spontaneous_breathing and snapshot["apnea"] != "present"
                if not has_order(state, "surfactant") and ((ps_threshold or early) and spontaneous or intubated):
                    basis = "RDS-INTUBATED-PS" if intubated else "RDS-EARLY-PS" if early else "RDS-PS"
                    add("surfactant", "肺表面活性物质给药" + ("（经气管导管）" if intubated else "（LISA）"), "intervention",
                        f"依据已知胎龄{gestation:.2f}周、生后{age_hours:g}小时、CPAP {snapshot['cpap']}和FiO₂ {snapshot['fio2']}判定适用条件。", [basis],
                        parameters={"route": "气管导管" if intubated else "细导管LISA", "dose_status": "须由专科核定；本适配不自动给出个体剂量", "repeat": False})
                if gestation < 32 and not has_order(state, "caffeine"):
                    add("caffeine", "启动早产儿咖啡因治疗方案", "intervention", "<32周早产儿按专科方案评估早期咖啡因，不在后续每一步重复负荷。", ["RDS-CAFFEINE"],
                        parameters={"dose_status": "负荷及维持剂量由专科核定"})
            add("thermal_nutrition", "保温及液体营养支持复核", "intervention", "根据体温、体重、尿量及电解质评估个体化支持，不将固定步长视为固定补液医嘱。", ["RDS-SUPPORT"],
                parameters={"temperature_target_celsius": [36.5, 37.5]})
            oxygen_parameters = {"spo2_target": [90, 94]} if gestation < 28 else {"spo2_target": "依胎龄与病情个体化，未套用<28周目标"}
            add("oxygen_review", "氧疗目标及报警设置复核", "intervention", "区分稳定后氧疗与产房最初分钟的复苏给氧，避免过度给氧。", ["RDS-OXYGEN"], parameters=oxygen_parameters)
        elif snapshot["spo2"] < 93 or snapshot["support_mode"] != "room_air":
            add("oxygen_review", "实施/调整氧疗及呼吸支持", "intervention", "基于先前低氧或正在进行的呼吸支持实施个体化调整，并复核监护。", ["PN-RESPIRATORY"])

        active = antibiotics_active(state)
        bacterial_evidence = snapshot["culture"] == "positive" or request.initial_state.bacterial_pneumonia_supported
        treatment_needed = bacterial_evidence or (request.initial_state.suspected_pathogen != "viral" and (
            not rds and request.initial_state.suspected_pathogen == "bacterial" or snapshot["sepsis_evidence"] in {"suspected", "confirmed"}
            or request.initial_state.infection_suspected and snapshot["sepsis_evidence"] == "unknown"))
        if treatment_needed and not active:
            add("abx_start", "启动经验性抗菌治疗", "intervention", "存在感染疑虑，按发病背景、当地病原和既有培养资料选择方案；不单凭CRP或PCT。", ["RDS-ANTIBIOTIC", "PN-ANTIBIOTIC"] if rds else ["PN-ANTIBIOTIC"],
                parameters={"route": "静脉", "regimen_status": "具体药物、剂量和间隔须按胎龄/体重/肾功能核定"})
        elif active:
            abx_start_times = [s.timestamp for s in state.history for e in s.events if e.action.concept == "web:abx_start"]
            abx_hours = (timestamp - max(abx_start_times)).total_seconds() / 3600
            can_stop = rds and not request.initial_state.bacterial_pneumonia_supported and abx_hours >= 24 and snapshot["culture"] == "negative" and snapshot["sepsis_evidence"] == "absent"
            if can_stop:
                if abx_hours > 36:
                    warnings.append("当前可复核节点已超过抗菌药24–36小时复核窗口；记录实际决策时间，不回填成及时复核。可缩短生成步长。")
                add("abx_stop", "排除感染后停止经验性抗菌治疗", "intervention", f"当前用药{abx_hours:g}小时，前步培养阴性且综合评估无确诊败血症证据；无仍受支持的细菌肺炎，停止不必要经验性用药。", ["RDS-ANTIBIOTIC"])
            else:
                add("abx_continue", "复核并继续当前抗菌方案", "intervention", "仍需结合临床及病原学复核；培养待报、证据未知或仍疑似细菌肺炎时，不机械停药。", ["RDS-ANTIBIOTIC"] if rds else ["PN-ANTIBIOTIC", "PN-NEGATIVE-CULTURE"])

        failure = snapshot["apnea"] == "present" or (snapshot["cpap"] > 6 and snapshot["fio2"] > .30 and snapshot["spo2"] < 93)
        severe = failure or snapshot["spo2"] <= 92 or snapshot["respiratory_rate"] > 70
        if severe:
            name = "上级医院转诊及转运支持评估" if request.care_setting == "primary" else "新生儿高级呼吸支持床旁会诊"
            add("escalation_review", name, "intervention", "基于已有低氧、呼吸暂停或支持失败信号复核救治能力；记录会诊/转运评估，不伪造已完成气管插管。", ["RDS-VENTILATION", "PN-REFERRAL"] if rds else ["PN-ESCALATE", "PN-REFERRAL"])

    add("summary", "记录本步病程与处置反应", "inquiry", "把实际列出的检查、干预与合成观测结果关联记录，不凭空新增药物、剂量或出院结论。", ["RDS-SUPPORT" if rds else "PN-DIFFERENTIAL"], value_type="label")
    before = {k: v for k, v in snapshot.items() if k != "summary"}
    record = {"actor": "模拟医生", "decision_basis": "初始已知状态" if not state.history else "仅此前已生成病历",
              "observation_cutoff": state.history[-1].timestamp.isoformat() if state.history else None,
              "postnatal_age_hours": age_hours, "gestational_age_weeks": gestation, "prior_observations": before,
              "assessment": "结合胎龄、围产史、先前氧合/呼吸支持和感染证据评估；本步结果在下达动作后生成。",
              "orders": decisions, "preview_only": preview,
              "warnings": warnings,
              "note": "后续干预需等前一步结果后再决定。" if preview and index > 0 else "模拟执行的动作进入EHRWorld历史；本步结束不等于疗程结束。"}
    return PhysicianPlan(timestamp, tuple(actions), record)
