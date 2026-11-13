"""All explicitly recorded admission-window numeric results, never future labs."""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict

from ehrdreamer.learning.dataset import COMPILED, DATE, calendar, dated_blocks
from ehrdreamer.learning.schema import METRICS
from ehrdreamer.rl_data.extract import extract_lab_block

KNOWN = set(METRICS) - {'weight_g', 'feeding'}


def clinical_findings(raw):
    text = next(_segments(raw))[1]
    def present(term):
        return any(not re.search(r'无|未见|否认|不伴|未发生|未出现',text[max(0,m.start()-6):m.start()]) for m in re.finditer(term,text))
    flags=[term for term in ('惊厥','休克','呼吸暂停','需心肺复苏') if present(term)]
    sick = bool(flags or present('反应差'))
    well = bool(re.search(r'一般情况(?:尚)?可|反应(?:尚)?可|反应良好|精神反应好', text))
    return {'clinically_well': False if sick else True if well else None, 'assessed_at_hours': 0,'red_flags':flags,
            'source': 'explicit_admission_examination',
            'note': '仅入院时一般情况记录，不等同于后续时点的实际临床评估。'}


def _signed(name):
    return bool(re.search(r'cBase|base.?excess|碱剩余|剩余碱|(?<![A-Za-z])BE(?:\b|[(_])', name, re.I))


def _segments(raw):
    text = re.split(r'入院后|治疗后|经治疗|复查|出院', raw.get('入院时主要症状及体征', ''))[0]
    text = re.sub(r'(?:孕母|母亲|母体|孕妇|其母)[^。；;\n]*', lambda m: ' ' * len(m[0]), text)
    yield '入院时主要症状及体征', text, 0, 'admission_section'
    admission = calendar(raw.get('入院时间', ''))
    if not admission:
        return
    for field in ('实验室检查及主要会诊', '特殊检验及重要会诊'):
        for day, block, start, _ in dated_blocks(raw.get(field, '')):
            if day != admission or re.search(r'(?:复查|治疗后|出院前)\s*$', raw[field][max(0, start-10):start]):
                continue
            marker = DATE.match(raw[field], start)
            block = re.split(r'复查|治疗后|出院', block)[0]
            block = re.sub(r'(?:孕母|母亲|母体|孕妇|其母)[^。；;\n]*', lambda m: ' ' * len(m[0]), block)
            yield field, block, marker.end() if marker else start, 'calendar_day'


def extract_admission(raw):
    buckets = defaultdict(list)
    for field, text, offset, precision in _segments(raw):
        items = extract_lab_block(text, row=0, field=field, offset=offset, day=0, precision=precision)
        # Admission prose must use explicit recognised analytes, not age/weight/IDs.
        items = [i for i in items if i['kind'] == 'numeric' and (field != '入院时主要症状及体征' or i['key'] in KNOWN)]
        covered = {(i['key'], i['unit']) for i in items}
        for key in KNOWN:
            for pattern, scale in COMPILED.get(key, ()):
                for match in pattern.finditer(text):
                    if (key, METRICS[key][1]) in covered:
                        continue
                    op = match['censor'] or None
                    value = float(match['value']) * scale
                    items.append({'key': key, 'name': METRICS[key][0], 'unit': METRICS[key][1],
                        'value': None if op else value, 'comparator': op, 'bound': value if op else None,
                        'source_span': [offset+match.start(), offset+match.end()], 'source_flag': None})
        for item in items:
            if re.search(r'住院号|门诊号|病案号|检查号|检验号|标本号|申请号|流水号|身份证|电话|姓名|(?:编号|编码|序号)$',item['name']):
                continue
            value = item.get('bound') if item.get('comparator') else item['value']
            signed = _signed(item['name'])
            if not math.isfinite(value) or value < 0 and not signed:
                continue
            if item['key'] == 'ph' and value > 14 or item['key'] in ('sao2', 'spo2') and value > 100:
                continue
            item = {**item, 'as_of_hours': 0, 'origin': 'source_baseline', 'allows_negative': signed,
                    'time_precision': precision,
                    'evidence': {'field': field, 'start': item['source_span'][0], 'end': item['source_span'][1]}}
            buckets[(item['key'], item['unit'])].append(item)
    counts = defaultdict(int)
    for key, _ in buckets:
        counts[key] += 1
    result = {}
    for (key, unit), items in buckets.items():
        storage_key = key if counts[key] == 1 else key + ':' + hashlib.sha256(unit.encode()).hexdigest()[:6]
        first = items[0]
        values = {(i['value'], i.get('comparator'), i.get('bound')) for i in items}
        first = {k: v for k, v in first.items() if k in ('name', 'unit', 'value', 'comparator', 'bound',
            'as_of_hours', 'origin', 'time_precision', 'evidence', 'source_flag', 'allows_negative')}
        first['key'] = storage_key
        first['analyte_key'] = key
        if len(values) > 1:
            first.update(value=None, comparator=None, bound=None, ambiguous_same_day=True,
                         admission_results=[{'value': i['value'], 'comparator': i.get('comparator'),
                                             'bound': i.get('bound'), 'evidence': i['evidence']} for i in items])
        result[storage_key] = first
    return result
