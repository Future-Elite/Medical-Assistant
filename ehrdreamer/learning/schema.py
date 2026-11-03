from __future__ import annotations

import math

METRICS = {
    'spo2': ('血氧饱和度SpO₂', '%'),
    'sao2': ('血气氧饱和度sO₂', '%'),
    'respiratory_rate': ('呼吸频率', '次/min'),
    'heart_rate': ('心率', 'bpm'),
    'temperature': ('体温', '°C'),
    'ph': ('血气pH', 'unitless'),
    'paco2': ('PaCO₂', 'mmHg'),
    'crp': ('CRP', 'mg/L'),
    'pct': ('PCT', 'ng/mL'),
    'wbc': ('白细胞计数', '10^9/L'),
    'bilirubin': ('总胆红素', 'µmol/L'),
    'weight_g': ('体重', 'g'),
    'feeding': ('来源文本中的喂养耐受标签', 'binary'),
}
ACTIONS = ('oxygen', 'cpap', 'antimicrobial', 'surfactant', 'thermal_nutrition', 'phototherapy')
STATIC = ('gestational_weeks', 'gestational_days', 'postnatal_age_hours', 'birth_weight_g',
          'sex_male', 'respiratory_symptoms', 'jaundice_symptoms', 'maternal_infection',
          'prom', 'cesarean', 'multiple_birth')
FEATURES = list(STATIC) + ['elapsed_hours', 'delta_hours'] + [f'previous_{m}' for m in METRICS] + [f'previous_age_{m}' for m in METRICS] + [f'action_{a}' for a in ACTIONS]
OBS_FEATURES = list(STATIC) + [f'current_{m}' for m in METRICS]
PANELS = {'vitals': ('spo2', 'respiratory_rate', 'heart_rate', 'temperature'),
          'blood_gas': ('ph', 'paco2', 'sao2'), 'infection_panel': ('crp', 'pct', 'wbc'),
          'nutrition': ('weight_g', 'feeding'), 'bilirubin': ('bilirubin',)}
MODEL_VERSION = 'dreamer-ml-2.0'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def static_features(intake):
    if hasattr(intake, 'model_dump'):
        intake = intake.model_dump()
    symptoms = intake.get('symptoms', ())
    risks = intake.get('perinatal_risks', ())
    # Not mentioned does not establish absence. Preserve missingness as NaN.
    present = lambda condition: 1.0 if condition else None
    return [number(intake.get(k)) for k in STATIC[:4]] + [
        1.0 if intake.get('sex') == '男' else 0.0 if intake.get('sex') == '女' else None,
        present(any(s in symptoms for s in ('气促', '呼吸急促', '呻吟', '呼吸困难', '发绀', '咳嗽'))),
        present(any(s in symptoms for s in ('黄疸', '黄染'))),
        present('母体感染风险' in risks), present('胎膜早破' in risks),
        present(intake.get('delivery_mode') == 'cesarean'), present('多胎妊娠' in risks)]


def features(intake, previous=None, elapsed=0, delta=0, actions=None, ages=None):
    previous, actions = previous or {}, actions or {}
    ages = ages or {}
    return static_features(intake) + [elapsed, delta] + [previous.get(k) for k in METRICS] + [ages.get(k) for k in METRICS] + [actions.get(k) for k in ACTIONS]


def observation_features(intake, values):
    return static_features(intake) + [values.get(k) for k in METRICS]


def group_split(group):
    # Exactly the same hospital-ID group is never shared across partitions.
    bucket = int(group[:8], 16)
    return 'test' if bucket % 5 == 0 else 'validation' if (bucket // 5) % 5 == 0 else 'train'
