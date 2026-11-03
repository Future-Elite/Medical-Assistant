"""Deterministic, auditable extraction of observed values and dated transitions.

Extraction patterns identify labels, units and calendar dates. They never generate
physiology. Unmentioned treatment is unknown; censored values are not exact labels.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from ehrworld.io import ROOT, output_path
from ehrdreamer.source import SourceCatalog, extract_intake
from .schema import ACTIONS, FEATURES, METRICS, features, group_split, observation_features, number

DATE = re.compile(r'(?<!\d)(20\d{2})[.年/-](\d{1,2})[.月/-](\d{1,2})日?')
VALUE = r'\s*[:：=]?\s*(?P<censor>[<>≤≥]?)\s*(?P<value>\d+(?:\.\d+)?)\s*'
PATTERNS = {
    'spo2': [(r'(?:SpO2|SpO₂|经皮血氧饱和度|脉搏血氧饱和度)' + VALUE + r'%', 1)],
    'sao2': [(r'(?<![A-Za-z])(?:sO2|sO₂|SaO2)' + VALUE + r'%', 1)],
    'respiratory_rate': [(r'(?:呼吸频率|呼吸|(?<![A-Za-z])RR?|(?<![A-Za-z])R)' + VALUE + r'次[/／](?:分|min)', 1)],
    'heart_rate': [(r'(?:心率|脉搏|(?<![A-Za-z])HR|(?<![A-Za-z])P)' + VALUE + r'(?:次[/／](?:分|min)|bpm)', 1)],
    'temperature': [(r'(?:体温|(?<![A-Za-z])T)' + VALUE + r'(?:℃|°C|摄氏度)', 1)],
    'ph': [(r'(?:PH值|pH值|(?<![A-Za-z])PH|(?<![A-Za-z])pH)' + VALUE, 1)],
    'paco2': [(r'(?:动脉血二氧化碳分压|PaCO2|pCO2)' + VALUE + r'mm\s*H[Gg]', 1),
              (r'(?:动脉血二氧化碳分压|PaCO2|pCO2)' + VALUE + r'kPa', 7.50062)],
    'crp': [(r'(?:超敏C反应蛋白|C反应蛋白|(?<![A-Za-z])CRP)' + VALUE + r'mg[/／]L', 1)],
    'pct': [(r'(?:降钙素原|(?<![A-Za-z])PCT)' + VALUE + r'ng[/／]m[Ll]', 1)],
    'wbc': [(r'(?:白细胞计数|白细胞|WBC)' + VALUE + r'(?:[×x*]\s*)?10\^?9[/／]L', 1)],
    'bilirubin': [(r'(?:总胆红素|胆红素测定)' + VALUE + r'[uµμ]mol[/／]L', 1),
                  (r'(?:总胆红素|胆红素测定)' + VALUE + r'mg[/／]dL', 17.104)],
    'weight_g': [(r'(?<!出生)(?:当前体重|体重)' + VALUE + r'(?:kg|公斤)', 1000),
                 (r'(?<!出生)(?:当前体重|体重)' + VALUE + r'(?:g|克)', 1)],
}
COMPILED = {k: [(re.compile(p, re.I), scale) for p, scale in values] for k, values in PATTERNS.items()}
TREATMENT_TERMS = {
    'oxygen': r'吸氧|氧疗', 'cpap': r'CPAP|无创(?:通气|呼吸)|NIPPV',
    'antimicrobial': r'头孢\w*|青霉素|氨苄西林|美罗培南|万古霉素|抗感染|抗生素',
    'surfactant': r'肺表面活性|固尔苏|PS治疗', 'thermal_nutrition': r'保暖|保温|静脉营养|营养支持',
    'phototherapy': r'光疗|蓝光',
}


def observed_values(text, audit=None):
    buckets = defaultdict(list)
    for key, patterns in COMPILED.items():
        for pattern, scale in patterns:
            for match in pattern.finditer(text):
                if match['censor']:
                    if audit is not None:
                        audit['censored_not_used_as_exact'] += 1
                    continue
                value = float(match['value']) * scale
                # Representation checks only: these are not expected normal ranges.
                if value < 0 or (key == 'ph' and value > 14) or (key in ('spo2', 'sao2') and value > 100):
                    if audit is not None:
                        audit['invalid_representation'] += 1
                    continue
                buckets[key].append(value)
    return {k: median(values) for k, values in buckets.items()}


def calendar(value):
    match = DATE.search(str(value))
    try:
        return date(*map(int, match.groups())) if match else None
    except ValueError:
        return None


def dated_blocks(text):
    matches = list(DATE.finditer(text))
    for i, match in enumerate(matches):
        day = calendar(match[0])
        if day:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            yield day, text[match.end():end], match.start(), end


def image_label(text):
    # Derived supervision is explicit and versioned; diagnoses are not features.
    if not re.search(r'肺|胸', text):
        return None
    if re.search(r'未见明显异常|未见异常|两肺清晰|双肺清晰|两肺未见', text):
        return 'no_definite_abnormality'
    if re.search(r'实变|磨玻璃|弥漫|网格|白肺|透亮度减低|透明膜', text):
        return 'diffuse_or_airspace_abnormality'
    if re.search(r'纹理.*(?:增多|增粗|紊乱)|斑片|渗出|炎性|肺炎', text):
        return 'other_pulmonary_abnormality'
    return None


def treatment_intervals(course, admission, discharge, audit):
    result = defaultdict(list)
    if not admission or not discharge or discharge < admission:
        return result
    # A treatment mention must share a clause with an explicit date range.
    for clause in re.split(r'[。；;\n]', course):
        for target, terms in TREATMENT_TERMS.items():
            term = re.search(terms, clause, re.I)
            if not term:
                continue
            if re.search(r'未予|未用|不用|不予|无需',clause[max(0,term.start()-6):term.start()]):
                audit['negated_treatment_mentions_excluded'] += 1
                continue
            tail = clause[term.end():term.end() + 100]
            match = re.search(r'(?<!\d)(\d{1,2})[./月](\d{1,2})\s*(?:日)?\s*[-—~～至]\s*(\d{1,2})[./月](\d{1,2})', tail)
            if not match:
                audit['treatment_mentions_without_range'] += 1
                continue
            between=tail[:match.start()]
            if any(re.search(pattern,between,re.I) for other,pattern in TREATMENT_TERMS.items() if other!=target):
                audit['ambiguous_range_owned_by_another_treatment'] += 1
                continue
            m1, d1, m2, d2 = map(int, match.groups())
            candidates = []
            for year in range(admission.year, discharge.year + 1):
                try:
                    start, end = date(year, m1, d1), date(year + int(m2 < m1), m2, d2)
                except ValueError:
                    continue
                if admission <= start <= end <= discharge:
                    candidates.append((start, end))
            if len(candidates) == 1:
                result[target].append(candidates[0])
                audit['dated_treatment_intervals'] += 1
    return result


def action_features(intervals, start, end):
    result = {}
    for target in ACTIONS:
        if any(left <= start and end <= right for left, right in intervals.get(target, [])):
            result[target] = 1.0
        # Unknown, including outside a recorded range, is not a documented negative.
    return result


def add_row(rows, group, intake, values, *, source, elapsed=0, previous=None, delta=0, actions=None, ages=None, provenance=None):
    if values:
        rows.append({'group': group, 'split': group_split(group), 'source': source,
                     'x': features(intake, previous, elapsed, delta, actions, ages), 'y': values,
                     'provenance': provenance or {}, 'action_coverage': actions or {}})


def build_dataset(workbook=None, test_json=None):
    catalog = SourceCatalog(workbook, 'outputs/dreamer_ml/source.sqlite')
    catalog.ensure()
    initial, transitions, images = [], [], []
    audit = Counter()
    for case in catalog.rows:
        raw, intake, group = case['raw'], case['intake'], case['group_id']
        if any('重复就诊键' in f for f in case['quality_flags']):
            audit['duplicate_encounters_excluded'] += 1
            continue
        admission, discharge = calendar(raw.get('入院时间')), calendar(raw.get('出院时间'))
        # Admission document targets may include examination values. These target
        # values NEVER enter the static intake feature projection.
        narrative = re.split(r'入院后|经治疗|治疗后|辅助检查|实验室|出院', raw.get('入院时主要症状及体征', ''))[0]
        admission_values = observed_values(narrative, audit)
        if re.search(r'喂养(?:好|可|耐受)|吃奶(?:好|可)', narrative):
            admission_values['feeding'] = 1.0
        elif re.search(r'拒奶|喂养困难|吃奶差|喂养不耐受', narrative):
            admission_values['feeding'] = 0.0
        add_row(initial, group, intake, admission_values, source='xlsx_admission', provenance={'row':case['row'],'field':'入院时主要症状及体征'})
        if not admission or not discharge or discharge < admission:
            audit['invalid_date_encounters_no_transitions'] += 1
            continue
        daily = defaultdict(lambda: defaultdict(list))
        daily_refs = defaultdict(list)
        for field in ('实验室检查及主要会诊', '特殊检验及重要会诊'):
            for day, block, left, right in dated_blocks(raw.get(field, '')):
                if not admission <= day <= discharge:
                    audit['outside_encounter_blocks'] += 1
                    continue
                values = observed_values(block, audit)
                for metric, value in values.items():
                    daily[day][metric].append(value)
                daily_refs[day].append({'field':field,'span':[left,right]})
                modality = 'chest_xray' if re.search(r'X线|胸片|摄片|DR检查|床边胸', block, re.I) else 'lung_ultrasound' if re.search(r'肺.*超声|肺.*B超', block) else None
                label = image_label(block) if modality else None
                if label:
                    images.append({'group':group,'split':group_split(group),'target':modality,'label':label,
                                   'x':observation_features(intake, values), 'source':'xlsx','row':case['row']})
        intervals = treatment_intervals(raw.get('诊疗过程', ''), admission, discharge, audit)
        previous, previous_dates = {}, {}
        last_day = None
        for day in sorted(daily):
            values = {k:median(v) for k,v in daily[day].items()}
            elapsed = (day-admission).days*24
            if elapsed == 0:
                add_row(initial, group, intake, values, source='xlsx_first_calendar_day', elapsed=0,
                        provenance={'row':case['row'],'date_precision':'calendar_day','spans':daily_refs[day]})
            if last_day is not None and day > last_day:
                delta = (day-last_day).days*24
                # No invented hours or ordering within one calendar day.
                if delta <= 168:
                    actions = action_features(intervals, last_day, day)
                    add_row(transitions, group, intake, values, source='xlsx_dated_labs', elapsed=(last_day-admission).days*24,
                            previous=previous, delta=delta, actions=actions,
                            ages={m:(last_day-d).days*24 for m,d in previous_dates.items()},
                            provenance={'row':case['row'],'from_day':(last_day-admission).days,
                                        'to_day':(day-admission).days,'time_precision':'calendar_day',
                                        'treatment_alignment':'date_range_overlap_not_exact_administration',
                                        'spans':daily_refs[day]})
            previous.update(values)  # forward-fill observed PAST only
            previous_dates.update({m:day for m in values})
            last_day = day
    test_path = Path(test_json or ROOT/'test.json')
    if test_path.is_file():
        records = json.loads(test_path.read_text(encoding='utf-8-sig'))
        for index, row in enumerate(records):
            group = hashlib.sha256(str(row.get('patient_id') or f'test-row-{index}').strip().encode()).hexdigest()
            structured = row.get('clinical_structured') or {}
            dev, risk, status = (structured.get(k) or {} for k in ('development_factors','perinatal_risk','physiological_status'))
            risks = ['胎膜早破'] if risk.get('premature_rupture_of_membranes') is True else []
            if dev.get('multiple_gestation'):
                risks.append('多胎妊娠')
            intake = {'gestational_weeks':number(dev.get('gestational_age_weeks')),
                      'birth_weight_g':number(dev.get('birth_weight_g')), 'perinatal_risks':risks,
                      'delivery_mode':'cesarean' if 'cesarean' in str(risk.get('delivery_mode')) else 'unknown'}
            values = {m:number(status.get(k)) for m,k in [('heart_rate','pulse_bpm'),('respiratory_rate','respiration_rate_bpm'),('temperature','body_temperature_c')]}
            values = {k:v for k,v in values.items() if v is not None}
            values.update(observed_values(row.get('clinical_information') or '', audit))
            gas = status.get('blood_gas') or {}
            for metric,key in [('ph','ph'),('paco2','pa_co2_mmHg')]:
                entry = gas.get(key) if isinstance(gas,dict) else None
                value = number(entry.get('value')) if isinstance(entry,dict) else number(entry)
                if value is not None:
                    values[metric] = value
            # These rows have no validated exam timestamps. Cross-sectional only;
            # file order, image IDs and temporal comparison labels are NOT a clock.
            add_row(initial, group, intake, values, source='test_json_cross_sectional', provenance={'row':index,'time_precision':'unknown'})
            label = image_label(row.get('imaging_conclusion') or '')
            if label:
                images.append({'group':group,'split':group_split(group),'target':'chest_xray','label':label,
                               'x':observation_features(intake, values),'source':'test_json','row':index})
    hashes = {'workbook':catalog.metadata['source_sha256'],
              'test_json':hashlib.sha256(test_path.read_bytes()).hexdigest() if test_path.is_file() else None}
    dataset = {'schema':'dreamer-observed-training-2.0','feature_names':FEATURES,'source_hashes':hashes,'initial':initial,'transitions':transitions,'images':images,
               'audit':dict(audit),'metric_counts':{kind:dict(Counter(k for r in rows for k in r['y'])) for kind,rows in [('initial',initial),('transitions',transitions)]},
               'split_counts':{kind:dict(Counter(r['split'] for r in rows)) for kind,rows in [('initial',initial),('transitions',transitions),('images',images)]}}
    return dataset
