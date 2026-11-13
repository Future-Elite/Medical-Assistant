"""Observed records only: no daily interpolation, medians or invented execution."""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import date, timedelta

from ehrdreamer.learning.dataset import DATE, calendar, dated_blocks

ALIASES = {
    'ph': ('ph', 'ph值', '血气ph'), 'paco2': ('paco2', 'pco2', '动脉血二氧化碳分压'),
    'sao2': ('sao2', 'so2', '血气氧饱和度'), 'spo2': ('spo2', '经皮血氧饱和度'),
    'crp': ('c反应蛋白', '超敏c反应蛋白', 'crp'), 'pct': ('降钙素原', 'pct'),
    'wbc': ('白细胞', '白细胞计数', 'wbc'), 'bilirubin': ('总胆红素', '胆红素测定'),
    'hemoglobin': ('血红蛋白', '血红蛋白测定', 'hgb', 'hb'), 'platelet': ('血小板', '血小板计数', 'plt'),
    'sodium': ('钠', 'na+'), 'potassium': ('钾', 'k+'), 'chloride': ('氯', 'cl-'),
    'glucose': ('葡萄糖', '血糖', 'glu'), 'lactate': ('乳酸', 'lac'),
    'calcium_total': ('钙', '总钙'), 'calcium_ionized': ('离子钙', 'ca2+'),
    'creatinine': ('肌酐', 'crea'), 'urea': ('尿素',), 'albumin': ('白蛋白',),
}


def normalized(text):
    return re.sub(r'\s+', '', text.lower().replace('₂', '2').replace('＋', '+').replace('－', '-'))


ALIAS_KEY = {normalized(name): key for key, names in ALIASES.items() for name in names}
LABEL = r'(?:[\u4e00-\u9fffA-Za-zαβγµμ0-9₂₃+%/\-· ]|\([^()\r\n:：]{1,40}\)|（[^（）\r\n:：]{1,40}）){1,55}'
NUMBER = re.compile(r'(?P<name>' + LABEL + r')[:：]\s*(?P<op><=|>=|[<>≤≥])?\s*'
                    r'(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)'
                    r'(?P<unit>[^,，;；\n:：↑↓]{0,35}?)(?P<flag>[↑↓])?(?=[,，;；\n]|$)', re.I)
QUALITATIVE = re.compile(r'(?P<name>' + LABEL + r')[:：]\s*(?P<text>[^,，;；\n:：]{1,120})(?=[,，;；\n]|$)')
REPORT_HEADER = re.compile(r'[(（](?:\d{5,}|(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{10,})[)）]|报告|检查所见|检查结论|影像诊断|(?:西院)?彩超|(?:床边)?[Xx]线')
THERAPY_PATTERNS = {
    'oxygen': r'吸氧|氧疗', 'cpap': r'CPAP', 'nippv': r'NIPPV',
    'noninvasive_support': r'无创(?:通气|呼吸)|无创/呼吸',
    'respiratory_support_unspecified': r'呼吸机辅助通气',
    'invasive_ventilation': r'气管插管|(?<!无)有创(?:通气|机械通气)',
    'surfactant': r'固尔苏|肺表面活性|PS治疗',
    'antimicrobial': r'头孢[\u4e00-\u9fff]{0,4}|青霉素|氨苄西林|美罗培南|万古霉素|阿奇霉素|优立新|美平|邦达|抗感染|抗生素|抗炎治疗',
    'phototherapy': r'光疗|蓝光', 'thermal_nutrition': r'保暖|保温|暖箱|静脉营养|补液',
    'feeding': r'开奶|加奶|禁食|减少奶量|强化喂养', 'caffeine': r'咖啡因',
    'vasopressor': r'多巴胺|多巴酚丁胺|肾上腺素', 'transfusion': r'输注红细胞|输血|输注血小板',
    'immunoglobulin': r'丙球|免疫球蛋白', 'thyroid_replacement': r'优甲乐|左甲状腺素',
    'gi_decompression': r'胃肠(?:道)?减压', 'iron_vitamins': r'铁剂|维生素|VitD|VitAD',
}
MD_DATE = re.compile(r'(?<![\d.])(?P<m>\d{1,2})[./月](?P<d>\d{1,2})(?:日|号)?'
                     r'(?![\d.]|\s*(?:mg|ml|g/|g\b|cm|%|毫|克))', re.I)


def metric(name, unit):
    name = name.strip().lstrip('、*')
    # Some source cells omit a comma between the preceding value/unit and the
    # next label. Remove that explicit numerical prefix, not the analyte itself.
    name = re.sub(r'^\d+(?:[A-Za-zµμ%/^×*+\-]+)?\s+(?=[A-Za-z\u4e00-\u9fff])','',name)
    key = ALIAS_KEY.get(normalized(name), 'lab_' + hashlib.sha256(normalized(name).encode()).hexdigest()[:12])
    unit = re.sub(r'\s+', '', unit).replace('μ', 'µ').rstrip('。.;、') if unit else ''
    unit = re.sub(r'^u(?=mol|g|l|L)', 'µ', unit)
    if normalized(unit) in ('mmhg',): unit = 'mmHg'
    scale = 1.
    if key == 'ph': unit = 'unitless'
    elif key == 'paco2' and unit.lower() == 'kpa': unit, scale = 'mmHg', 7.50062
    elif key in ('crp',) and unit.lower() == 'mg/l': unit = 'mg/L'
    elif key == 'pct' and unit.lower() == 'ng/ml': unit = 'ng/mL'
    elif key == 'wbc' and re.fullmatch(r'[×x*]?10\^?9/L', unit, re.I): unit = '10^9/L'
    elif key == 'bilirubin' and unit.lower() == 'mg/dl': unit, scale = 'µmol/L', 17.104
    elif key == 'bilirubin' and unit.lower() == 'µmol/l': unit = 'µmol/L'
    return key, name, unit, scale


def extract_lab_block(text, *, row, field, offset, day, precision='calendar_day'):
    results = []; numeric_spans = []
    for match in NUMBER.finditer(text):
        key, name, unit, scale = metric(match['name'], match['unit'].strip())
        value = float(match['value']) * scale
        if not name or not re.search(r'[A-Za-z\u4e00-\u9fff]',name) or REPORT_HEADER.search(name) or not math.isfinite(value): continue
        if '→' in unit or '←' in unit:continue
        # Enumerated imaging prose ("1.双侧...") is not a numerical lab value.
        if re.search(r'[\u4e00-\u9fff]',unit) and unit not in ('秒','次/分','次/min','摄氏度','个/HP','个/LP'):continue
        op = {'<=': '≤', '>=': '≥'}.get(match['op'], match['op'])
        event_id = f'r{row}:{field}:{offset+match.start()}'
        results.append({'event_id': event_id, 'day': day, 'key': key, 'name': name, 'unit': unit,
                        'value': None if op else value, 'comparator': op, 'bound': value if op else None,
                        'kind': 'numeric', 'source_flag': match['flag'] or None,
                        'time_precision': precision, 'source_field': field,
                        'source_span': [offset+match.start(), offset+match.end()]})
        numeric_spans.append(match.span())
    for match in QUALITATIVE.finditer(text):
        if any(left <= match.start() < right for left, right in numeric_spans): continue
        name, value = match['name'].strip(), match['text'].strip()
        key, name, _, _ = metric(name, '')
        if len(value)>100 or not re.search(r'[A-Za-z\u4e00-\u9fff]',name) or REPORT_HEADER.search(name): continue
        # Keep qualitative culture results such as "5天培养未生长" without
        # interpreting the duration as a laboratory concentration.
        if re.fullmatch(r'\s*[<>≤≥]?\s*[-+]?\d+(?:\.\d+)?[^\u4e00-\u9fff]*',value):continue
        results.append({'event_id': f'r{row}:{field}:{offset+match.start()}', 'day': day,
                        'key': key, 'name': name, 'unit': '', 'value': None, 'comparator': None,
                        'bound': None, 'kind': 'qualitative', 'text': value,
                        'source_flag': None, 'time_precision': precision, 'source_field': field,
                        'source_span': [offset+match.start(), offset+match.end()]})
    return sorted(results, key=lambda item:item['source_span'][0])


def labs_from_row(raw, row):
    admission, discharge = calendar(raw.get('入院时间')), calendar(raw.get('出院时间'))
    results, excluded = [], 0
    if not admission or not discharge or discharge < admission: return [], {'invalid_encounter_dates': 1}
    for field in ('实验室检查及主要会诊', '特殊检验及重要会诊'):
        for day, text, left, _ in dated_blocks(raw.get(field, '')):
            if not admission <= day <= discharge:
                excluded += 1; continue
            marker = DATE.match(raw[field], left)
            results.extend(extract_lab_block(text, row=row, field=field, offset=marker.end(), day=(day-admission).days))
    field = '入院时主要症状及体征'; text = re.split(r'入院后|治疗后|经治疗|出院', raw.get(field, ''))[0]
    text = re.sub(r'(?:孕母|母亲|母体|孕妇|其母)[^。；;\n]*', lambda m:' '*len(m[0]), text)
    admission_items = extract_lab_block(text, row=row, field=field, offset=0, day=0, precision='admission_section')
    # Admission prose contributes explicit known lab labels only, not numeric biographical fields.
    results += [item for item in admission_items if item['key'] in ALIASES]
    return results, {'outside_encounter_lab_blocks':excluded}


def unique_day_state(items):
    """Preserve conflicting same-day results; never infer their within-day order."""
    buckets = defaultdict(list)
    for item in items: buckets[(item['key'],item['unit'])].append(item)
    state = {}
    key_counts = defaultdict(int)
    for key, unit in buckets: key_counts[key] += 1
    for (key, unit), values in buckets.items():
        distinct = {(i.get('value'),i.get('comparator'),i.get('bound'),i.get('text')) for i in values}
        projected = {k:v for k,v in values[0].items() if k not in ('source_field','source_span')}
        if len(distinct)>1 or key_counts[key]>1:
            projected.update(value=None, comparator=None, bound=None, ambiguous_same_day=True,
                             same_day_results=[{k:i.get(k) for k in ('event_id','value','comparator','bound','text','unit','source_flag')} for i in values])
        else: projected['ambiguous_same_day'] = False
        projected['evidence_ids'] = [i['event_id'] for i in values]
        storage_key = key if key_counts[key]==1 else key+':'+hashlib.sha256(unit.encode()).hexdigest()[:6]
        state[storage_key] = projected
    return state


def date_tokens(text, admission, discharge):
    """Full or uniquely resolvable month/day dates. No guessed year across ambiguities."""
    tokens = []; occupied = []
    for match in DATE.finditer(text):
        day = calendar(match[0]);occupied.append(match.span())
        if day and admission <= day <= discharge: tokens.append((match.start(),match.end(),day))
    for match in MD_DATE.finditer(text):
        if any(a<=match.start()<b for a,b in occupied): continue
        choices = []
        for year in range(admission.year, discharge.year+1):
            try: day=date(year,int(match['m']),int(match['d']))
            except ValueError: continue
            if admission<=day<=discharge: choices.append(day)
        if len(choices)==1: tokens.append((match.start(),match.end(),choices[0]))
    return sorted(tokens)


def treatments_from_row(raw, row, *, deduplicate=True, treatment_patterns=None):
    admission, discharge = calendar(raw.get('入院时间')), calendar(raw.get('出院时间'))
    course = raw.get('诊疗过程',''); events=[]; seen=set()
    for clause in re.finditer(r'[^。；;\n]+',course):
        # Commas often separate different regimens; do not assign one regimen's dates to another.
        for part in re.finditer(r'[^，,]+',clause[0]):
            text=part[0]; base=clause.start()+part.start(); mentions=[]
            for target,pattern in (treatment_patterns or THERAPY_PATTERNS).items():
                if target=='respiratory_support_unspecified' and re.search(r'无创|CPAP|NIPPV|气管插管|有创',text,re.I):continue
                for match in re.finditer(pattern,text,re.I):
                    if target=='noninvasive_support' and re.search(r'CPAP|NIPPV',text,re.I) and not re.fullmatch(r'Biph|BiPAP',match[0],re.I):continue
                    if re.search(r'未予|未用|不予|无需|未行|未曾|未光疗',text[max(0,match.start()-5):match.end()]): continue
                    if target=='oxygen' and re.search(r'未吸氧|未予吸氧|无需吸氧',text[max(0,match.start()-4):match.end()]):continue
                    mentions.append((match.start(),match.end(),target))
            if not mentions:continue
            tokens=date_tokens(text,admission,discharge) if admission and discharge and admission<=discharge else []
            for start,end,target in sorted(mentions):
                prefix=text[max(0,start-7):start]
                operation='stop' if re.search(r'停用|停止|撤除|撤离|停|撤',prefix) else 'adjust' if re.search(r'调整|减量|改为|改用|升级',prefix) else 'documented_treatment'
                begin=finish=None;precision='undated'
                # A date interval must belong to this target, not a different intervening therapy.
                after=[t for t in tokens if t[0]>=end and not any(end<=m[0]<t[0] and m[2]!=target for m in mentions)]
                before=[t for t in tokens if t[1]<=start and not any(t[1]<=m[0]<start and m[2]!=target for m in mentions)]
                token=after[0] if after and (text[end:after[0][0]].strip().startswith(('（','(')) or not before) else before[-1] if before else None
                if token:
                    begin=token[2];precision='calendar_day'
                    later=[t for t in tokens if t[0]>=token[1]]
                    if token in after and later and re.fullmatch(r'\s*(?:日)?\s*[-—~～至]\s*',text[token[1]:later[0][0]]):finish=later[0][2]
                elif re.search(r'入院(?:后|时)?(?:予|给予|即|完善)|入科(?:后|时)?(?:予|给予)',text):
                    begin=admission;precision='admission_day_clause'
                fingerprint=(target,operation,begin,finish,base)
                if deduplicate and fingerprint in seen:continue
                seen.add(fingerprint)
                events.append({'event_id':f'r{row}:t:{base+start}', 'target':target,'operation':operation,
                               'day':(begin-admission).days if begin and admission else None,
                               'end_day':(finish-admission).days if finish and admission else None,
                               'time_precision':precision,'source_field':'诊疗过程','source_span':[base+start,base+end],
                               'same_day_order':'unknown', 'execution_evidence':'retrospective_summary_not_medication_administration_record'})
    return events
