"""Read-only test.json initial-condition import. Temporal/future labels never become context."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from ehrworld.io import ROOT
from .models import BatchRequest, GenerationRequest, InitialState, PatientInput


class SeedDataError(ValueError):
    pass


def number(value):
    if isinstance(value, dict): value = value.get('value')
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError): return None


def image_time(row):
    matches=re.findall(r'_([12]\d{9,13})(?:\D|$)', str(row.get('image','')))
    return min(matches) if matches else '99999999999999'


def convert_record(row, source_hash):
    label=str(row.get('disease_label',''))
    if any(word in label for word in ('支气管肺发育不良','气胸','胸腔积液')):
        raise SeedDataError('complex_comorbidity')
    structured=row.get('clinical_structured') or {}
    development=structured.get('development_factors') or {}
    physiology=structured.get('physiological_status') or {}
    gestation=number(development.get('gestational_age_weeks'))
    weight=number(development.get('birth_weight_g'))
    if gestation is None or not 22<=gestation<=42 or int(gestation)!=gestation:
        raise SeedDataError('missing_or_invalid_gestation')
    if weight is None or not 200<=weight<=6500 or int(weight)!=weight:
        raise SeedDataError('missing_or_invalid_birth_weight')
    pneumonia='肺炎' in label
    rds='呼吸窘迫' in label
    if rds and gestation<37: scenario='neonatal_rds'
    elif pneumonia: scenario='neonatal_pneumonia'
    else: raise SeedDataError('outside_supported_scenarios')
    p=PatientInput.for_scenario(scenario).model_dump()
    initial=InitialState.for_scenario(scenario).model_dump()
    text=str(row.get('clinical_information') or '')
    english=str(row.get('clinical_information_en') or '')
    sex='女' if re.search(r'性别\s*女',text) or 'gender female' in english.lower() else '男' if re.search(r'性别\s*男',text) or 'gender male' in english.lower() else '未知'
    patient_id=str(row.get('patient_id',''))
    p.update(name=f'来源患者 {patient_id}', age=None, sex=sex, gestational_weeks=int(gestation),gestational_days=0,
             birth_weight_g=int(weight),weight_g=int(weight),primary_diagnosis=str(row.get('disease_diagnosis') or label)[:300],
             chief_complaint='依据来源记录建立初始状态，随后生成合成随访轨迹。',
             history=text[:1600] + ('；初始影像文字：'+str(row.get('imaging_conclusion',''))[:350] if row.get('imaging_conclusion') else ''),
             perinatal_history=json.dumps(structured.get('perinatal_risk') or {},ensure_ascii=False)[:2000],allergies='来源未记录')
    observed={'patient.gestational_weeks':'clinical_structured.development_factors.gestational_age_weeks',
              'patient.birth_weight_g':'clinical_structured.development_factors.birth_weight_g'}
    if sex!='未知': observed['patient.sex']='clinical_information / clinical_information_en'
    for target,key,bounds in [('heart_rate','pulse_bpm',(0,300)),('respiratory_rate','respiration_rate_bpm',(10,140)),
                              ('temperature','body_temperature_c',(30,43)),('glucose','blood_glucose_mmol_l',(0,80))]:
        value=number(physiology.get(key))
        if value is not None and bounds[0]<=value<=bounds[1]:
            initial[target]=int(value) if target in {'heart_rate','respiratory_rate'} else value
            observed['initial_state.'+target]='clinical_structured.physiological_status.'+key
    for target,key,bounds in [('ph','ph',(6,8)),('paco2','pa_co2_mmHg',(5,200))]:
        value=number((physiology.get('blood_gas') or {}).get(key))
        if value is not None and bounds[0]<=value<=bounds[1]:
            initial[target]=value;observed['initial_state.'+target]='clinical_structured.physiological_status.blood_gas.'+key
    assumed={
        'patient.postnatal_age_hours':{'value':p['postnatal_age_hours'],'reason':'来源未提供可靠日龄，使用场景模拟设定'},
        'patient.gestational_days':{'value':0,'reason':'来源只记录整数孕周，余天数未记录'},
        'patient.weight_g':{'value':int(weight),'reason':'当前体重未记录，模拟初值沿用出生体重'},
    }
    for key in ('spo2','fio2','cpap_cm_h2o','respiratory_support','spontaneous_breathing','recurrent_apnea'):
        assumed['initial_state.'+key]={'value':initial[key],'reason':'来源未提供，属于可复现的场景假设'}
    initial['infection_suspected']=pneumonia
    initial['bacterial_pneumonia_supported']=False
    initial['suspected_pathogen']='bacterial' if pneumonia else 'undetermined'
    assumed['initial_state.suspected_pathogen']={'value':initial['suspected_pathogen'],'reason':'场景模拟假设，疾病标签不能确定具体病原'}
    assumed['initial_state.bacterial_pneumonia_supported']={'value':False,'reason':'来源疾病标签不作为细菌感染的确诊证据'}
    if 'initial_state.respiratory_rate' not in observed:
        assumed['initial_state.respiratory_rate']={'value':initial['respiratory_rate'],'reason':'来源未记录有效呼吸频率，使用场景模拟设定'}
    if not pneumonia and scenario=='neonatal_rds': initial['infection_suspected']=False
    provenance={'source_file':'test.json','source_sha256':source_hash,'source_record_id':row.get('id'),
                'source_patient_id':patient_id,'source_image_reference':str(row.get('image','')),
                'source_data_type':str(row.get('data_type','')),'disease_label':label,
                'selection_rule':'每名患者最早的单时点 Single 记录，不使用 Temporal 或未来时间关系',
                'observed_fields':observed,'assumptions':assumed,
                'excluded_fields':['temporal_triplets_en','其他时间点记录','影像像素（未读取）'],
                'raw_structured_initial':structured}
    # Validate before this record can enter the eligible pool.
    GenerationRequest(patient=p,initial_state=initial,scenario_id=scenario,source_seed=provenance)
    return {'source_patient_id':patient_id,'record_id':row.get('id'),'scenario_id':scenario,
            'patient':p,'initial_state':initial,'source_seed':provenance}


@lru_cache(maxsize=4)
def _load(path, size, mtime):
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    try: rows=json.loads(raw.decode('utf-8-sig'))
    except (ValueError,UnicodeDecodeError): raise SeedDataError('test.json 不是可读取的 JSON。') from None
    if not isinstance(rows,list): raise SeedDataError('test.json 根节点必须是记录数组。')
    earliest={};skipped=Counter()
    for ordinal,row in enumerate(rows):
        if not isinstance(row,dict): skipped['invalid_record']+=1;continue
        if row.get('data_type')!='Single' or ',' in str(row.get('image','')):
            skipped['temporal_or_non_single']+=1;continue
        identifier=str(row.get('patient_id','')).strip()
        if not identifier: skipped['missing_patient_id']+=1;continue
        rank=(image_time(row),ordinal)
        if identifier not in earliest or rank<earliest[identifier][0]:earliest[identifier]=(rank,row)
    eligible=[]
    for identifier,(_,row) in sorted(earliest.items()):
        try: eligible.append(convert_record(row,digest))
        except (ValueError,TypeError) as exc:
            skipped[str(exc) if isinstance(exc,SeedDataError) else 'invalid_initial_fields']+=1
    summary={'source_file':'test.json','source_sha256':digest,'record_count':len(rows),
             'unique_patients':len({str(r.get('patient_id')) for r in rows if isinstance(r,dict) and r.get('patient_id')}),
             'single_baseline_patients':len(earliest),'eligible_patients':len(eligible),
             'scenario_counts':dict(Counter(e['scenario_id'] for e in eligible)),'excluded_counts':dict(skipped),
             'import_notes':['优先读取结构化初始字段；不把未来记录/temporal_triplets_en发给模型。',
                             '日龄、当前体重和呼吸支持等缺失项明确标记为模拟假设。',
                             '出生体重/胎龄缺失、复杂合并症或超出两条路径的初始记录不参与本批次。']}
    return summary,eligible


class SeedCatalog:
    def __init__(self,path=None): self.path=Path(path or ROOT/'test.json').resolve()
    def load(self):
        if not self.path.is_relative_to(ROOT): raise SeedDataError('来源文件必须位于项目目录。')
        if not self.path.is_file(): raise SeedDataError('项目根目录未找到 test.json。')
        stat=self.path.stat()
        return _load(self.path,stat.st_size,stat.st_mtime_ns)
    def summary(self): return self.load()[0]
    def plan(self,body:BatchRequest):
        summary,eligible=self.load()
        if body.source_sha256 and body.source_sha256!=summary['source_sha256']:
            raise SeedDataError('test.json 已变化，请重新预览批次。')
        if len(eligible)<10: raise SeedDataError('可用的不同患者不足10名，不能以重复患者补齐。')
        rng=random.Random(body.seed)
        pools={s:[r for r in eligible if r['scenario_id']==s] for s in ('neonatal_pneumonia','neonatal_rds')}
        selected=[]
        for pool in pools.values():selected.extend(rng.sample(pool,min(5,len(pool))))
        used={r['source_patient_id'] for r in selected}
        if len(selected)<10:selected.extend(rng.sample([r for r in eligible if r['source_patient_id'] not in used],10-len(selected)))
        rng.shuffle(selected)
        groups=[('short',rng.randint(1,2)) for _ in range(3)]+[('medium',rng.randint(3,5)) for _ in range(5)]+[('long',rng.randint(6,10)) for _ in range(2)]
        rng.shuffle(groups)
        children=[]
        for index,(record,(group,steps)) in enumerate(zip(selected,groups),1):
            request=GenerationRequest(patient=record['patient'],initial_state=record['initial_state'],scenario_id=record['scenario_id'],
                                      source_seed=record['source_seed'],model=body.model,backend=body.backend,steps=steps,
                                      interval_hours=body.interval_hours,start_time=body.start_time,care_setting=body.care_setting)
            children.append({'index':index,'length_group':group,'target_steps':steps,'request':request})
        return summary,children

    def preview(self,body):
        summary,children=self.plan(body)
        return {'source':summary,'seed':body.seed,'distribution':{'short':3,'medium':5,'long':2},
                'total_patients':10,'total_steps':sum(c['target_steps'] for c in children),
                'patients':[{**{k:v for k,v in c.items() if k!='request'},'request':c['request'].model_dump(mode='json')} for c in children]}
