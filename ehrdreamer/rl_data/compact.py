"""Compact model-facing reports. Provenance stays outside model conversations."""
import json

DISPLAY={'ph':'pH','paco2':'PaCO2','sao2':'SaO2','spo2':'SpO2','crp':'CRP','pct':'PCT','wbc':'WBC',
         'bilirubin':'总胆红素','hemoglobin':'血红蛋白','platelet':'血小板','sodium':'钠','potassium':'钾',
         'chloride':'氯','glucose':'葡萄糖','lactate':'乳酸','calcium_total':'总钙','calcium_ionized':'离子钙'}


def report_json(state):
    """One check -> one flat dict. Censoring survives; ambiguous values are null."""
    result={}
    for storage_key,item in state.items():
        key=item.get('key',storage_key);unit=item.get('unit','')
        name=DISPLAY.get(key,item.get('name',key))
        label=name if unit in ('','unitless') else f'{name}({unit})'
        value=item.get('value')
        if item.get('ambiguous_same_day'):value=None
        elif value is None and item.get('comparator') and item.get('bound') is not None:
            value=f"{item['comparator']}{item['bound']:g}"
        elif value is None and item.get('text'):value=item['text']
        if label in result and result[label]!=value:result[label]=None
        else:result[label]=value
    return result


def patient_json(intake):
    keys={'sex':'sex','gestational_weeks':'ga_weeks','gestational_days':'ga_days',
          'postnatal_age_hours':'age_hours','birth_weight_g':'birth_weight_g',
          'chief_complaint':'complaint','symptoms':'symptoms','perinatal_risks':'risks'}
    return {label:intake[key] for key,label in keys.items() if intake.get(key) not in (None,'',[],())}


POLICY_SYSTEM='''你是新生儿诊疗策略模型。依据患者、历史、当前化验及所给指南选择治疗，使72小时后固定评价项目异常最少，并减少过程异常与反弹。不得编造检验值或药物剂量。仅输出JSON：actions、monitoring_metric_keys、uncertainty。动作字段：operation、target、at_hours、reassess_after_hours、evidence_ids、guideline_rule_ids、rationale。依据只引用报告ID和指南ID；理由简短。'''


def compact_policy_prompt(old_user):
    history=[];evidence={};latest_ids={}
    for index,report in enumerate(old_user['clinical_history']['lab_history'],1):
        rid=f'L{index}'
        history.append({'day':report['day'],'id':rid,'labs':report_json(report['labs'])})
        for item in report['labs'].values():
            for original in item.get('evidence_ids',[item['event_id']]):evidence[original]=rid
        latest_ids[rid]=True
    current_id=f'L{len(history)+1}'
    for item in old_user['current_labs'].values():
        for original in item.get('evidence_ids',[item['event_id']]):evidence[original]=current_id
    treatments=[]
    for index,event in enumerate(old_user['clinical_history']['treatment_history'],1):
        aid=f'A{index}';evidence[event['event_id']]=aid
        treatments.append({'day':event['day'],'id':aid,'action':event['operation'],'treatment':event['target']})
    rules=old_user['fixed_evaluation_rules']
    # A few clinically relevant guide sentences, not repeated PDF metadata.
    guide_ids=['PN-ASSESS','PN-LABS','PN-ANTIBIOTIC','PN-RESPIRATORY']
    if (old_user['patient'].get('gestational_weeks') or 40)<37:
        guide_ids+=['RDS-CPAP','RDS-PS','RDS-ANTIBIOTIC','RDS-SUPPORT','RDS-VENTILATION','RDS-OXYGEN']
    guides={r['rule_id']:r['summary'] for r in old_user['guideline_summaries'] if r['rule_id'] in guide_ids}
    payload={'patient':patient_json(old_user['patient']),'day':old_user['decision_day'],
             'history':history,'treatments':treatments,
             'current':{'id':current_id,'labs':report_json(old_user['current_labs'])},
             'evaluate':{k:{f:v for f,v in rule.items() if f in ('unit','lower','upper','upper_inclusive') and v is not None} for k,rule in rules.items()},
             'guidelines':guides,'allowed_treatments':list(old_user['allowed_treatments'])}
    if old_user.get('last_known_results_with_age'):
        older={}
        for key,item in old_user['last_known_results_with_age'].items():
            day=old_user['decision_day']-1-item.get('observation_age_days',0)
            older.setdefault(day,{})[key]=item
        # These values already appear in history; do not repeat every metric.
        if not history:payload['older_labs']={str(day):report_json(state) for day,state in older.items()}
    return [{'role':'system','content':POLICY_SYSTEM},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}],evidence
