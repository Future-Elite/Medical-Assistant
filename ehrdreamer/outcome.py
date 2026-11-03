from __future__ import annotations

from .catalog import FOLLOW_UP, TREATMENTS
from .contracts import OutcomeProposal, ConditionOutcome


def outcome_constraints(env):
    ids=[o['event_id'] for o in env.observations]
    allowed={};condition_evidence={}
    def series(target,key):
        return [(o,next(i['value'] for i in o['items'] if i['key']==key))
                for o in env.observations if o['target']==target and any(i['key']==key for i in o['items'])]
    def declining(target,key,margin=0):
        values=series(target,key)
        return len(values)>1 and values[-1][0]['time_hours']>values[0][0]['time_hours'] and values[-1][1]<values[0][1]-margin
    vs=[o for o in env.observations if o['target']=='vitals']
    respiratory_improved=False
    if len(vs)>1 and vs[-1]['time_hours']>vs[0]['time_hours']:
        first={i['key']:i['value'] for i in vs[0]['items']};last={i['key']:i['value'] for i in vs[-1]['items']}
        respiratory_improved=(last['fio2']<=first['fio2'] and last['cpap']<=first['cpap'] and
                              (last['spo2']>first['spo2']+.5 or last['respiratory_rate']<first['respiratory_rate']-2))
    for condition in env.conditions:
        # No unobserved latent improvement is turned into a confirmed clinical fact.
        statuses=['unknown','unresolved']
        code=condition['code']
        targets={'jaundice':('bilirubin',),'infection_risk':('infection_panel',),
                 'pneumonia':('vitals',),'rds':('vitals',),'respiratory_distress':('vitals',)}.get(code,())
        improved=declining('bilirubin','bilirubin') if code=='jaundice' else declining('infection_panel','crp',.5) if code=='infection_risk' else respiratory_improved if code in ('pneumonia','rds','respiratory_distress') else False
        if improved:statuses+=['improved']
        allowed[condition['condition_id']]=statuses
        condition_evidence[condition['condition_id']]=[o['event_id'] for o in env.observations if o['target'] in targets]
    return {'disposition':'home' if env.terminal=='discharged' else 'transfer' if env.terminal=='transferred' else 'ongoing',
            'allowed_overall':(['improved','unresolved','unknown'] if any('improved' in s for s in allowed.values()) else ['unresolved','unknown']) if env.terminal=='treatment_completed' else (['improved','unknown'] if any('improved' in s for s in allowed.values()) else ['unknown']) if env.terminal=='discharged' else ['unresolved','unknown'],
            'conditions':allowed,'condition_evidence_ids':condition_evidence,'evidence_ids':ids}


def validate_outcome(proposal,constraints):
    if proposal.disposition!=constraints['disposition'] or proposal.overall_outcome not in constraints['allowed_overall']:
        raise ValueError('Outcome conflicts with episode termination evidence')
    if {c.condition_id for c in proposal.per_condition}!=set(constraints['conditions']) or len(proposal.per_condition)!=len(constraints['conditions']):
        raise ValueError('Every working condition requires exactly one outcome')
    known=set(constraints['evidence_ids'])
    if not set(proposal.selected_evidence_ids)<=known:raise ValueError('Unknown final evidence')
    if known and not proposal.selected_evidence_ids:raise ValueError('Final record requires observation evidence')
    for item in proposal.per_condition:
        if item.status not in constraints['conditions'][item.condition_id] or not set(item.evidence_ids)<=known:
            raise ValueError('Condition outcome exceeds available evidence')
        if item.status=='improved' and len(set(item.evidence_ids)&set(constraints['condition_evidence_ids'][item.condition_id]))<2:
            raise ValueError('Improvement requires condition-specific comparison observations')


class LLMOutcome:
    def __init__(self,client):self.client=client
    def summarize(self,env):
        constraints=outcome_constraints(env)
        return self.client.request(
            'You are the final outcome/document component of EHR Dreamer. Summarize only the supplied generated '
            'clinical evidence. Return structured outcome codes, per-condition statuses and event references. '
            'Do not invent lab values, prescriptions, diagnoses or cure. Budget exhaustion means ongoing. '
            'Use exactly the allowed disposition, statuses and evidence. Text is rendered from your selected '
            'facts by the application. Return schema JSON only.',
            {'record':env.public_record(),'constraints':constraints,'follow_up_catalog':FOLLOW_UP,'schema':OutcomeProposal.model_json_schema()},
            OutcomeProposal,operation='dreamer_outcome',validator=lambda p:validate_outcome(p,constraints))


class DemoOutcome:
    def summarize(self,env):
        constraints=outcome_constraints(env);ids=tuple(constraints['evidence_ids'][-4:])
        overall='improved' if 'improved' in constraints['allowed_overall'] else constraints['allowed_overall'][0]
        return OutcomeProposal(disposition=constraints['disposition'],overall_outcome=overall,
                               per_condition=tuple(ConditionOutcome(condition_id=c['condition_id'],
                                   status='improved' if 'improved' in constraints['conditions'][c['condition_id']] else 'unknown',
                                   evidence_ids=tuple(constraints['condition_evidence_ids'][c['condition_id']][-4:])) for c in env.conditions),
                               selected_evidence_ids=ids,follow_up_codes=('receiving_hospital',) if env.terminal=='transferred' else ('neonatal_review','feeding_weight_review'))


def build_discharge(env,proposal):
    constraints=outcome_constraints(env);validate_outcome(proposal,constraints)
    by_id={o['event_id']:o for o in env.observations}
    selected=[by_id[i] for i in proposal.selected_evidence_ids]
    last={o['target']:o for o in env.observations}
    latest_metrics={item['key']:{**item,'reported_at_hours':o['time_hours']} for o in env.observations for item in o['items']}
    observations=list(latest_metrics.values())
    monitored={item['key'] for o in env.observations if o.get('session_type')=='followup' for item in o['items']}
    trends=[]
    for metric in sorted(monitored):
        readings=[(o,item) for o in env.observations for item in o['items'] if item['key']==metric and isinstance(item['value'],(int,float))]
        if len(readings)<2 or readings[-1][0]['time_hours']<=readings[0][0]['time_hours']:continue
        before,after=readings[0],readings[-1];delta=after[1]['value']-before[1]['value']
        trends.append({'key':metric,'name':after[1]['name'],'unit':after[1]['unit'],
                       'before':before[1]['value'],'after':after[1]['value'],'delta':round(delta,4),
                       'direction':'decreased' if delta<0 else 'increased' if delta>0 else 'unchanged',
                       'before_event_id':before[0]['event_id'],'after_event_id':after[0]['event_id'],
                       'clinical_significance':'not_automatically_inferred'})
    complete=env.terminal in ('discharged','transferred')
    finished=env.terminal=='treatment_completed'
    executed=list(dict.fromkeys(e['target'] for e in env.events if e['event_type']=='treatment' and e['status']=='executed'))
    completed_followups=env.workflow['review_count']-int(env.workflow['review_pending'])
    summary='本次记录'+('、'.join(TREATMENTS[k]['name'] for k in executed) or '已有治疗计划')+f"，完成{completed_followups}轮针对性复查。"
    if trends:summary+='；'.join(f"{t['name']}由{t['before']}变为{t['after']}{t['unit'] or ''}" for t in trends)+'。'
    summary+='逐诊断疗效依据已有报告记录，未覆盖部分保留待评估；指标变化不自动解释为治疗因果效果。'
    issues=env.discharge_blockers() if not finished else []
    if finished:
        if not {'spo2','respiratory_rate','temperature'}<=set(env.capabilities['transition_metrics']):
            issues.append('当前数据尚未覆盖完整生命体征变化，临床出院情况需另行核实；不追加重复检查来填补模型能力。')
        unresolved=[c['name'] for c in env.conditions if next(p.status for p in proposal.per_condition if p.condition_id==c['condition_id']) in ('unknown','unresolved')]
        if unresolved:issues.append('仍需后续评估的问题：'+'、'.join(unresolved))
        remaining=[target for target in env.treatments if target!='supportive_care']
        if remaining:issues.append('记录结束时仍有治疗计划，按实际临床评估决定继续或调整；本次不虚构停药。')
    return {'document_type':'treatment_outcome_summary' if finished else 'discharge_summary' if complete else 'interim_outcome',
            'title':'治疗后情况与治疗结果' if finished else '出院小结' if env.terminal=='discharged' else '转院交接记录' if env.terminal=='transferred' else '阶段结局记录（尚未出院）',
            'disposition':proposal.disposition,'overall_outcome':proposal.overall_outcome,
            'per_condition_outcomes':[{**p.model_dump(mode='json'),'name':next(c['name'] for c in env.conditions if c['condition_id']==p.condition_id)} for p in proposal.per_condition],
            'discharge_status':'本次检查—治疗—针对性复评流程已结束，依据实际生成的报告记录治疗结果；该结果不自动等于已治愈或已获准临床出院。' if finished else '已通过本研究环境的可见证据门控；逐项疗效按对应复查证据记录，不断言全部治愈。' if env.terminal=='discharged' else '模拟转院，待接收机构进一步评估。' if env.terminal=='transferred' else '模拟已停止，当前病程尚未完成；停止原因见时间线。',
            'latest_observations':observations,'selected_evidence':selected,
            'treatment_result_text':summary,'monitored_indicator_changes':trends,
            'hospital_course':[{'time_hours':e['time_hours'],'name':e['name'],'operation':e['parameters'].get('operation'),
                                'event_id':e['event_id']} for e in env.events if e['event_type'] in ('diagnosis','treatment','treatment_review','treatment_end','consult')],
            'care_summary':{'initial_assessment_count':sum(o.get('session_type')=='admission' for o in env.orders),
                            'followup_rounds':completed_followups,'followup_orders':env.workflow['review_count'],'planned_max_followups':env.workflow['max_followups'],
                            'treatment_trajectory_complete':finished or complete,'active_treatments_at_end':list(env.treatments)},
            'follow_up_plan':[FOLLOW_UP[c] for c in dict.fromkeys(proposal.follow_up_codes)],
            'unresolved_issues':issues if env.terminal!='discharged' else ['工作诊断仍需结合后续专科随访复核。'],
            'evidence_ids':list(proposal.selected_evidence_ids),'synthetic':True,
            'medication_prescriptions':[], 'prescription_note':'本版本记录抽象治疗操作，未生成真实个体处方或医师签名。'}
