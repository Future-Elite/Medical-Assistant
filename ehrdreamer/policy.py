from __future__ import annotations

from .catalog import EXAMS, TREATMENTS
from .contracts import Decision, Parameters
from .workflow import routine_action
from .learning.runtime import LearnedDynamics


class LLMDoctor:
    def __init__(self,client):self.client=client

    def decide(self,view):
        routine=routine_action(view)
        if routine is not None:return routine
        system=(
            'You are the single simulated neonatal physician policy in EHR Dreamer. Choose ONE next clinical action. '
            'Follow workflow.allowed_actions. Admission assessment is ordered ONCE as one group. '
            'Use its released reports to form working diagnoses, then START appropriate treatment or supportive_care. '
            'Do not repeat an admission check, order whole panels in treatment, or poll static vital signs. '
            'supportive_care is an observation/support plan, not an invented drug or fitted treatment effect. '
            'After selecting/adjusting a regimen, choose review_treatment to confirm the plan and proceed. '
            'The scheduler waits and issues only due targeted follow-ups linked to that plan. '
            'After a follow-up, adjust/continue treatment or finish_treatment to generate treatment results. '
            'Do not use further tests to postpone the outcome or force cure/discharge. '
            'You only see intake and RELEASED observations; never invent results, source diagnoses, doses or future events. '
            'Use learned_capabilities; unsupported measurements cannot be generated. '
            'No causal treatment effect or dose response has been identified. '
            'Cite known report event_ids. Return JSON matching the schema.'
        )
        return self.client.request(system,{
            'agent_view':view.model_dump(mode='json'),
            'exam_catalog':{k:v for k,v in EXAMS.items() if k in view.guidance.get('learned_capabilities',{}).get('supported_exams',EXAMS)},
            'treatment_catalog':TREATMENTS,'schema':Decision.model_json_schema()},Decision,operation='dreamer_policy')


DemoDynamics=LearnedDynamics


class DemoDoctor:
    """Offline clinical controller using the same finite care workflow as API mode."""
    def decide(self,view):
        routine=routine_action(view)
        if routine is not None:return routine
        reports=list(view.observations)
        def latest(target):return next((r for r in reversed(reports) if r['target']==target),None)
        def vals(target):
            report=latest(target)
            return {i['key']:i['value'] for i in report['items']} if report else {}
        def act(kind,target='',why='按当前治疗计划复评',params=None,diagnosis=None,evidence=None):
            ids=[r['event_id'] for r in reports[-4:]] if evidence is None else evidence
            return Decision(action_type=kind,target=target,rationale=why,parameters=Parameters(**(params or {})),
                            diagnosis=diagnosis,evidence_ids=tuple(ids))
        vital=vals('vitals');infection=vals('infection_panel')
        jaundice=any(s in view.intake.symptoms for s in ('黄疸','黄染'))
        respiratory=any(s in view.intake.symptoms for s in ('呼吸急促','气促','呻吟','呼吸困难','三凹征','发绀','咳嗽'))
        images=vals('chest_xray') or vals('lung_ultrasound')
        finding=images.get('finding','')
        if not view.conditions:
            if ('RDS' in finding or '弥漫' in finding) and view.intake.gestational_weeks is not None and view.intake.gestational_weeks<37:code='rds'
            elif infection.get('crp',0)>12 and ('实变' in finding or '异常' in finding and '未见' not in finding):code='pneumonia'
            elif respiratory:code='respiratory_distress'
            elif jaundice:code='jaundice'
            elif any(r in view.intake.perinatal_risks for r in ('母体感染风险','胎膜早破')):code='infection_risk'
            else:code='neonatal_observation'
            return act('diagnose',code,'依据本次入院检查组形成工作诊断；不追加重复的入院检查。',diagnosis=code)
        codes={c['code'] for c in view.conditions}
        if jaundice and 'jaundice' not in codes and view.workflow.get('care_started_at') is None:
            return act('diagnose','jaundice','入院检查已包含胆红素，分别记录合并问题。',diagnosis='jaundice',evidence=[latest('bilirubin')['event_id']])
        active={t['target']:t for t in view.active_treatments}
        stopped={o['target'] for o in view.orders if o.get('status')=='stopped'}
        if vital.get('spo2',100)<92 and not any(k in active for k in ('cpap','oxygen')) and not any(k in stopped for k in ('cpap','oxygen')):
            mode='cpap' if view.intake.gestational_weeks is not None and view.intake.gestational_weeks<37 else 'oxygen'
            return act('start_treatment',mode,'入院已发布低氧观察，先记录模拟支持动作，再按计划复评。',{'fio2':.30,'pressure_cm_h2o':6 if mode=='cpap' else None})
        if infection.get('crp',0)>12 and (respiratory or any(r in view.intake.perinatal_risks for r in ('母体感染风险','胎膜早破'))) and 'antimicrobial' not in active and 'antimicrobial' not in stopped:
            return act('start_treatment','antimicrobial','结合入院感染指标及已知临床背景记录研究级抗感染方案，随后只复查相关指标。')
        if 'rds' in codes and view.intake.birth_weight_g and 'cpap' in active and 'surfactant' not in active and 'surfactant' not in stopped:
            return act('start_treatment','surfactant','已有工作诊断及支持记录，记录研究级治疗动作。')
        if view.workflow.get('care_started_at') is None:
            return act('start_treatment','supportive_care','当前不自动虚构药物指征，明确一般支持与观察计划；治疗期仅按问题复查特定指标。')
        return act('review_treatment',why='结合本轮已发布报告复评并确认当前治疗/观察计划，按计划推进；不重复申请整套检查。')
