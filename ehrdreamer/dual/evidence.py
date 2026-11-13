"""Versioned, source-checked implementation summaries, not clinician validation.

Public bibliographic discovery stays in outputs/. Only these bounded summaries
and the user-provided PDF rules can enter actionable retrieval context.
"""
from __future__ import annotations

EVIDENCE_VERSION = 'neonatal-evidence-2026-11-12'

SOURCES = {
    'EU-RDS-2025': {'title': 'European Consensus Guidelines on the Management of RDS: 2025',
        'year': 2026, 'pmid': '41802129', 'url': 'https://doi.org/10.1159/000551062',
        'document_url':'https://oulurepo.oulu.fi/bitstream/handle/10024/61586/nbnfioulu-202604022440.pdf?isAllowed=y&sequence=1', 'kind': 'guideline',
        'scope': '早产儿RDS；产房稳定、稳定后及呼吸支持失败场景分别处理'},
    'CN-PNEUMONIA-2025': {'title': '基层医疗卫生机构常见新生儿疾病诊疗指南：新生儿感染性肺炎（2025年）',
        'year': 2025, 'pmid': '40962511', 'url': 'https://pubmed.ncbi.nlm.nih.gov/40962511/', 'kind': 'guideline', 'scope': '新生儿感染性肺炎'},
    'CN-SEPSIS-2024': {'title': '新生儿败血症诊断与治疗专家共识（2024）',
        'year': 2024, 'url': 'https://cspm.cma.org.cn/index/news?id=3634', 'kind': 'consensus', 'scope': '日龄≤28天或校正胎龄≤44周'},
    'CN-SEPSIS-2025': {'title': '基层医疗卫生机构常见新生儿疾病诊疗专家共识：新生儿败血症（2025年）',
        'year': 2025, 'url': 'https://cspm.cma.org.cn/index/news?id=3807', 'kind': 'consensus', 'scope': '基层新生儿感染识别、治疗与转诊'},
    'NICE-NG195': {'title': 'NICE NG195 Neonatal infection: antibiotics for prevention and treatment',
        'year': 2026, 'url': 'https://www.nice.org.uk/guidance/ng195',
        'document_url':'https://www.nice.org.uk/guidance/ng195/resources/neonatal-infection-antibiotics-for-prevention-and-treatment-pdf-66142083827653',
        'kind': 'guideline', 'scope': '截至2026-05-13更新；校正日龄≤28天的疑似/确诊细菌感染'},
    'AAP-EOS-TERM': {'title': 'Management of Neonates Born at ≥35 0/7 Weeks With Suspected or Proven EOS',
        'year': 2018, 'pmid': '30455342', 'url': 'https://pubmed.ncbi.nlm.nih.gov/30455342/', 'kind': 'clinical_report', 'scope': '胎龄≥35周；非所有早产儿'},
    'AAP-EOS-PRETERM': {'title': 'Management of Neonates Born at ≤34 6/7 Weeks With Suspected or Proven EOS',
        'year': 2018, 'pmid': '30455344', 'url': 'https://pubmed.ncbi.nlm.nih.gov/30455344/', 'kind': 'clinical_report', 'scope': '胎龄≤34+6周；结合出生情境分层'},
    'WHO-SBI-2024': {'title': 'WHO recommendations for management of serious bacterial infections in infants aged 0–59 days',
        'year': 2024, 'url': 'https://www.who.int/publications/i/item/9789240102903',
        'document_url':'https://iris.who.int/server/api/core/bitstreams/3ae5bd43-7196-43d2-8c77-be8725e21823/content',
        'kind': 'guideline', 'scope': '0–59天；住院与无法转诊场景分开'},
    'WHO-PRETERM-2022': {'title': 'WHO recommendations for care of the preterm or low-birth-weight infant',
        'year': 2022, 'url': 'https://www.who.int/publications/i/item/9789240058262',
        'document_url':'https://iris.who.int/server/api/core/bitstreams/707c8feb-e4e4-4e5c-b029-b78202c67cc7/content',
        'kind': 'guideline', 'scope': '早产或低出生体重婴儿'},
    'CPS-SURFACTANT': {'title': 'Canadian Paediatric Society: Guidelines for surfactant replacement therapy in neonates',
        'year': 2021, 'url': 'https://cps.ca/en/documents/position/guidelines-for-surfactant-replacement-therapy-in-neonates', 'kind': 'position_statement', 'scope': '新生儿表面活性物质治疗'},
    'AAP-BILIRUBIN-2022': {'title': 'AAP Clinical Practice Guideline Revision: Management of Hyperbilirubinemia',
        'year': 2022, 'pmid': '35927462', 'url': 'https://pubmed.ncbi.nlm.nih.gov/35927462/', 'kind': 'guideline', 'scope': '胎龄≥35周，按生后小时和神经毒性危险因素分层'},
    'NICE-CG98': {'title': 'NICE CG98 Jaundice in newborn babies under 28 days',
        'year': 2023, 'url': 'https://www.nice.org.uk/guidance/cg98', 'kind': 'guideline', 'scope': '出生28天内黄疸；使用适用胎龄曲线'},
    'BAPM-GLUCOSE-2024': {'title': 'BAPM Identification and Management of Neonatal Hypoglycaemia in the Full-Term Infant',
        'year': 2024, 'url': 'https://hubble-live-assets.s3.eu-west-1.amazonaws.com/bapm/file_asset/file/2864/Identification_and_Management_of_Neonatal_Hypoglycaemia_Sept_24.pdf', 'kind': 'framework', 'scope': '足月儿出生至72小时；不能外推至所有早产儿'},
    'RBC-GUIDELINE-2024': {'title': 'Clinical Practice Guideline for Red Blood Cell Transfusion Thresholds in Very Preterm Neonates',
        'year': 2024, 'pmid': '38874929', 'url': 'https://pubmed.ncbi.nlm.nih.gov/38874929/', 'kind': 'guideline', 'scope': '极早产儿；需结合生后周龄和呼吸支持'},
    'SUPPORT': {'title': 'Early CPAP versus surfactant in extremely preterm infants', 'year': 2010,
        'pmid': '20472939', 'url': 'https://pubmed.ncbi.nlm.nih.gov/20472939/', 'kind': 'rct', 'scope': '极早产儿；群体结局不是个体药效公式'},
    'COIN': {'title': 'Nasal CPAP or intubation at birth for very preterm infants', 'year': 2008,
        'pmid': '18272893', 'url': 'https://pubmed.ncbi.nlm.nih.gov/18272893/', 'kind': 'rct', 'scope': '出生时的早产儿呼吸支持选择'},
    'OPTIMIST-A': {'title': 'OPTIMIST-A: Minimally invasive surfactant therapy vs sham', 'year': 2021,
        'pmid': '34902013', 'url': 'https://pubmed.ncbi.nlm.nih.gov/34902013/', 'kind': 'rct', 'scope': '25–28周、早期CPAP患儿；主要复合结局未达统计学显著差异'},
    'OPTIMIST-A2': {'title': 'Two-Year Outcomes After Minimally Invasive Surfactant Therapy', 'year': 2023,
        'pmid': '37695601', 'url': 'https://pubmed.ncbi.nlm.nih.gov/37695601/', 'kind': 'rct_followup', 'scope': 'OPTIMIST-A长期随访；不能等同于即时血气预测'},
    'NEOPINS': {'title': 'NeoPInS: Procalcitonin-guided antibiotic duration', 'year': 2017,
        'pmid': '28711318', 'url': 'https://pubmed.ncbi.nlm.nih.gov/28711318/', 'kind': 'rct', 'scope': '胎龄≥34周疑似EOS；依据生后时龄PCT规则及连续测量，不是单点通用阈值'},
    'CAP': {'title': 'Caffeine therapy for apnea of prematurity', 'year': 2006,
        'pmid': '16707748', 'url': 'https://pubmed.ncbi.nlm.nih.gov/16707748/', 'kind': 'rct', 'scope': '低出生体重早产儿；不是治疗所有血气异常'},
    'NEOPROM': {'title': 'NeOProM: Oxygen saturation targeting and death or disability', 'year': 2018,
        'pmid': '29872859', 'url': 'https://pubmed.ncbi.nlm.nih.gov/29872859/', 'kind': 'individual_patient_meta_analysis', 'scope': '极早产儿氧目标；较低目标不能被当成安全降氧捷径'},
}

# id, summary, sources, concepts, treatments, monitoring, population constraints
RECOMMENDATIONS = [
    ('CARE-ASSESS', '先核实感染风险、呼吸表现、生命体征和已有治疗。正常的pH/PaCO2或异常计数归零不能证明没有肺炎、RDS或感染。', ['CN-PNEUMONIA-2025', 'NICE-NG195'], ['assessment', 'infection', 'respiratory'], [], ['vitals'], {}),
    ('OBS-LOW-RISK', '无必须治疗的临床指征、经临床评估稳定且符合感染风险分层时可观察；必须记录依据、监护和复评安排。观察不是维持尚未开始的治疗。', ['NICE-NG195', 'AAP-EOS-TERM'], ['observation', 'infection', 'assessment'], ['observation'], ['vitals', 'infection_panel'], {'ga_min': 35}),
    ('EOS-RED-FLAGS', '呼吸暂停、惊厥、休克或需复苏/机械通气等感染危险征象不能仅靠等待预测血气正常来解除，应及时评估并处理。', ['NICE-NG195', 'WHO-SBI-2024'], ['infection', 'safety', 'respiratory'], ['antimicrobial', 'invasive_ventilation'], ['vitals', 'blood_gas', 'culture'], {}),
    ('EOS-ANTIBIOTIC', '有疑似细菌感染治疗指征时及时开始经验性抗菌治疗；能及时取得标本时先留病原学样本，不能等待培养最终结果才处理。', ['CN-SEPSIS-2024', 'NICE-NG195', 'WHO-SBI-2024'], ['infection', 'pneumonia'], ['antimicrobial'], ['culture', 'infection_panel'], {}),
    ('EOS-NO-ROUTINE-ANTIBIOTIC', '没有感染依据时不因早产、一个CRP/WBC值或RDS标签机械使用抗生素。需将病史、体征及风险分层一起判断。', ['CN-SEPSIS-2024', 'NICE-NG195', 'AAP-EOS-PRETERM'], ['infection', 'rds', 'observation'], ['antimicrobial', 'observation'], ['infection_panel', 'vitals'], {}),
    ('EOS-REASSESS', '经验性抗菌治疗后结合临床病程、培养及连续炎症指标复评；中国共识的48–72小时复评与其他指南的特定早停情境不能混为统一疗程。', ['CN-SEPSIS-2024', 'NICE-NG195'], ['infection'], ['antimicrobial'], ['culture', 'infection_panel'], {}),
    ('EOS-LABS-LIMIT', '血常规、CRP/PCT只能辅助感染评估；单次结果正常不能排除EOS，异常也不等同于细菌感染确诊。', ['CN-SEPSIS-2024', 'AAP-EOS-TERM', 'AAP-EOS-PRETERM'], ['laboratory', 'infection'], ['antimicrobial'], ['cbc', 'infection_panel'], {}),
    ('PCT-AGE', 'PCT停药研究使用特定胎龄、时龄规则和连续测量；不从NeoPInS推导一个适用于所有新生儿的固定PCT正常值。', ['NEOPINS', 'CN-SEPSIS-2024'], ['laboratory', 'infection'], ['antimicrobial'], ['infection_panel'], {'ga_min': 34}),
    ('PN-DIAGNOSIS', '肺炎评估需综合症状、围产风险、影像和病原学，鉴别RDS、暂时性呼吸增快、胎粪吸入及先心病。', ['CN-PNEUMONIA-2025'], ['pneumonia', 'respiratory', 'assessment'], [], ['imaging', 'infection_panel', 'culture'], {}),
    ('PN-CULTURE-NEGATIVE', '仍有临床/影像依据的细菌性肺炎不能仅因培养阴性或血气恢复就提前认定无需治疗；按病情和病原资料调整。', ['CN-PNEUMONIA-2025'], ['pneumonia', 'infection'], ['antimicrobial'], ['culture', 'infection_panel'], {}),
    ('PN-RESPIRATORY', '低氧或呼吸衰竭时评估氧疗及呼吸支持；以SpO2和临床表现监测，必要时结合血气，不能只看pH和PaCO2。', ['CN-PNEUMONIA-2025'], ['pneumonia', 'respiratory'], ['oxygen', 'cpap', 'invasive_ventilation'], ['vitals', 'blood_gas'], {}),
    ('RDS-CPAP', '有RDS风险且可自主呼吸、无需插管维持稳定的早产儿优先无创呼吸支持；根据需求选择CPAP或NIPPV并复评。', ['EU-RDS-2025', 'SUPPORT', 'COIN'], ['rds', 'respiratory'], ['cpap', 'nippv'], ['vitals', 'blood_gas'], {'ga_max_exclusive': 37}),
    ('RDS-PS', '早期加重RDS在无创支持下评估救援PS；>28周情境参考MAP≥6cmH2O、FiO2≥0.30，或肺超声提示PS需求；不能只凭异常血气给PS。', ['EU-RDS-2025', 'CPS-SURFACTANT'], ['rds'], ['surfactant'], ['vitals', 'blood_gas', 'imaging'], {'ga_min_exclusive': 28, 'ga_max_exclusive': 37}),
    ('RDS-EARLY-PS', '胎龄<28周且稳定后有早期RDS征象者，可在生后第1小时考虑选择性预防PS；不能把入院0小时误当作出生0小时。', ['EU-RDS-2025'], ['rds'], ['surfactant'], ['vitals'], {'ga_max_exclusive': 28, 'age_max_exclusive': 1}),
    ('RDS-INTUBATED-PS', '<32周且需插管维持稳定时尽早评估PS；重复给药须有持续RDS依据并排除其他原因。', ['EU-RDS-2025'], ['rds'], ['surfactant', 'invasive_ventilation'], ['vitals', 'blood_gas'], {'ga_max_exclusive': 32}),
    ('RDS-LISA', '自主呼吸早产儿可优先细导管给PS；OPTIMIST-A并不证明每名患儿均获益，不能转写为确定的化验变化幅度。', ['EU-RDS-2025', 'OPTIMIST-A', 'OPTIMIST-A2'], ['rds', 'safety'], ['surfactant'], ['vitals', 'blood_gas'], {'ga_max_exclusive': 37}),
    ('RDS-OXYGEN', '稳定后的氧目标须按胎龄及情境设定；不能混用产房复苏氧浓度、SpO2目标与血气SaO2。避免为了少一项异常而过度给氧。', ['EU-RDS-2025', 'NEOPROM'], ['rds', 'respiratory', 'safety'], ['oxygen', 'cpap', 'nippv'], ['vitals', 'blood_gas'], {'ga_max_exclusive': 37}),
    ('RDS-VENTILATION', '无创支持失败时评估有创肺保护通气，而不是连续等待血气自行恢复；不能由一次数值推断全部插管指征。', ['EU-RDS-2025'], ['rds', 'respiratory', 'safety'], ['invasive_ventilation', 'nippv'], ['vitals', 'blood_gas'], {'ga_max_exclusive': 37}),
    ('RDS-CAFFEINE', '按早产儿胎龄、呼吸暂停及呼吸支持情境评估咖啡因，不每轮重复负荷；此治疗不是所有异常化验的通用修复动作。', ['EU-RDS-2025', 'CAP'], ['rds', 'apnea'], ['caffeine'], ['vitals'], {'ga_max_exclusive': 32}),
    ('RDS-INFECTION', 'RDS本身不等于感染。停经验抗菌药需满足相应感染排除条件；不能把RDS排除感染后的早停规则套到仍疑似细菌肺炎者。', ['EU-RDS-2025', 'CN-PNEUMONIA-2025'], ['rds', 'infection', 'pneumonia'], ['antimicrobial'], ['culture', 'infection_panel'], {}),
    ('SUPPORT-THERMAL', '早产/低出生体重儿需要体温和营养支持，液体、电解质及喂养按体重、尿量和实际监测个体化调整；不自动编造缺失输入。', ['WHO-PRETERM-2022', 'EU-RDS-2025'], ['supportive', 'rds'], ['thermal_nutrition', 'fluid_electrolyte', 'feeding_support'], ['vitals', 'electrolytes', 'renal', 'glucose'], {'preterm_or_low_birth_weight':True}),
    ('BILI-AGE-CURVE', '光疗决策需总胆红素、生后小时、胎龄与危险因素；不能把胆红素纳入单一固定正常区间，也不能对所有早产儿套AAP≥35周曲线。', ['AAP-BILIRUBIN-2022', 'NICE-CG98'], ['jaundice', 'laboratory'], ['phototherapy'], ['bilirubin'], {}),
    ('GLUCOSE-AGE', '低血糖须结合症状、日龄、测量方法和适用胎龄及时评估；BAPM足月儿出生72小时框架不能外推至所有早产儿。', ['BAPM-GLUCOSE-2024'], ['glucose', 'supportive'], ['feeding_support', 'fluid_electrolyte'], ['glucose', 'vitals'], {'ga_min': 37, 'age_max': 72}),
    ('RBC-THRESHOLD', '极早产儿输血评估取决于生后周龄、呼吸支持及血红蛋白等资料，不因单项Hb偏离成人范围就输血。', ['RBC-GUIDELINE-2024'], ['hematology', 'supportive'], ['transfusion'], ['cbc', 'vitals'], {'ga_max_exclusive': 30}),
]

CONCEPTS = {
    'assessment': ('入院综合评估', ['评估', '诊断', '查体', '主诉']),
    'observation': ('有依据的临床观察', ['观察', '等待', 'watchful waiting']),
    'infection': ('新生儿感染/败血症评估', ['感染', '败血症', 'EOS', 'LOS', 'sepsis', 'CRP', 'PCT']),
    'pneumonia': ('新生儿肺炎', ['肺炎', 'pneumonia']),
    'respiratory': ('新生儿呼吸窘迫', ['气促', '呼吸困难', '呻吟', '发绀', '低氧', '血气', 'PaCO2', 'SpO2']),
    'rds': ('早产儿RDS', ['RDS', '呼吸窘迫综合征', '肺透明膜病', '早产']),
    'apnea': ('早产儿呼吸暂停', ['呼吸暂停', 'apnea']),
    'supportive': ('支持与营养治疗', ['营养', '保暖', '电解质', '补液']),
    'laboratory': ('新生儿检验解读', ['化验', '检验', '血常规', '参考范围']),
    'safety': ('危险征象及安全约束', ['惊厥', '休克', '复苏', '恶化', '禁忌']),
    'jaundice': ('新生儿黄疸', ['黄疸', '黄染', '胆红素']),
    'glucose': ('新生儿血糖评估', ['血糖', '葡萄糖', '低血糖', 'cGlu']),
    'hematology': ('血液学评估', ['血红蛋白', 'Hb', '输血', '血小板']),
}

TREATMENT_ALIASES = {
    'oxygen': ['氧疗', '吸氧', 'supplemental oxygen'], 'cpap': ['CPAP', 'nCPAP', '持续气道正压'],
    'nippv': ['NIPPV', '经鼻间歇正压通气'], 'invasive_ventilation': ['气管插管', '有创通气', '机械通气'],
    'surfactant': ['PS', '肺表面活性物质', '固尔苏', 'LISA'], 'antimicrobial': ['抗菌治疗', '抗感染', '抗生素'],
    'caffeine': ['咖啡因', '枸橼酸咖啡因'], 'thermal_nutrition': ['保温及营养支持', '暖箱', '保暖'],
    'feeding_support': ['喂养支持', '母乳', '营养支持'], 'fluid_electrolyte': ['液体电解质管理', '补液', '电解质调整'],
    'phototherapy': ['光疗', '蓝光'], 'transfusion': ['输血', '红细胞输注'],
    'observation': ['临床观察', '不启动治疗', 'watchful waiting'],
}

PANELS = {
    'vitals': ('生命体征', ['SpO2', '心率', '呼吸频率', '体温']),
    'blood_gas': ('血气分析', ['pH', 'PaCO2', 'SaO2', 'sO2', 'pO2', 'ctHb', 'cBase', '碳酸氢根', 'cK+', 'cNa+', 'cCl-', 'cGlu', 'cLac']),
    'cbc': ('血常规', ['WBC', '白细胞', '红细胞', '血红蛋白', '血小板', '粒细胞', '淋巴细胞', '单核细胞']),
    'infection_panel': ('感染相关检验', ['CRP', 'PCT', 'C反应蛋白', '降钙素原', '血清淀粉样蛋白']),
    'culture': ('病原学培养', ['血培养', '培养', '药敏']), 'imaging': ('肺部影像', ['肺超声', '胸片', 'X线']),
    'electrolytes': ('电解质', ['钠', '钾', '氯', '钙', '磷']), 'renal': ('肾功能', ['肌酐', '尿素']),
    'glucose': ('血糖', ['血糖', '葡萄糖', 'cGlu']), 'bilirubin': ('胆红素', ['胆红素', 'ctBil']),
}

# A warning against routine prescribing must never authorize that prescription.
CONSTRAINT_ONLY = {'EOS-NO-ROUTINE-ANTIBIOTIC','EOS-LABS-LIMIT','PCT-AGE','RDS-INFECTION','RDS-ANTIBIOTIC'}
LOCAL_BINDINGS = {
    'PN-ANTIBIOTIC': (['antimicrobial'], ['culture','infection_panel']),
    'PN-NEGATIVE-CULTURE': (['antimicrobial'], ['culture','infection_panel']),
    'PN-ESCALATE': (['invasive_ventilation'], ['vitals','blood_gas']),
    'PN-CULTURE': ([], ['culture']), 'PN-IMAGING': ([], ['imaging']),
    'RDS-SUPPORT': (['thermal_nutrition','fluid_electrolyte','feeding_support'], ['vitals','electrolytes','renal']),
    'RDS-ANTIBIOTIC': (['antimicrobial'], ['culture','infection_panel']),
}
