"""Four observed, calendar-day SFT tasks. No generated values or model calls."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

from ehrworld.io import ROOT
from ehrdreamer.source import FIELDS, extract_intake, value_text
from ehrdreamer.learning.dataset import calendar
from .dataset import dumps, project_path, split_for
from .extract import labs_from_row, treatments_from_row, unique_day_state, date_tokens, THERAPY_PATTERNS, DATE
from .compact import patient_json, report_json

SYSTEM='你是新生儿患者动力学模型。依据既往状态、给定治疗及作用时间预测化验。仅输出JSON：单步为一份完整化验字典，多步按第1天、第2天排列每日化验字典。指标名含单位，保留检出限，不解释、不生成医嘱、不填补缺测值。'
NAMES={'oxygen':'氧疗','cpap':'CPAP','nippv':'NIPPV','noninvasive_support':'无创呼吸支持',
       'respiratory_support_unspecified':'呼吸支持(方式未注明)','invasive_ventilation':'气管插管/有创通气',
       'surfactant':'肺表面活性物质','antimicrobial':'抗感染治疗','phototherapy':'光疗',
       'feeding':'喂养调整','caffeine':'咖啡因','vasopressor':'循环支持','transfusion':'输血',
       'immunoglobulin':'免疫球蛋白','thyroid_replacement':'甲状腺激素治疗','gi_decompression':'胃肠减压',
       'iron_vitamins':'铁剂/维生素','thermal_nutrition':'保温'}
DRUGS=re.compile(r'氨苄西林(?:钠)?(?:[/、\s]*舒巴坦(?:钠)?)?|阿莫西林(?:克拉维酸钾)?|哌拉西林(?:他唑巴坦)?|'
                 r'头孢噻肟(?:钠)?|头孢呋辛(?:钠)?|头孢他啶|头孢曲松|头孢哌酮(?:舒巴坦)?|头孢吡肟|'
                 r'头孢唑肟|头孢西丁|美罗培南|亚胺培南(?:西司他丁)?|万古霉素|替考拉宁|利奈唑胺|'
                 r'阿奇霉素|红霉素|克拉霉素|青霉素|苯唑西林|优立新|邦达|美平|氟康唑|伏立康唑|阿昔洛韦')
FILES={'single_step':'single_step.csv','multi_step':'multi_step.csv','combination':'combination.csv','mixed':'mixed.csv'}
FIELDS_OUT=['sample_id','patient_id','episode_id','split','task_type','n_steps','origin','parent_ids','input','output','messages','provenance']


def _parts(course):
    """A comma inside a treatment's date parentheses is not another regimen."""
    left=depth=0;result=[]
    for i,char in enumerate(course):
        if char in '(（':depth+=1
        elif char in ')）':depth=max(0,depth-1)
        if char in '。；;\n' or char in '，,' and depth==0:
            result.append((left,course[left:i]));left=i+1
            if char in '。；;\n':depth=0
    result.append((left,course[left:]));return result


def _relative_day(text):
    match=re.search(r'(?:入院|入科)(?:后)?第?([0-9一二三四五六七八九十两]+)天',text)
    if not match:return None
    token=match[1];digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if token.isdigit():n=int(token)
    elif token=='十':n=10
    elif '十' in token:
        a,b=token.split('十',1);n=digits.get(a,1)*10+digits.get(b,0)
    else:n=digits.get(token,0)
    return max(0,n-1) if 1<=n<=100 and '第' in match[0] else n if 1<=n<=100 else None


def _owned_periods(text,start,end,others,admission,discharge,fallback):
    """Bind dates to this treatment, not to all regimens in the paragraph."""
    tokens=date_tokens(text,admission,discharge)
    def can_cross(limit):
        previous=end
        for other in sorted(others,key=lambda x:x['a']):
            if not end<=other['a']<limit:continue
            if other['descriptor']:continue
            gap=text[previous:other['a']]
            if other['target']!=fallback['target'] or not re.search(r'联合|联用|合用|及|和|、|\+',gap) or re.search(r'改|换|停|升级',gap):return False
            previous=other['b']
        return True
    # Expand multiple courses ONLY inside an owned, date-only parenthesis.
    for group in re.finditer(r'[（(]([^（）()]*)[）)]',text):
        if group.start()<end or not can_cross(group.start()):continue
        inner=group[1];its=date_tokens(inner,admission,discharge)
        if not its:continue
        residue=inner
        for a,b,_ in reversed(its):residue=residue[:a]+residue[b:]
        if re.sub(r'[\s,，、及和至日号\-—~～]','',residue):continue
        periods=[];i=0
        while i<len(its):
            token=its[i];finish=None;j=i+1
            if j<len(its) and re.fullmatch(r'\s*(?:日)?\s*[-—~～至]\s*',inner[token[1]:its[j][0]]):
                finish=its[j][2];j+=1
            periods.append(((token[2]-admission).days,(finish-admission).days if finish else None,group[0],'own_date_parenthesis'))
            i=j
        return periods
    after=[t for t in tokens if t[0]>=end and can_cross(t[0])]
    if after:
        first=after[0];finish=None;evidence=first[1]
        later=[t for t in tokens if t[0]>=first[1]]
        if later and re.fullmatch(r'\s*(?:日)?\s*[-—~～至]\s*',text[first[1]:later[0][0]]):
            finish=later[0][2];evidence=later[0][1]
        return [((first[2]-admission).days,(finish-admission).days if finish else None,text[first[0]:evidence],'own_suffix_date')]
    before=[t for t in tokens if t[1]<=start and not any(t[1]<=o['a']<start and not o['descriptor'] for o in others)]
    if before:
        token=before[-1]
        index=tokens.index(token)
        if index and re.fullmatch(r'\s*(?:日)?\s*[-—~～至]\s*',text[tokens[index-1][1]:token[0]]):
            first=tokens[index-1]
            if not any(o['a']<first[0] and not o['descriptor'] for o in others):
                return [((first[2]-admission).days,(token[2]-admission).days,text[first[0]:token[1]],'own_prefix_range')]
            return []
        # A date closing another regimen's parentheses is not this drug's start.
        if not re.search(r'[)）]',text[token[1]:start]) or not re.search(r'[A-Za-z\u4e00-\u9fff]',text[:token[0]].strip(' （(')):
            return [((token[2]-admission).days,None,text[token[0]:token[1]],'own_prefix_date')]
    relative=_relative_day(text)
    if relative is not None:return [(relative,None,text,'relative_admission_day')]
    if fallback['time_precision']=='admission_day_clause':return [(0,None,text,'admission_day_clause')]
    return []


def detailed_treatments(raw,row):
    """Retain named joint antibiotics; do not split their common outcome labels."""
    patterns={**THERAPY_PATTERNS,'antimicrobial':DRUGS.pattern+'|'+THERAPY_PATTERNS['antimicrobial'],
              'noninvasive_support':THERAPY_PATTERNS['noninvasive_support']+'|Biph|BiPAP'}
    coarse=treatments_from_row(raw,row,deduplicate=False,treatment_patterns=patterns);result=[];seen=set();audit=Counter()
    course=raw.get('诊疗过程','');parts=_parts(course)
    admission,discharge=calendar(raw.get('入院时间')),calendar(raw.get('出院时间'))
    end_day=(discharge-admission).days if admission and discharge and discharge>=admission else None
    for event in coarse:
        left,text=next((a,t) for a,t in parts if a<=event['source_span'][0]<=a+len(t));target=event['target']
        local_start,local_end=event['source_span'][0]-left,event['source_span'][1]-left
        names=[NAMES[target]];routine=target=='iron_vitamins'
        if target=='antimicrobial':
            named=list(DRUGS.finditer(text));owned=[m for m in named if m.start()<local_end and m.end()>local_start]
            if named and not owned:continue  # "抗感染" is a descriptor of the already named agents.
            if owned:names=[owned[0][0]];local_start,local_end=owned[0].span()
        elif re.fullmatch(r'Biph|BiPAP',course[event['source_span'][0]:event['source_span'][1]],re.I):
            names=[course[event['source_span'][0]:event['source_span'][1]]]
        elif target=='thermal_nutrition':
            if re.search(r'静脉营养',text):names=['静脉营养/补液']
            elif re.search(r'补液',text):names=['补液']
            else:routine=True
        elif target=='feeding':
            names=['禁食' if '禁食' in text else '开奶/喂养调整']
        others=[]
        for e in coarse:
            a,b=e['source_span'][0]-left,e['source_span'][1]-left
            if not 0<=a<len(text) or a==event['source_span'][0]-left:continue
            matched=course[e['source_span'][0]:e['source_span'][1]]
            others.append({'a':a,'b':b,'target':e['target'],'descriptor':e['target']=='antimicrobial' and matched in ('抗感染','抗生素','抗炎治疗')})
        periods=_owned_periods(text,local_start,local_end,others,admission,discharge,event) if end_day is not None else []
        if not periods:audit['unresolved_treatment_dates_not_assigned']+=1
        for start,finish,time_text,precision in periods:
            if finish is None:
                barrier=min([o['a'] for o in others if o['a']>=local_end and not o['descriptor']]+[len(text)])
                duration=re.search(r'(?<![\d.])([1-9]\d*)天(?!余|多|左右)',text[local_end:barrier])
                if duration and not re.search(r'[A-Za-z\u4e00-\u9fff]',time_text.replace('日','').replace('号','')):
                    finish=start+int(duration[1])-1
                elif duration and precision=='relative_admission_day':finish=start+int(duration[1])-1
            right_censored=False
            if finish is None and re.search(r'至今|迄今|至出院|至转院',text) and end_day is not None:
                finish=end_day;right_censored=True
            if finish is not None and finish<start:
                audit['invalid_treatment_ranges_excluded']+=1;continue
            for name in names:
                identity=f'{target}:{name}'
                fingerprint=(identity,start,finish)
                if fingerprint in seen:continue
                seen.add(fingerprint)
                result.append({'key':identity,'name':name,'target':target,'start':start,'end':finish,
                               'operation':event['operation'],'routine':routine,'right_censored':right_censored,
                               'source_span':event['source_span'],'time_precision':precision,
                               'source_time_text':time_text,'source_segment':text})
    # Keep a real source audit outside model messages; it includes dates and the
    # owning phrase so erroneous cross-regimen assignments can be reviewed.
    return sorted(result,key=lambda x:(x['start'],x['key'])),audit


def _state(items):
    numeric=[]
    for item in items:
        unit=item.get('unit','')
        if item['kind']!='numeric' or unit.endswith('/') or '→' in unit or '←' in unit:continue
        if unit.startswith('^') or len(unit)>24:continue
        numeric.append(item)
    # An ambiguous within-day result is not a numerical SFT target.
    return {k:v for k,v in unique_day_state(numeric).items() if not v.get('ambiguous_same_day')}


def _actions(treatments,day):
    active={};support={}
    for t in treatments:
        # Unknown end -> a documented event on its own day, not an invented course.
        covered=t['start']==day if t['end'] is None else t['start']<=day<=t['end']
        if not covered:continue
        item={'name':t['name'],'since_day':t['start']}
        if t['operation'] in ('stop','adjust'):item['op']=t['operation']
        destination=support if t['routine'] else active
        destination[t['key']]=item
    return list(active.values()),list(support.values())


def _sample(patient_id,episode_id,split,intake,states,treatments,baseline,steps,kind,row,origin):
    current=report_json(states[baseline]);history=[]
    for day in [d for d in sorted(states) if d<baseline][-2:]:
        history.append({'day':day-baseline,'labs':report_json(states[day])})
    schedule={};support={}
    for index in range(steps):
        actions,nursing=_actions(treatments,baseline+index)
        projected=[{**a,'since_day':a['since_day']-baseline} for a in actions]
        schedule[str(index+1)]=projected
        if nursing:support[str(index+1)]=list(dict.fromkeys(a['name'] for a in nursing))
    past=[]
    for t in treatments:
        if t['start']<baseline and (t['end'] is None or t['end']<baseline) and not t['routine']:
            item={'name':t['name'],'start_day':t['start']-baseline}
            if t['end'] is not None:item['end_day']=t['end']-baseline
            past.append(item)
    patient=dict(intake)
    if patient.get('postnatal_age_hours') is not None:patient['postnatal_age_hours']+=baseline*24
    payload={'patient':patient_json(patient),'labs':current,'actions':schedule}
    if history:payload['history']=history
    if past:payload['past_treatments']=past
    if support:payload['support']=support
    # No target values (including intermediate future days) enter this payload.
    reports={f'第{i}天':report_json(states[baseline+i]) for i in range(1,steps+1)}
    output=reports['第1天'] if steps==1 else reports
    input_text=dumps(payload);output_text=dumps(output)
    identifier=hashlib.sha256(f'{patient_id}:{episode_id}:{kind}:{baseline}:{steps}'.encode()).hexdigest()[:24]
    counts=[len(_actions(treatments,baseline+i)[0]) for i in range(steps)]
    return {'sample_id':'SFT2-'+identifier,'patient_id':patient_id,'episode_id':episode_id,'split':split,
            'task_type':kind,'n_steps':steps,'origin':origin,'parent_ids':[],
            'input':input_text,'output':output_text,
            'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':input_text},{'role':'assistant','content':output_text}],
            'provenance':{'source_row':row,'baseline_day':baseline,'target_days':list(range(baseline+1,baseline+steps+1)),
                'documented_primary_treatment_counts':counts,'daily_steps':True,
                'mix_pattern':'single_and_combined' if min(counts)==1 and max(counts)>1 else 'combined_each_step' if min(counts)>1 else 'single_each_step',
                'alignment':'calendar_day_bins_not_verified_sampling_or_execution_hours',
                'single_means':'one_documented_primary_treatment_not_proven_causal_monotherapy',
                'routine_warmth_vitamins_are_support_context':True,
                'future_labs_in_input':False,'labels':'source_observed_not_generated'}}


def episode_samples(patient_id,episode_id,split,intake,labs,treatments,row):
    daily=defaultdict(list)
    for item in labs:daily[item['day']].append(item)
    states={day:_state(items) for day,items in daily.items()}
    valid=[]
    for day in sorted(states):
        if not states[day] or not states.get(day+1):continue
        if not set(states[day])&set(states[day+1]):continue
        actions,_=_actions(treatments,day)
        if actions:valid.append(day)
    runs=[]
    for day in valid:
        if not runs or runs[-1][-1]+1!=day:runs.append([day])
        else:runs[-1].append(day)
    parents=[]
    for run in runs:
        if len(run)<2:continue
        counts=[len(_actions(treatments,d)[0]) for d in run]
        if max(counts)==1:kind='multi_step'
        elif min(counts)==1:kind='mixed'
        else:continue  # All-combination trajectories are not a single/combined mixture.
        parents.append(_sample(patient_id,episode_id,split,intake,states,treatments,run[0],len(run),kind,row,'complete_daily_trajectory'))
        if kind=='mixed':
            single_runs=[]
            for d,n in zip(run,counts):
                if n!=1:continue
                if not single_runs or single_runs[-1][-1]+1!=d:single_runs.append([d])
                else:single_runs[-1].append(d)
            for subrun in single_runs:
                if len(subrun)>=2:
                    parents.append(_sample(patient_id,episode_id,split,intake,states,treatments,subrun[0],len(subrun),'multi_step',row,'single_treatment_subtrajectory'))
    first=defaultdict(list)
    for parent in parents:first[parent['provenance']['baseline_day']].append(parent)
    singles=[]
    for day in valid:
        n=len(_actions(treatments,day)[0]);kind='single_step' if n==1 else 'combination'
        sample=_sample(patient_id,episode_id,split,intake,states,treatments,day,1,kind,row,'observed_daily_transition')
        if day in first:
            sample['parent_ids']=[p['sample_id'] for p in first[day]]
            sample['origin']='observed_transition_and_trajectory_first_step'
            sample['provenance']['first_step_of']=[p['task_type'] for p in first[day]]
        singles.append(sample)
    return singles+parents


def build_sft(workbook='2024带孕周的出院小结汇总.xlsx',output_dir='data/dynamics_sft_2024_v2',*,secret=None):
    source=project_path(workbook);outdir=project_path(output_dir);outdir.mkdir(parents=True,exist_ok=True)
    auditdir=project_path('outputs/dynamics_sft_v2') if outdir==ROOT/'data/dynamics_sft_2024_v2' else outdir.with_name(outdir.name+'-audit')
    auditdir.mkdir(parents=True,exist_ok=True)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if secret is None:
        keypath=ROOT/'data/gspo_2024/private/patient_hmac.key'
        if not keypath.exists():
            keypath=auditdir/'patient_hmac.key'
            if not keypath.exists():keypath.write_bytes(os.urandom(32))
        secret=keypath.read_bytes()
    candidates={};audit=Counter();wb=openpyxl.load_workbook(source,read_only=True,data_only=True)
    try:
        sheet=wb.worksheets[0];it=sheet.iter_rows(values_only=True);headers=list(next(it))
        if not set(FIELDS)<=set(headers):raise ValueError('Unexpected source columns')
        for row,values in enumerate(it,2):
            if not any(v is not None for v in values):continue
            raw={str(k):value_text(v) for k,v in zip(headers,values) if k in FIELDS};audit['source_rows']+=1
            pid='P-'+hmac.new(secret,(raw.get('住院号') or f'missing:{sha}:{row}').encode(),hashlib.sha256).hexdigest()[:24]
            eid='E-'+hmac.new(secret,f'{pid}:{raw.get("入院时间") or row}'.encode(),hashlib.sha256).hexdigest()[:24]
            key=(pid,eid);richness=sum(len(raw.get(f,'')) for f in ('实验室检查及主要会诊','特殊检验及重要会诊'))
            if key in candidates:
                audit['duplicate_encounters_excluded']+=1
                if candidates[key][0]>=richness:continue
            candidates[key]=(richness,row,raw)
    finally:wb.close()
    streams={k:(outdir/name).open('w',encoding='utf-8-sig',newline='') for k,name in FILES.items()}
    counts=Counter();splits=defaultdict(Counter);augmented=Counter();ids=set();maxsteps=Counter();patient_sets=defaultdict(set)
    proof_stream=(auditdir/'treatment_audit.jsonl').open('w',encoding='utf-8')
    try:
        writers={k:csv.DictWriter(stream,fieldnames=FIELDS_OUT) for k,stream in streams.items()}
        for writer in writers.values():writer.writeheader()
        for (pid,eid),(_,row,raw) in candidates.items():
            labs,counters=labs_from_row(raw,row);audit.update(counters)
            therapies,counters=detailed_treatments(raw,row);audit.update(counters)
            for therapy in therapies:proof_stream.write(dumps({'source_row':row,**therapy})+'\n')
            intake,*_=extract_intake(raw,'source',row);split=split_for(pid)
            for sample in episode_samples(pid,eid,split,intake,labs,therapies,row):
                assert sample['sample_id'] not in ids;ids.add(sample['sample_id'])
                kind=sample['task_type'];counts[kind]+=1;splits[kind][split]+=1
                maxsteps[kind]=max(maxsteps[kind],sample['n_steps']);patient_sets[kind].add(pid)
                if sample['parent_ids']:augmented[kind]+=1
                encoded={k:dumps(v) if isinstance(v,(dict,list)) else v for k,v in sample.items()}
                writers[kind].writerow(encoded)
    finally:
        proof_stream.close()
        for stream in streams.values():stream.close()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:raise RuntimeError('Source changed during extraction')
    report={'data_version':'dynamics-sft-2.0-owned-treatment-dates','source_sha256':sha,'source_unchanged':True,'counts':dict(counts),'patient_counts':{k:len(v) for k,v in patient_sets.items()},
            'first_step_derived_samples':dict(augmented),'split_counts':{k:dict(v) for k,v in splits.items()},
            'max_steps':dict(maxsteps),'audit':dict(audit),'output_dir':outdir.relative_to(ROOT).as_posix(),
            'model_api_calls':0,'training_executed':False,'daily_labels_interpolated':False,
            'task_definition':'mixed requires both single-treatment and combined-treatment days in the same daily trajectory',
            'limitations':['Date-level alignment is not proof of treatment-before-sampling order or causal effects.',
                           'Undated treatments are not assigned invented days; routine warmth/vitamins remain support context.']}
    (auditdir/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
