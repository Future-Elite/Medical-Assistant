from __future__ import annotations

from datetime import timedelta

from ehrworld.schema import Action, Diagnoses, Diagnosis, PatientContext
from .models import GenerationRequest, PatientInput
from .neonatal import NEONATAL_SCENARIOS, neonatal_demo_values


SCENARIOS = {
    **NEONATAL_SCENARIOS,
    "diabetes": {
        "id": "diabetes", "name": "血糖管理", "description": "代谢指标与生命体征的连续观察",
        "default_patient": PatientInput.for_scenario("diabetes").model_dump(),
        "measurements": [
            {"key": "glucose", "name": "血糖", "unit": "mmol/L", "color": "#0e8a83"},
            {"key": "potassium", "name": "血钾", "unit": "mmol/L", "color": "#8b5cf6"},
            {"key": "heart_rate", "name": "心率", "unit": "bpm", "color": "#e0904c"},
            {"key": "systolic", "name": "收缩压", "unit": "mmHg", "color": "#4e7ee1"},
            {"key": "temperature", "name": "体温", "unit": "°C", "color": "#e2697a"},
        ],
        "intervention": "血糖管理与补液干预",
    },
    "pneumonia": {
        "id": "pneumonia", "name": "肺部感染", "description": "感染指标与呼吸状态的连续观察",
        "default_patient": PatientInput.for_scenario("pneumonia").model_dump(),
        "measurements": [
            {"key": "temperature", "name": "体温", "unit": "°C", "color": "#0e8a83"},
            {"key": "spo2", "name": "血氧饱和度", "unit": "%", "color": "#4e7ee1"},
            {"key": "heart_rate", "name": "心率", "unit": "bpm", "color": "#e0904c"},
            {"key": "respiratory_rate", "name": "呼吸频率", "unit": "次/min", "color": "#8b5cf6"},
            {"key": "wbc", "name": "白细胞计数", "unit": "10^9/L", "color": "#e2697a"},
        ],
        "intervention": "抗感染及呼吸支持干预",
    },
}


def patient_context(request: GenerationRequest) -> PatientContext:
    p = request.patient
    if request.scenario_id.startswith("neonatal_"):
        return PatientContext(
            basic_information=f"合成新生儿，初始生后{p.postnatal_age_hours:g}小时，胎龄{p.gestational_weeks}+{p.gestational_days}周，{p.sex}，出生体重{p.birth_weight_g}g，当前体重{p.weight_g}g。主诉：{p.chief_complaint}。过敏史：{p.allergies}。",
            history_information=f"病史：{p.history or '未记录'}。围产史：{p.perinatal_history or '未记录'}。",
            diagnoses=Diagnoses(primary=Diagnosis(content=p.primary_diagnosis, reason="来源单时点初始标签；用于合成轨迹，需结合本步检查动态鉴别。" if request.source_seed else "用户设定的合成病例；需结合本步检查动态鉴别。")),
            demographics={"postnatal_age_hours_at_start": p.postnatal_age_hours, "gestational_weeks": p.gestational_weeks,
                          "gestational_days": p.gestational_days, "birth_weight_g": p.birth_weight_g, "weight_g": p.weight_g,
                          "sex": p.sex, "synthetic": True, "care_setting": request.care_setting})
    return PatientContext(
        basic_information=f"合成患者，{p.age} 岁，{p.sex}。主诉：{p.chief_complaint}。过敏史：{p.allergies or '未记录'}。",
        history_information=p.history or "既往史未记录。",
        diagnoses=Diagnoses(primary=Diagnosis(content=p.primary_diagnosis, reason="用户为本次合成住院设定的主要诊断。")),
        demographics={"age": p.age, "sex": p.sex, "synthetic": True},
    )


def action_plan(request: GenerationRequest):
    """Fixed research observation schedule; clinical order selection is exogenous to EHRWorld."""
    if request.scenario_id.startswith("neonatal_"):
        from ehrworld.schema import PatientState
        from .physician import plan_step
        state = PatientState(timestamp=request.start_time, context=patient_context(request))
        return [(p.timestamp, p.actions) for i in range(request.steps) for p in [plan_step(request, state, i, preview=True)]]
    scenario = SCENARIOS[request.scenario_id]
    plan = []
    for index in range(request.steps):
        actions = [Action(action_id=f"step-{index + 1:02d}:{m['key']}", kind="inquiry", name=m["name"],
                          concept=f"web:{m['key']}", source="synthetic-observation", value_type="number", unit=m["unit"])
                   for m in scenario["measurements"]]
        actions.append(Action(action_id=f"step-{index + 1:02d}:summary", kind="inquiry", name="病程记录",
                              concept="web:summary", source="synthetic-note", value_type="label"))
        if index in (0, 3, 6):
            actions.append(Action(action_id=f"step-{index + 1:02d}:intervention", kind="intervention",
                                  name=scenario["intervention"], concept=f"web:{request.scenario_id}:intervention",
                                  source="synthetic-intervention", parameters={"purpose": "合成轨迹研究", "dose": "未指定"}))
        plan.append((request.start_time + timedelta(hours=request.interval_hours * index), tuple(actions)))
    return plan


class DemoPredictor:
    """Deterministic UI demo only; no claim of learned dynamics or clinical fidelity."""
    name = "demo:synthetic-rule-example"

    def __init__(self, request: GenerationRequest):
        self.request = request

    def predict(self, state, actions):
        if self.request.scenario_id.startswith("neonatal_"):
            return neonatal_demo_values(self.request, state, actions)
        t = len(state.history)
        progress = t / max(self.request.steps - 1, 1)
        values = {
            "glucose": round(15.8 - 8.6 * progress + (0.2 if t % 3 == 1 else 0), 1),
            "potassium": round(3.7 + .5 * progress, 1),
            "heart_rate": round((102 if self.request.scenario_id == "pneumonia" else 92) - 18 * progress),
            "systolic": round(142 - 16 * progress),
            "temperature": round((39.1 - 2.2 * progress) if self.request.scenario_id == "pneumonia" else (36.8 - .3 * progress), 1),
            "spo2": round(92 + 6 * progress), "respiratory_rate": round(26 - 9 * progress),
            "wbc": round(15.6 - 8.1 * progress, 1),
        }
        note = (f"第 {t + 1} 次合成观察。" + ("初始评估已记录，开始连续监测。" if t == 0 else
                "观察指标沿演示规则逐渐变化，继续记录后续病程。" if t < 6 else "演示指标趋于稳定，继续观察。") +
                "此记录来自离线规则示例。")
        return {a.action_id: note if a.concept == "web:summary" else values[a.concept.split(":", 1)[1]]
                for a in actions if a.kind == "inquiry"}
