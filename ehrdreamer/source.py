"""Read-only workbook ingestion with a private archive and a strictly projected intake."""
from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import threading
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl

from ehrworld.io import ROOT, output_path
from .contracts import Intake

FIELDS = ['性别', '年龄', '住院号', '入院时间', '出院时间', '入院诊断', '出院诊断', '入院时主要症状及体征',
          '实验室检查及主要会诊', '特殊检验及重要会诊', '诊疗过程', '合并症', '出院时情况', '出院医嘱', '出院指导',
          '治疗结果', '来源文件', '孕周']
FIELD_ROLES = {
    '性别': '入院已知资料', '年龄': '核对年龄所属时点', '住院号': '仅本地关联与分组',
    '入院时间': '仅本地时间核对；模拟使用合成起点', '出院时间': '仅回顾性时程参考',
    '入院诊断': '隔离保存的入院判断，不作为检查前确诊', '出院诊断': '隔离保存的结局标签／训练分区群体先验',
    '入院时主要症状及体征': '仅提取检查前可知事实', '实验室检查及主要会诊': '隔离的检查事件参考',
    '特殊检验及重要会诊': '隔离的影像／会诊参考', '诊疗过程': '隔离的治疗动作与先后约束参考',
    '合并症': '隔离核对多病并存', '出院时情况': '隔离的终点参考', '出院医嘱': '隔离的随访参考',
    '出院指导': '隔离的指导参考', '治疗结果': '隔离的结局标签', '来源文件': '仅本地来源定位', '孕周': '孕周提取与冲突核对',
}


class SourceError(ValueError):
    pass


def value_text(value):
    return '' if value is None else value.isoformat() if isinstance(value, (date, datetime)) else str(value).strip()


def positive(text, term):
    for match in re.finditer(term, text):
        if not re.search(r'无|未见|否认|不伴', text[max(0, match.start()-5):match.start()]):
            return True
    return False


def extract_intake(row, sheet, row_number):
    """Build from an allowlist, not a redacted copy of the whole narrative."""
    narrative = row.get('入院时主要症状及体征', '')
    before = re.split(r'入院后|治疗后|经治疗|辅助检查|实验室|血常规|血气|胸片|初步诊断|入院诊断|出院|查体[：:]', narrative)[0]
    complaint_match = re.search(r'主诉\s*[：:]\s*([^。；;\n]+)', before)
    complaint_part = complaint_match.group(1) if complaint_match else before[:500]
    # Maternal complaints are risk factors, never infant symptoms. Some rows use
    # "母亲发热" as the chief complaint even when the infant has no fever.
    patient_complaint = re.sub(r'(?:孕母|母亲|母体|孕妇|其母|母)[^，,。；;\n]*', '', complaint_part)
    symptom_terms = ['呼吸急促', '气促', '呻吟', '呼吸困难', '三凹征', '发绀', '咳嗽', '反应差', '喂养困难', '黄疸', '黄染', '发热']
    symptoms = tuple(term for term in symptom_terms if positive(patient_complaint, re.escape(term)))
    risks = []
    for label, pattern in [('母体感染风险', r'孕母发热|母亲发热|母产前发热|孕母产前发热|羊膜炎|绒毛膜羊膜炎'),
                           ('胎膜早破', r'胎膜早破'), ('多胎妊娠', r'双胎|多胎'), ('剖宫产', r'剖宫产')]:
        if positive(before, pattern): risks.append(label)
    raw_ga = row.get('孕周', '')
    ga_match = re.fullmatch(r'(\d{2})(?:\.0+)?(?:周)?', raw_ga)
    weeks, days = (int(ga_match.group(1)), None) if ga_match else (None, None)
    text_ga = re.search(r'(?:胎龄|母孕|孕)\s*(\d{2})\s*(?:周\s*\+?\s*([0-6])\s*天?|\+\s*([0-6])\s*周|周)', before)
    flags, evidence = [], []
    if text_ga:
        found_weeks = int(text_ga.group(1))
        if weeks is not None and weeks != found_weeks:
            flags.append('孕周列与入院叙述冲突，未自动择一')
            weeks = None
        else:
            weeks = found_weeks
            days = int(text_ga.group(2) or text_ga.group(3)) if text_ga.group(2) or text_ga.group(3) else None
    if weeks is not None and not 20 <= weeks <= 44:
        weeks = None; flags.append('孕周超出可表示范围')
    if weeks is None: flags.append('检查前孕周未知')
    age = None
    if re.search(r'患儿[^。\n]{0,12}初生', before):
        age = 0
    else:
        age_match = re.search(r'(?:生后|出生后)\D{0,8}?(\d+(?:\.\d+)?)\s*(小时|天)', complaint_part)
        if age_match:
            age = float(age_match.group(1)) * (24 if age_match.group(2) == '天' else 1)
    if age is not None and age > 1440: age = None
    if age is None: flags.append('年龄列时点未明，未当作入院日龄')
    weight = None
    weight_match = re.search(r'出生体重\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(kg|公斤|g|克)', before, re.I)
    if weight_match:
        weight = round(float(weight_match.group(1)) * (1000 if weight_match.group(2).lower() in ('kg', '公斤') else 1))
        if not 200 <= weight <= 6500: weight = None
    if weight is None: flags.append('出生体重未可靠提取')
    complaint = '、'.join(symptoms) or ('围产感染风险待评估' if '母体感染风险' in risks else '新生儿入院评估，主诉细节待核实')
    # All free text above remains local. Only controlled clinical phrases leave this boundary.
    intake = Intake(sex=row.get('性别') if row.get('性别') in ('男','女') else '未知', gestational_weeks=weeks,
                    gestational_days=days, postnatal_age_hours=age, birth_weight_g=weight, chief_complaint=complaint,
                    symptoms=symptoms, perinatal_risks=tuple(risks),
                    delivery_mode='cesarean' if '剖宫产' in risks else 'vaginal' if '顺产' in before else 'unknown')
    for name in ('性别', '孕周', '入院时主要症状及体征'):
        col = openpyxl.utils.get_column_letter(FIELDS.index(name)+1)
        evidence.append({'field': name, 'source_cell': f'{sheet}!{col}{row_number}', 'evidence_status': 'extracted'})
    respiratory = any(s in symptoms for s in ('呼吸急促','气促','呻吟','呼吸困难','三凹征','发绀','咳嗽'))
    eligible = bool(respiratory or weeks is not None and weeks < 37 or risks or any(s in symptoms for s in ('黄疸','黄染')))
    if weeks is not None and weeks < 22:
        flags.append('极低孕周需人工复核，未进入默认模拟队列'); eligible = False
    phenotype = '早产风险' if weeks is not None and weeks < 37 else '呼吸症状' if respiratory else '感染风险' if '母体感染风险' in risks or '胎膜早破' in risks else '黄疸症状' if any(s in symptoms for s in ('黄疸','黄染')) else '围产背景待评估'
    return intake.model_dump(mode='json'), flags, evidence, eligible, phenotype


def reference_index(row):
    """Private weak reference index; never a source of online patient truth."""
    course = row.get('诊疗过程', '')
    actions = [target for target, pattern in {'cpap':'CPAP|无创', 'oxygen':'吸氧|氧疗', 'antimicrobial':'抗感染|抗生素',
                                              'surfactant':'肺表面活性|固尔苏', 'phototherapy':'光疗'}.items() if re.search(pattern, course, re.I)]
    return {'diagnosis_labels_raw': row.get('出院诊断', ''), 'outcome_raw': row.get('治疗结果', ''),
            'action_mentions': actions, 'action_mentions_are_not_execution_records': True,
            'date_mentions': re.findall(r'(?:20\d{2}[.年/-])?\d{1,2}[.月/-]\d{1,2}', course),
            'field_roles': FIELD_ROLES}


class SourceCatalog:
    def __init__(self, path=None, archive_path='outputs/dreamer/source.sqlite'):
        self.path = Path(path or ROOT/'2024带孕周的出院小结汇总.xlsx').resolve()
        self.archive_path = output_path(archive_path)
        self.lock = threading.RLock()
        self.stamp = None
        self.rows = []
        self.metadata = {}

    def ensure(self):
        with self.lock:
            if not self.path.is_relative_to(ROOT) or not self.path.is_file():
                raise SourceError('项目内未找到出院小结工作簿。')
            stat = self.path.stat(); stamp = (stat.st_size, stat.st_mtime_ns)
            if stamp == self.stamp: return
            digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
            workbook = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
            cases, seen, counts = [], set(), Counter()
            try:
                sheet = workbook.worksheets[0]; it = sheet.iter_rows(values_only=True)
                headers = list(next(it))
                if any(field not in headers for field in FIELDS): raise SourceError('工作簿字段与出院小结格式不匹配。')
                for number, values in enumerate(it, 2):
                    if not any(value_text(v) for v in values): continue
                    raw = {key: value_text(value) for key, value in zip(headers, values) if key in FIELDS}
                    intake, flags, evidence, eligible, phenotype = extract_intake(raw, sheet.title, number)
                    dates=[]
                    for field in ('入院时间','出院时间'):
                        match=re.match(r'^(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?=$|\D)',raw.get(field,''))
                        try:parsed=date(*map(int,match.groups())) if match else None
                        except ValueError:parsed=None
                        dates.append(parsed)
                    if not all(dates):flags.append('来源住院时间缺失或格式异常，未自动修正')
                    elif dates[1]<dates[0]:flags.append('来源出院日期早于入院日期，已隔离，模拟不沿用该时程')
                    if len(raw.get('实验室检查及主要会诊',''))>=32767:flags.append('检验文本触及Excel字符上限，可能有源内容截断')
                    key = (raw.get('住院号'), raw.get('入院时间'))
                    duplicate = bool(key[0] and key in seen)
                    if duplicate: flags.append('重复就诊键，保留原文但排除默认采样'); eligible = False
                    seen.add(key)
                    group = hashlib.sha256((raw.get('住院号') or f'row:{number}').encode()).hexdigest()
                    case_id = 'DC-' + hashlib.sha256(f'{digest}:{number}'.encode()).hexdigest()[:16]
                    private_reference = reference_index(raw)
                    case = {'id': case_id, 'label': f'来源病例 {number-1:04}', 'row': number, 'sheet': sheet.title,
                            'intake': intake, 'quality_flags': flags, 'intake_evidence': evidence,
                            'eligible': eligible, 'phenotype': phenotype, 'group_id': group,
                            'partition': 'evaluation' if int(group[:8],16)%5 == 0 else 'reference',
                            'raw': raw, 'reference': private_reference}
                    cases.append(case)
                    for field in FIELDS: counts[field] += bool(raw.get(field))
            finally: workbook.close()
            with sqlite3.connect(self.archive_path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS source_rows (source_hash TEXT, case_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
                db.executemany('INSERT OR REPLACE INTO source_rows VALUES (?,?,?)',
                               [(digest,c['id'],json.dumps(c,ensure_ascii=False,allow_nan=False)) for c in cases])
            self.rows = cases
            self.metadata = {'source_file': self.path.name, 'source_sha256': digest, 'records': len(cases),
                             'eligible_cases': sum(c['eligible'] for c in cases), 'fields':len(FIELDS),
                             'field_coverage':[{'name':f,'nonempty':counts[f],'usage':FIELD_ROLES[f]} for f in FIELDS],
                             'partition_counts':dict(Counter(c['partition'] for c in cases)),
                             'notes':['所有18列原值保存在项目内的私有来源库；在线只使用白名单初始信息。',
                                      '病例标签是入院表型，不是提前给定的出院诊断。',
                                      '时间、年龄和缺失值保留待核实状态；不补成已知正常。']}
            self.stamp = stamp

    def summary(self):
        self.ensure(); return self.metadata

    @staticmethod
    def public(case):
        return {k:v for k,v in case.items() if k not in ('raw','reference','group_id')}

    def list(self, offset=0, limit=20, eligible_only=True):
        self.ensure(); rows = [c for c in self.rows if c['eligible'] or not eligible_only]
        return {'items':[self.public(c) for c in rows[offset:offset+limit]], 'total':len(rows)}

    def get(self, identifier):
        self.ensure()
        case = next((c for c in self.rows if c['id']==identifier),None)
        if case is None: raise SourceError('未找到来源病例，文件变化后请重新选择。')
        return case

    def priors(self, exclude_group):
        self.ensure()
        rows = [c for c in self.rows if c['partition']=='reference' and c['group_id']!=exclude_group]
        strata = {}
        for name, lo, hi in [('preterm',20,36),('term',37,44),('unknown',0,0)]:
            group = [c for c in rows if lo <= (c['intake']['gestational_weeks'] or 0) <= hi]
            n = len(group)
            strata[name] = {'records':n,
                            'infection_prevalence':sum('肺炎' in c['raw'].get('出院诊断','') for c in group)/n if n else .3,
                            'rds_prevalence':sum(bool(re.search('呼吸窘迫|NRDS|RDS',c['raw'].get('出院诊断',''),re.I)) for c in group)/n if n else .3}
        return {'reference_records':len(rows),'strata':strata,
                'method':'reference partition, leave target hospital-id group out; descriptive prevalence, not causal treatment effects'}

    def plan(self, body):
        self.ensure()
        if body.source_sha256 and body.source_sha256 != self.metadata['source_sha256']:
            raise SourceError('来源工作簿已变化，请重新预览。')
        unique = {}; rng = random.Random(body.seed)
        for case in self.rows:
            if case['eligible']: unique.setdefault(case['group_id'],case)
        if len(unique)<10: raise SourceError('符合范围且来源组不同的病例不足10名。')
        selected = rng.sample(list(unique.values()),10)
        budgets = [('short',rng.randint(24,32)) for _ in range(3)] + [('medium',rng.randint(48,64)) for _ in range(5)] + [('long',rng.randint(80,96)) for _ in range(2)]
        rng.shuffle(budgets)
        return {'source':self.metadata,'total_patients':10,'distribution':{'short':3,'medium':5,'long':2},
                'patients':[{'index':i,'length_group':group,'max_operations':budget,'case':self.public(case)}
                            for i,(case,(group,budget)) in enumerate(zip(selected,budgets),1)]}
