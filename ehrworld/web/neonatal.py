from __future__ import annotations

from .models import InitialState, PatientInput


def measurement(key, name, unit, bounds):
    return {"key": key, "name": name, "unit": unit, "color": "#0e8a83", "bounds": bounds}


VITALS = [measurement("spo2", "经皮血氧饱和度", "%", [0, 100]),
          measurement("respiratory_rate", "呼吸频率", "次/min", [0, 180]),
          measurement("heart_rate", "心率", "bpm", [0, 300]),
          measurement("temperature", "体温", "°C", [30, 43]),
          measurement("fio2", "吸入氧浓度 FiO₂", "fraction", [.21, 1]),
          measurement("cpap", "CPAP 压力", "cmH2O", [0, 20])]
LABS = {"crp": measurement("crp", "C 反应蛋白", "mg/L", [0, 600]),
        "pct": measurement("pct", "降钙素原", "ng/mL", [0, 1000]),
        "wbc": measurement("wbc", "白细胞计数", "10^9/L", [0, 150]),
        "ph": measurement("ph", "血气 pH", "unitless", [6, 8]),
        "paco2": measurement("paco2", "血气 PaCO₂", "mmHg", [5, 200]),
        "sodium": measurement("sodium", "血清钠", "mmol/L", [80, 220]),
        "urine_output": measurement("urine_output", "尿量监测", "mL/kg/h", [0, 20])}

NEONATAL_SCENARIOS = {
    "neonatal_pneumonia": {"id": "neonatal_pneumonia", "name": "新生儿肺炎",
                          "description": "基于本地 2025 年新生儿感染性肺炎指南，动态安排检查与干预。",
                          "default_patient": PatientInput.for_scenario("neonatal_pneumonia").model_dump(),
                          "measurements": VITALS + [LABS["crp"], LABS["pct"], LABS["wbc"]],
                          "guideline_ids": ["pneumonia-2025"], "population": "neonatal"},
    "neonatal_rds": {"id": "neonatal_rds", "name": "早产儿 RDS",
                     "description": "基于欧洲 RDS 2025 版指南中文解读，按胎龄和已有呼吸状态安排动作。",
                     "default_patient": PatientInput.for_scenario("neonatal_rds").model_dump(),
                     "measurements": VITALS + [LABS["ph"], LABS["paco2"], LABS["sodium"], LABS["urine_output"]],
                     "guideline_ids": ["rds-2025-interpretation"], "population": "neonatal"},
}
for _key, _scenario in NEONATAL_SCENARIOS.items():
    _scenario["default_initial_state"] = InitialState.for_scenario(_key).model_dump()
    _scenario["default_interval_hours"] = 3


def neonatal_demo_values(request, state, actions):
    """UI fixture, not a physiological model. Actual past actions determine support labels."""
    t = len(state.history)
    p = t / max(request.steps - 1, 1)
    rds = request.scenario_id == "neonatal_rds"
    mode = request.initial_state.respiratory_support
    for history_step in state.history:
        for event in history_step.events:
            if event.action.concept == "web:support_mode": mode = event.value
    if any(a.concept == "web:cpap_start" for a in actions): mode = "cpap"
    values = {"spo2": round(request.initial_state.spo2 + (95 - request.initial_state.spo2) * p),
              "respiratory_rate": round(request.initial_state.respiratory_rate - 25 * p),
              "heart_rate": round((request.initial_state.heart_rate if request.initial_state.heart_rate is not None else 166) * (1-p) + 142*p), "temperature": round((request.initial_state.temperature if request.initial_state.temperature is not None else (36.6 if rds else 37.8)) * (1-p) + 36.8*p, 1),
              "fio2": round(request.initial_state.fio2 + (.23 - request.initial_state.fio2) * p, 2),
              "cpap": 6 if mode in {"cpap", "nippv"} else 0, "crp": round(24 - 12 * p, 1), "pct": round(2.4 - 1.3 * p, 2),
              "wbc": round(22 - 7 * p, 1), "ph": round((request.initial_state.ph if request.initial_state.ph is not None else 7.25) * (1-p) + 7.35*p, 3), "paco2": round((request.initial_state.paco2 if request.initial_state.paco2 is not None else 58) * (1-p) + 46*p, 1),
              "sodium": round(139 + p, 1), "urine_output": round(1.6 + .7 * p, 1)}
    result = {}
    for action in actions:
        if action.kind != "inquiry":
            continue
        key = action.concept.split(":", 1)[1]
        if key == "summary":
            names = "、".join(a.name for a in actions if a.kind == "intervention") or "本步检查与临床复核"
            value = f"第 {t+1} 步：模拟医生完成所列检查，并记录{names}。继续随访呼吸、喂养及治疗反应；本步不代表治愈或疗程结束。离线规则演示。"
        elif key == "culture":
            options = action.parameters["allowed_values"]
            value = "negative" if "negative" in options else options[0]
        elif key == "sepsis_evidence":
            value = "suspected" if not rds else "absent" if t * request.interval_hours >= 12 else "unknown"
        elif key == "support_mode":
            value = mode
        elif key == "apnea":
            value = "present" if request.initial_state.recurrent_apnea and t == 0 else "absent"
        elif key == "lung_ultrasound":
            value = "双肺弥漫性改变，结合临床鉴别 RDS 与感染" if rds else "肺实变伴支气管充气征，结合感染风险综合评估"
        else:
            value = values[key]
        result[action.action_id] = value
    return result
