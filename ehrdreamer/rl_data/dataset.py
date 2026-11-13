"""Build one-patient-per-row GSPO data and separate observational dynamics data."""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import openpyxl

from ehrworld.io import ROOT
from ehrworld.web.guidelines import manifest
from ehrdreamer.source import FIELDS, extract_intake, value_text
from ehrdreamer.learning.dataset import calendar
from ehrdreamer.dual.references import classification, rules_for
from . import DATA_VERSION
from .contracts import PolicyResponse, SYSTEM_PROMPT, TREATMENTS
from .extract import labs_from_row, treatments_from_row, unique_day_state
from .reward import RewardConfig
from .compact import compact_policy_prompt, POLICY_SYSTEM


def dumps(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def project_path(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('All inputs and outputs must remain inside project')
    return path


def _write_csv(path,fields,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='raise');writer.writeheader()
        for row in rows:
            writer.writerow({k:dumps(v) if isinstance(v,(dict,list,tuple,bool)) else '' if v is None else v for k,v in row.items()})


def _patient_id(secret,value):
    return 'P-'+hmac.new(secret,value.encode(),hashlib.sha256).hexdigest()[:24]


def _reference_course(raw):
    """Local chart reference only, never a policy prompt or optimal answer."""
    text=raw.get('诊疗过程','')
    if raw.get('住院号'):text=text.replace(raw['住院号'],'[患者编号]')
    text=re.sub(r'(?<![A-Za-z0-9])[A-Fa-f0-9]{16,}(?![A-Za-z0-9])','[报告编号]',text)
    text=re.sub(r'(?<!\d)\d{7,}(?!\d)','[编号]',text)
    text=re.sub(r'(?:姓名|身份证|联系电话|住院号)\s*[:：]\s*[^，,。；;\s]+','[已隐去身份字段]',text)
    return text


def split_for(patient_id):
    bucket=int(hashlib.sha256(patient_id.encode()).hexdigest()[:8],16)%100
    return 'train' if bucket<80 else 'validation' if bucket<90 else 'test'


def _lab_view(item):
    fields=('event_id','key','name','unit','value','comparator','bound','kind','text','source_flag',
            'ambiguous_same_day','same_day_results','evidence_ids')
    result={k:item[k] for k in fields if k in item and item[k] is not None}
    if result.get('kind')=='numeric':result.pop('kind')
    if result.get('ambiguous_same_day') is False:result.pop('ambiguous_same_day')
    if result.get('evidence_ids')==[item['event_id']]:result.pop('evidence_ids')
    # Explicit null is necessary for censored/ambiguous/non-numeric results.
    if 'value' not in result:result['value']=None
    return result


def _past_action(event,anchor):
    # Future stops/duration and free-text outcome clauses never enter the prompt.
    result={k:event[k] for k in ('event_id','target','operation','day','time_precision','same_day_order')}
    result['days_before_decision']=anchor+1-event['day']
    return result


def _policy_visible_lab(item):
    # Culture/pathogen/qualitative reports may take days. Their collection date
    # does not establish availability; retain them in the observed archive only.
    return item['kind']=='numeric'


def _action_history(episode,anchor):
    history=[];active={};activity=defaultdict(list)
    for event in episode['treatments']:
        day=event['day']
        if day is None or day>anchor:continue
        history.append(_past_action(event,anchor))
        activity[event['target']].append(event)
        # A recorded end day is inclusive at day precision. Do not leak that end
        # until the decision is after it; no exact administration hour is implied.
        if event['end_day'] is not None and event['end_day']<=anchor:
            history.append({'event_id':event['event_id']+':end','target':event['target'],
                            'operation':'recorded_interval_ended','day':event['end_day'],
                            'days_before_decision':anchor+1-event['end_day'],'time_precision':'calendar_day',
                            'same_day_order':'unknown'})
    for target,events in activity.items():
        latest=max(e['day'] for e in events);current=[e for e in events if e['day']==latest]
        # The source's section order is not an execution order. A same-day stop
        # and start is ambiguous; an undated end is not evidence of ongoing use.
        if any(e['operation']=='stop' for e in current):continue
        supported=[e for e in current if e['end_day'] is not None and e['end_day']>anchor]
        if supported:active[target]=supported[0]['event_id']
    return sorted(history,key=lambda x:x['day']),active


def _anchor(episode,guidance,horizon):
    admission=calendar(episode['raw'].get('入院时间'));discharge=calendar(episode['raw'].get('出院时间'))
    choices=[]
    if not admission or not discharge or discharge<admission:return None
    last_day=(discharge-admission).days
    for day,items in sorted(episode['daily'].items()):
        if day>=last_day:continue
        state=unique_day_state([i for i in items if _policy_visible_lab(i)])
        intake=dict(episode['intake']);age=intake.get('postnatal_age_hours')
        if age is not None:intake['postnatal_age_hours']=age+(day+1)*24
        candidate_rules=rules_for(intake,state,max_hours=horizon)
        usable={k:v for k,v in candidate_rules.items() if not state[k].get('ambiguous_same_day') and classification(state[k],v) in ('normal','low','high')}
        if usable:choices.append((day,state,usable,intake))
    # One deterministic decision cut per patient. Prefer a cut with at least one
    # earlier lab day; never select by a favorable later outcome.
    if not choices:return None
    return next((c for c in choices if any(d<c[0] for d in episode['daily'])),choices[0])


def _policy_row(patient_id,split,episode,guidance,outdir,max_prompt_chars):
    reward_config=asdict(RewardConfig());candidate=_anchor(episode,guidance,reward_config['horizon_hours'])
    reasons=[]
    if not episode['hospital_id_present']:reasons.append('missing_patient_linkage_id')
    if not episode['scope_eligible']:reasons.append('outside_visible_neonatal_guideline_scope')
    if not all(s['reviewed'] and s['available'] for s in guidance['sources']):reasons.append('guideline_source_not_verified')
    if not candidate:reasons.append('no_pre_discharge_unambiguous_labs_with_applicable_reference')
    day,current,rules,intake=candidate or (0,{}, {},episode['intake'])
    history=[];latest={}
    for observed_day,items in sorted(episode['daily'].items()):
        if observed_day>day:break
        state=unique_day_state([i for i in items if _policy_visible_lab(i)])
        for key,item in state.items():latest[key]={**_lab_view(item),'as_of_hours':(observed_day-day)*24,
                                                  'observation_age_days':day-observed_day}
        if observed_day<day:
            history.append({'day':observed_day,'days_before_decision':day+1-observed_day,
                            'labs':{k:_lab_view(v) for k,v in state.items()}})
    action_history,active=_action_history(episode,day)
    evidence=sorted({e for item in latest.values() for e in item.get('evidence_ids',[item['event_id']])}
                    | {event['event_id'] for event in action_history})
    # Include evidence from all visible earlier observations, not only last value.
    evidence=sorted(set(evidence)|{i['event_id'] for d,items in episode['daily'].items() if d<=day for i in items if _policy_visible_lab(i)})
    context={'patient_id':patient_id,'episode_id':episode['episode_id'],'intake':intake,'anchor_day':day,
             'time_convention':'decision_after_calendar_day; within_day_order_unknown; source_reports_not_guaranteed_available_same_day',
             'initial_labs':{k:{**_lab_view(current[k]),'as_of_hours':0} for k in rules},
             'baseline_state':latest,'reference_rules':rules,'known_metric_keys':list(latest),
             'known_active_treatments':list(active),'visible_evidence_ids':evidence,
             'guideline_rule_ids':[r['rule_id'] for r in guidance['rules']], 'reward_config':reward_config,
             'clinical_safety_gate':'independent_review_required','dynamics_status':'must_be_frozen_and_validated_before_rl',
             'future_real_records_in_context':False,'data_version':DATA_VERSION}
    user={'patient':intake,'decision_day':day+1,'time_precision':'calendar_day; no invented administration/availability hours',
          'clinical_history':{'lab_history':history,'treatment_history':action_history,
                              'undated_treatment_mentions_exist':any(t['day'] is None for t in episode['treatments'])},
            'current_labs':{k:_lab_view(v) for k,v in current.items()},
            'last_known_results_with_age':{k:v for k,v in latest.items() if k not in current},
          'known_active_treatments':list(active),'fixed_evaluation_rules':rules,'horizon_hours':72,
          'allowed_treatments':TREATMENTS,'guideline_summaries':guidance['rules'],
          'response_schema':PolicyResponse.model_json_schema()}
    prompt,evidence_map=compact_policy_prompt(user)
    compact_user=json.loads(prompt[1]['content'])
    context['visible_evidence_ids']=sorted(set(evidence_map.values()))
    context['compact_evidence_map']=evidence_map
    context['guideline_rule_ids']=list(compact_user['guidelines'])
    context['prompt_version']='compact-reports-2.0'
    if len(dumps(prompt))>max_prompt_chars:reasons.append('prompt_exceeds_configured_character_budget_no_truncation')
    context_bytes=dumps(context).encode();context_path=outdir/'environments'/f'{patient_id}.json'
    context_path.write_bytes(context_bytes)
    ground_truth={'patient_id':patient_id,'context_path':context_path.relative_to(ROOT).as_posix(),
                  'context_sha256':hashlib.sha256(context_bytes).hexdigest(),'optimal_answer_available':False}
    # Only explicitly aligned next-day or ongoing chart treatments are reference
    # actions. They are kept outside prompt and excluded from the verl export.
    reference=[]
    for t in episode['treatments']:
        if t['day']==day+1 or t['day'] is not None and t['day']<=day and t['end_day'] is not None and t['end_day']>=day+1:
            reference.append({k:t[k] for k in ('target','operation','day','end_day','event_id','time_precision')})
    extra={'patient_id':patient_id,'episode_id':episode['episode_id'],'split':split,'anchor_day':day,'data_version':DATA_VERSION}
    return {'patient_id':patient_id,'episode_id':episode['episode_id'],'split':split,'rl_eligible':not reasons,
            'exclusion_reasons':reasons,'anchor_day':day,'data_source':'ehrdreamer/neonatal_gspo_v1',
            'system_prompt':POLICY_SYSTEM,'clinical_history':{'history':compact_user['history'],'treatments':compact_user['treatments']},'current_labs':compact_user['current']['labs'],
            'prompt':prompt,'ability':'neonatal_long_horizon_treatment_planning',
            'reward_model':{'style':'rule','ground_truth':dumps(ground_truth)},'extra_info':extra,
            'output':None,'reference_treatment':reference or None,
            'reference_treatment_text':_reference_course(episode['raw']),
            'reference_semantics':'observed_doctor_plan_not_proven_optimal; not_a_gspo_target',
            'fixed_evaluation_metric_keys':list(rules),'unassessed_metric_count':len(current)-len(rules)}


def _transitions(patient_id,split,episode):
    days=sorted(episode['daily']);previous={}
    for index,day in enumerate(days):
        current=unique_day_state(episode['daily'][day]);previous.update(current)
        if index==len(days)-1:break
        next_day=days[index+1];targets=unique_day_state(episode['daily'][next_day])
        actions=[]
        for t in episode['treatments']:
            if t['day'] is not None and (day<t['day']<=next_day or t['day']<=day and t['end_day'] is not None and t['end_day']>=day):
                clipped={k:t[k] for k in ('event_id','target','operation','day','time_precision','same_day_order')}
                if t['end_day'] is not None and t['end_day']<=next_day:clipped['end_day']=t['end_day']
                else:clipped['end_day']=None
                actions.append(clipped)
        has_unanchored=any(t['day'] is None for t in episode['treatments'])
        observed={k:_lab_view(v) for k,v in targets.items() if not v.get('ambiguous_same_day')}
        condition={'patient':episode['intake'],'from_day':day,'to_day':next_day,'elapsed_calendar_days':next_day-day,
                   'last_known_labs':{k:{**_lab_view(v),'observed_day':v['day'],'age_days_at_start':day-v['day']} for k,v in previous.items()},
                   'documented_treatment_events':actions,'undated_treatments_may_exist':has_unanchored,
                   'temporal_limit':'same-day ordering/execution hours unavailable; intervals are retrospective documentation',
                   'observation_boundary':'end_of_calendar_day_convention_not_actual_sampling_time'}
        yield {'patient_id':patient_id,'episode_id':episode['episode_id'],'split':split,'from_day':day,'to_day':next_day,
               'delta_days':next_day-day,'documented_aligned_action_count':len(actions),
               'contains_undated_treatments':has_unanchored,'temporal_alignment_supported':bool(actions),
               'causal_effect_identified':False,'input':condition,'output_observed_labs':observed,
               'target_missing_or_ambiguous_keys':[k for k in targets if k not in observed],
               'use':'observational_dynamics_data; not_counterfactual_reward_labels'}


def build_dataset(workbook=None,output_dir='data/gspo_2024',*,max_prompt_chars=60000,parquet=True):
    source=project_path(workbook or ROOT/'2024带孕周的出院小结汇总.xlsx');outdir=project_path(output_dir)
    if not source.is_file():raise FileNotFoundError(source)
    before_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    outdir.mkdir(parents=True,exist_ok=True)
    for name in ('private','environments','verl'): (outdir/name).mkdir(exist_ok=True)
    secret_path=outdir/'private/patient_hmac.key'
    if not secret_path.exists():secret_path.write_bytes(os.urandom(32))
    secret=secret_path.read_bytes()
    if len(secret)!=32:raise ValueError('Invalid local pseudonymization key')
    guidance=manifest();audit=Counter();groups=defaultdict(list);index_rows=[]
    wb=openpyxl.load_workbook(source,read_only=True,data_only=True)
    try:
        sheet=wb.worksheets[0];it=sheet.iter_rows(values_only=True);headers=list(next(it))
        if not set(FIELDS)<=set(headers):raise ValueError('Unexpected workbook columns')
        with (outdir/'private/source_records.jsonl').open('w',encoding='utf-8') as archive:
            for row_number,values in enumerate(it,2):
                if not any(v is not None for v in values):continue
                raw={str(k):value_text(v) for k,v in zip(headers,values) if k in FIELDS};audit['source_rows']+=1
                hospital=raw.get('住院号','');patient_id=_patient_id(secret,hospital or f'missing:{before_hash}:{row_number}')
                episode_id='E-'+hmac.new(secret,f'{patient_id}:{raw.get("入院时间") or row_number}'.encode(),hashlib.sha256).hexdigest()[:24]
                intake,flags,evidence,scope,phenotype=extract_intake(raw,sheet.title,row_number)
                labs,counters=labs_from_row(raw,row_number);audit.update(counters)
                treatments=treatments_from_row(raw,row_number);daily=defaultdict(list)
                for item in labs:daily[item['day']].append(item)
                episode={'episode_id':episode_id,'row':row_number,'raw':raw,'intake':intake,'quality_flags':flags,
                         'hospital_id_present':bool(hospital),'scope_eligible':scope,'phenotype':phenotype,
                         'daily':dict(daily),'treatments':treatments}
                groups[patient_id].append(episode)
                index_rows.append({'patient_id':patient_id,'episode_id':episode_id,'source_row':row_number,'hospital_id':hospital})
                archive.write(dumps({'patient_id':patient_id,'episode_id':episode_id,'row':row_number,'source':raw})+'\n')
    finally:wb.close()
    policy=[];patients=[];all_labs=[];all_actions=[];transitions=[];exclusions=Counter();coverage=Counter()
    for patient_id,rows in groups.items():
        split=split_for(patient_id);episodes={}
        for episode in rows:
            key=episode['episode_id'];old=episodes.get(key)
            if old is None or sum(len(v) for v in episode['daily'].values())>sum(len(v) for v in old['daily'].values()):episodes[key]=episode
        audit['duplicate_encounters_excluded']+=len(rows)-len(episodes)
        unique=sorted(episodes.values(),key=lambda e:(raw_date_sort(e['raw'].get('入院时间')),e['row']))
        # The first eligible encounter, then a deterministic history-bearing cut.
        selected=next((e for e in unique if e['hospital_id_present'] and e['scope_eligible'] and _anchor(e,guidance,72)),unique[0])
        policy_row=_policy_row(patient_id,split,selected,guidance,outdir,max_prompt_chars)
        policy.append(policy_row);exclusions.update(policy_row['exclusion_reasons'])
        archive_episodes=[]
        for episode in unique:
            days=sorted(episode['daily'])
            archive_episodes.append({'episode_id':episode['episode_id'],'intake':episode['intake'],'phenotype':episode['phenotype'],
                'quality_flags':episode['quality_flags'],'observed_days':days,
                'daily_records':[{'day':d,'labs':[_lab_view(i) for i in episode['daily'][d]]} for d in days],
                'treatment_events':episode['treatments'],'reference_course_text':_reference_course(episode['raw']),
                'all_calendar_days_observed':bool(days and len(days)==max(days)-min(days)+1)})
            for day,items in episode['daily'].items():
                for item in items:
                    all_labs.append({'patient_id':patient_id,'episode_id':episode['episode_id'],'split':split,**item});coverage[item['key']]+=1
            for action in episode['treatments']:all_actions.append({'patient_id':patient_id,'episode_id':episode['episode_id'],'split':split,**action})
            transitions.extend(_transitions(patient_id,split,episode))
        patients.append({'patient_id':patient_id,'split':split,'encounter_count':len(unique),'source_row_count':len(rows),
                         'rl_eligible':policy_row['rl_eligible'],'selected_episode_id':selected['episode_id'],'episodes':archive_episodes})
    _write_csv(outdir/'patients.csv',list(patients[0]),patients)
    _write_csv(outdir/'policy_gspo.csv',list(policy[0]),policy)
    for split in ('train','validation','test'):_write_csv(outdir/f'{split}.csv',list(policy[0]),[r for r in policy if r['split']==split and r['rl_eligible']])
    if all_labs:
        fields=['patient_id','episode_id','split','event_id','day','key','name','unit','value','comparator','bound','kind','source_flag','text','time_precision','source_field','source_span']
        _write_csv(outdir/'daily_labs.csv',fields,all_labs)
        dictionary={}
        for item in all_labs:
            key=item['key']
            entry=dictionary.setdefault(key,{'metric_key':key,'names':set(),'units':set(),'observation_count':0,
                'default_reward_support':key in ('ph','paco2','crp','wbc'),
                'note':'须逐病例核对参考范围适用性；源箭头不等于新生儿正常范围'})
            entry['names'].add(item['name']);entry['units'].add(item['unit']);entry['observation_count']+=1
        for entry in dictionary.values():
            entry['names']=sorted(entry['names']);entry['units']=sorted(entry['units'])
        _write_csv(outdir/'metric_dictionary.csv',list(next(iter(dictionary.values()))),dictionary.values())
    if all_actions:_write_csv(outdir/'treatment_events.csv',list(all_actions[0]),all_actions)
    if transitions:_write_csv(outdir/'dynamics_transitions.csv',list(transitions[0]),transitions)
    _write_csv(outdir/'private/source_index.csv',list(index_rows[0]),index_rows)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=before_hash:raise RuntimeError('Source changed while preparing data')
    report={'data_version':DATA_VERSION,'policy_prompt_format':'compact-reports-2.0','source_sha256':before_hash,'source_unchanged':True,'counts':dict(audit),
        'patients':len(patients),'encounters_after_deduplication':sum(p['encounter_count'] for p in patients),
        'policy_rows':len(policy),'rl_eligible_patients':sum(r['rl_eligible'] for r in policy),
        'eligible_split_counts':dict(Counter(r['split'] for r in policy if r['rl_eligible'])),
        'exclusion_reasons':dict(exclusions),'lab_observations':len(all_labs),'metric_coverage':dict(coverage),
        'treatment_mentions':len(all_actions),'dated_treatment_mentions':sum(a['day'] is not None for a in all_actions),
        'observational_transitions':len(transitions),'transitions_with_aligned_action':sum(t['temporal_alignment_supported'] for t in transitions),
        'future_real_records_in_policy_prompt':False,'optimal_labels_claimed':False,'model_api_calls':0,'training_executed':False,
        'reward_config':asdict(RewardConfig()),'guidelines':guidance['sources'],
        'limits':['Dates are calendar-day summaries, not daily administration or report-release records.',
                  'Not every calendar day has tests; missing days are not synthesized.',
                  'Only applicable sourced reference rules are scored; source arrows are not assumed neonatal truth.',
                  'Qualitative/culture reports are archived but excluded from policy prompts without verified release times.',
                  'Historical future outcomes are never counterfactual rewards for newly sampled treatments.',
                  'Independent frozen dynamics and clinical-safety evaluation must be connected before RL.']}
    if parquet:
        from .verl_adapter import export_parquet
        report['parquet_counts']=export_parquet(outdir/'policy_gspo.csv',outdir/'verl')
    (outdir/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def raw_date_sort(text):
    day=calendar(text)
    return day.isoformat() if day else '9999-99-99'
