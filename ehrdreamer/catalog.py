"""Action/check vocabulary. Patient values come from trained local models."""
VERSION = 'dreamer-ml-2.1'
PHASES = {'assessment': '治疗前 · 诊断评估', 'treatment': '治疗中 · 治疗与监测', 'discharge': '治疗后 · 结局'}
CONDITIONS = {'pneumonia': '新生儿肺炎（工作诊断）', 'rds': '新生儿RDS（工作诊断）',
              'infection_risk': '新生儿感染风险', 'respiratory_distress': '新生儿呼吸窘迫待查',
              'jaundice': '新生儿黄疸', 'neonatal_observation': '新生儿观察评估（未确诊疾病）'}
EXAMS = {
    'vitals': {'name': '生命体征与呼吸支持评估', 'delay': 0},
    'blood_gas': {'name': '血气分析', 'delay': .5},
    'infection_panel': {'name': '感染相关检验面板', 'delay': 2},
    'chest_xray': {'name': '胸部X线表现类别', 'delay': 0},
    'lung_ultrasound': {'name': '肺部超声报告', 'delay': 1},
    'culture': {'name': '血培养', 'delay': 24},
    'nutrition': {'name': '喂养、体重与液体平衡评估', 'delay': .25},
    'bilirubin': {'name': '胆红素检查', 'delay': 1},
}
TREATMENTS = {
    'supportive_care': {'name':'一般支持与观察计划','action_covariate':'care_plan_only_no_fitted_physiological_effect'},
    'oxygen': {'name': '氧疗支持', 'action_covariate': 'documented_date_coverage_only'},
    'cpap': {'name': '经鼻CPAP支持', 'action_covariate': 'documented_date_coverage_only'},
    'antimicrobial': {'name': '抗感染方案（研究级抽象干预）', 'action_covariate': 'documented_date_coverage_only'},
    'surfactant': {'name': '肺表面活性物质方案（研究级抽象干预）', 'action_covariate': 'documented_date_coverage_only'},
    'thermal_nutrition': {'name': '保温与营养支持', 'action_covariate': 'documented_date_coverage_only'},
    'phototherapy': {'name': '光疗支持方案', 'action_covariate': 'documented_date_coverage_only'},
}
UNITS = {'spo2': '%', 'respiratory_rate': '次/min', 'heart_rate': 'bpm', 'temperature': '°C',
         'fio2': 'fraction', 'cpap': 'cmH2O', 'ph': 'unitless', 'paco2': 'mmHg', 'crp': 'mg/L',
         'pct': 'ng/mL', 'wbc': '10^9/L', 'feeding': 'fraction', 'urine_output': 'mL/kg/h',
         'weight_g': 'g', 'bilirubin': 'µmol/L'}
LABELS = {'spo2': '血氧饱和度', 'respiratory_rate': '呼吸频率', 'heart_rate': '心率', 'temperature': '体温',
          'fio2': '吸入氧浓度', 'cpap': 'CPAP压力', 'ph': '血气pH', 'paco2': 'PaCO₂', 'crp': 'CRP',
          'pct': 'PCT', 'wbc': '白细胞计数', 'feeding': '喂养耐受', 'urine_output': '尿量',
          'weight_g': '模拟当前体重', 'bilirubin': '总胆红素', 'finding': '报告所见', 'culture': '培养结果'}
FOLLOW_UP = {'neonatal_review': '按新生儿专科安排复诊，复核未解决问题。',
             'respiratory_review': '复诊评估呼吸症状与支持需求。', 'feeding_weight_review': '随访喂养情况和体重变化。',
             'infection_review': '复核感染评估与后续报告，具体用药由专科核定。',
             'jaundice_review': '根据专科安排随访黄疸。', 'receiving_hospital': '向接收医院交接病情、已执行操作和待报项目。'}
LIMITATIONS = ['检查值与日级检验变化由真实数据训练；生命体征后续动态、剂量响应和因果治疗效应尚未识别。',
               '抽象治疗方案不等于具有药名、剂量和给药频次的真实处方。',
               '报告延迟和结局门控为模拟设置，不是统一临床诊疗标准。']
