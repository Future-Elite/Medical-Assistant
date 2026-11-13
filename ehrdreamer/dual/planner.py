from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean

from ehrworld.llm import ModelResponseError, ContextLimitError
from ehrdreamer.prompts import policy_prompt, PROMPT_VERSION
from .catalog import TREATMENTS, CONDITIONS
from .contracts import CandidatePlan, PlanAction, PlanProposal, PlanSelection
from .dynamics import apply_action, DynamicsError
from .knowledge import get_graph, visible_facts
from .protocol import PROTOCOL_VERSION, policy_view, metric_labels
from .references import score


class PlanningError(ValueError):
    def __init__(self, message, code='ungrounded_clinical_decision'):
        super().__init__(message)
        self.code = code


def model_knowledge(context):
    result={k: copy.deepcopy(context[k]) for k in
            ('version', 'queries', 'patient_facts', 'recommendations', 'excluded_recommendations', 'sources', 'boundary')}
    result['available_monitoring_panels']=[{k:v for k,v in p.items() if k!='metric_keys'} for p in context.get('available_monitoring_panels',[])]
    return result


def proposal_payload(env, end, visible=None, knowledge=None):
    context = knowledge or get_graph().retrieve(visible=env.view())
    return {'task': 'propose_plans', 'prompt_version': PROMPT_VERSION, 'protocol_version': PROTOCOL_VERSION,
            'visible_patient': policy_view(env.view()) if visible is None else visible,
            'knowledge_context': model_knowledge(context),
            'allowed_actions': ['start', 'adjust', 'stop', 'continue', 'wait', 'order_test'],
            'diagnostic_tests':{'culture':'病原学培养/血培养','imaging':'肺部影像评估'},
            'metric_labels': metric_labels(env.state['baseline']),
            'treatment_catalog': {k: {**v, 'aliases': get_graph().nodes['action:'+k]['aliases']} for k, v in TREATMENTS.items()},
            'clinical_constraints': ['先判断当前是否有治疗指征，不能仅凭预测异常数归零认定无需治疗。',
                                     '没有活动治疗禁止continue；观察必须引用适用的观察依据和已知稳定性证据。',
                                     '同一offset_hours=0可为本轮组合处置；以后的动作只是假设计划。',
                                     '只允许所给目录及参数；不编造药物剂量。'],
            'diagnosis_catalog': CONDITIONS, 'evaluation_metric_keys': list(env.rules),
            'reference_rules': env.rules, 'forecast_end_hours': end,
            'planning_budget': {'decision_hours': env.request.decision_hours, 'max_actions_per_plan': 6,
                                'max_evidence_rounds': 2},
            'max_candidates': env.request.max_candidates-(1 if env.active else 0),
            'response_schema': PlanProposal.model_json_schema()}


def validate_plan(plan, active, visible_ids, metrics, horizon, *, assessment=None, knowledge=None, visible=None):
    if plan.actions[0].offset_hours != 0 or list(a.offset_hours for a in plan.actions) != sorted(a.offset_hours for a in plan.actions):
        raise PlanningError('候选动作需要从当前时刻开始并按时间排序。')
    if not set(plan.review_metrics) <= set(metrics):
        raise PlanningError('复查只能选择已有真实基线的指标。')
    if assessment and not plan.review_metrics:
        raise PlanningError('请根据本轮临床问题指定复查项目或可用监测面板。')
    current = [a for a in plan.actions if a.offset_hours == 0]
    if any(a.operation in ('continue','wait') for a in current) and any(a.operation in ('start','adjust','stop') for a in current):
        raise PlanningError('组合处置不能混入没有实际操作的维持或等待。')
    if any(a.operation == 'continue' for a in current) and not active:
        raise PlanningError('尚无活动治疗，不能维持当前方案。')
    if assessment:
        rules = {r['id']: r for r in knowledge['recommendations']}
        if not set(assessment.evidence_ids) <= visible_ids or not set(assessment.guideline_rule_ids) <= set(rules):
            raise PlanningError('临床评估必须引用已发布证据及本轮检索到的推荐。')
        facts = visible_facts(visible)
        if assessment.mode == 'needs_clinical_review':
            raise PlanningError('当前资料不足以形成可执行方案，需补充临床评估。', 'clinical_review_required')
        if any(a.operation in ('wait', 'continue') for a in current):
            if facts['red_flags']:
                raise PlanningError('已有危险征象，不能用预测指标正常替代及时处理。')
            if active:
                if assessment.mode != 'maintain_current' or any(a.operation not in ('continue','order_test') for a in current):
                    raise PlanningError('活动治疗需要明确维持指征，不能伪装为未治疗观察。')
            else:
                if assessment.mode != 'observe_without_treatment' or any(a.operation not in ('wait','order_test') for a in current):
                    raise PlanningError('无治疗时只能选择有依据的临床观察，不能维持空方案。')
                if 'OBS-LOW-RISK' not in assessment.guideline_rule_ids:
                    raise PlanningError('观察方案缺少适用的风险分层推荐。')
                if facts['clinical_findings'].get('clinically_well') is not True:
                    raise PlanningError('来源没有明确的稳定临床评估，不能仅用两项血气推断可安全观察。', 'clinical_review_required')
                if rules['OBS-LOW-RISK']['population_match'] != 'population_matches':
                    raise PlanningError('观察依据的人群条件未被核实。', 'clinical_review_required')
        elif assessment.mode != 'treatment_required':
            raise PlanningError('治疗操作与本轮临床评估结论不一致。')
        if assessment.mode=='treatment_required' and not any(a.operation in ('start','adjust','stop') for a in current):
            raise PlanningError('有治疗指征时不能只申请检查而不处理。')
    active = copy.deepcopy(active)
    for index, action in enumerate(plan.actions):
        if action.offset_hours > horizon or not set(action.evidence_ids) <= visible_ids:
            raise PlanningError('动作时间或可见证据引用无效。')
        if action.operation=='order_test' and action.target not in ('culture','imaging'):
            raise PlanningError('培养或影像以外的已有数值项目请通过复查面板查询。')
        if action.operation=='order_test' and visible and any(p.get('target')==action.target for p in visible.get('pending_tests',[])):
            raise PlanningError('同一培养或影像仍待完成，不能重复申请。')
        if action.operation not in ('wait', 'continue','order_test') and action.target not in TREATMENTS:
            raise PlanningError('使用了目录外治疗。')
        parameters = action.parameters.model_dump(exclude_none=True)
        if any(p not in TREATMENTS.get(action.target, {}).get('parameters', []) for p in parameters):
            raise PlanningError('治疗参数超出该术语的定义。')
        if action.operation in ('wait', 'continue', 'stop') and parameters:
            raise PlanningError('等待、维持或停止不能暗含参数调整。')
        if assessment and action.operation not in ('wait', 'continue'):
            citations = set(action.guideline_rule_ids or assessment.guideline_rule_ids)
            if not citations <= set(rules):
                raise PlanningError('动作引用了未检索到的推荐。')
            if not any(action.target in rules[r]['monitoring_panels' if action.operation=='order_test' else 'actions'] for r in citations):
                raise PlanningError('引用推荐未支持或约束该治疗术语，不能将引用当装饰。')
        apply_action(active, [], action, action.offset_hours, f'validation:{index}')


class LLMPlanner:
    def __init__(self, client):
        self.client = client

    def decide(self, env, dynamics, progress=None):
        request = env.request
        end = min(env.time+request.forecast_horizon_hours, request.max_hours)
        observations = env.view()
        visible = policy_view(observations)
        known_ids = {o['event_id'] for o in observations['observations']} | {e['event_id'] for e in env.ledger}
        graph = get_graph()

        def log(stage, title, summary='', data=None, status='completed'):
            env.log_agent(stage, title, summary, data, status)
            if progress: progress(None, None, stage)

        queries = []
        knowledge = graph.retrieve(visible=observations)
        log('graph_retrieval', '检索医学术语与诊疗证据', '依据当前已发布资料进行图谱关系检索。', knowledge)
        proposal = None
        for round_index in range(3):
            payload = proposal_payload(env, end, visible, knowledge)
            log('policy_request', '策略模型评估当前诊疗指征', '先判断治疗、维持或观察的依据，再提出候选。', status='started')
            request_step=env.agent_steps[-1]
            def validate_proposal(reply):
                if reply.status == 'needs_evidence':
                    if not reply.knowledge_queries: raise PlanningError('补充证据请求缺少查询术语。')
                    return
                if reply.status == 'ready':
                    if reply.clinical_assessment is None: raise PlanningError('缺少临床指征评估。')
                    if not set(reply.clinical_assessment.evidence_ids) <= known_ids:
                        raise PlanningError('评估引用了不可见的病例证据。')
                    ids = {r['id'] for r in knowledge['recommendations']}
                    if not set(reply.clinical_assessment.guideline_rule_ids) <= ids:
                        raise PlanningError('评估引用了未检索到或人群不适用的推荐。')
            proposal = self.client.request(policy_prompt('propose_plans'), payload, PlanProposal,
                operation='dreamer_policy_propose', validator=validate_proposal)
            validate_proposal(proposal)
            request_step.update(status='completed',summary='已返回结构化诊疗评估或证据查询请求。')
            if proposal.status != 'needs_evidence': break
            if round_index == 2: raise PlanningError('图谱补充查询预算已用完，未伪造证据。')
            queries = list(dict.fromkeys([*queries, *proposal.knowledge_queries]))
            knowledge = graph.retrieve(queries, observations)
            log('graph_query', 'Agent补充查询图谱', '实际执行模型请求的术语检索。', knowledge)
        if proposal.status != 'ready' or not proposal.plans:
            raise PlanningError('缺少可执行诊疗方案，请复核临床资料。', 'clinical_review_required')
        assessment = proposal.clinical_assessment
        log('clinical_assessment', '诊疗指征评估结果', assessment.rationale, assessment.model_dump(mode='json'))
        plans = []
        if len(proposal.plans) > payload['max_candidates']: raise PlanningError('策略模型超过候选预算。')
        normalizations=[]
        for plan in proposal.plans:
            actions = tuple(a.model_copy(update={'target': graph.canonical_action(a.target)}) for a in plan.actions)
            normalizations.extend({'term':old.target,'canonical_id':new.target} for old,new in zip(plan.actions,actions) if old.target!=new.target)
            labels=metric_labels(env.state['baseline'])
            label_to_key={v:k for k,v in labels.items()}
            metrics=[label_to_key.get(k,k) for k in plan.review_metrics]
            panels={p['id']:p for p in knowledge.get('available_monitoring_panels',[])}
            for panel in plan.review_panels:
                if panel not in panels:raise PlanningError('所选复查面板无已初始化项目，不能虚构检验结果。')
                metrics.extend(panels[panel]['metric_keys'])
            plans.append(plan.model_copy(update={'actions': actions,'review_metrics':tuple(dict.fromkeys(metrics))}))
        if normalizations:log('term_normalization','归一化治疗术语','将模型使用的别名解析为目录内术语。',{'terms':normalizations})
        if env.active and assessment.mode == 'maintain_current' and plans:
            plans.append(CandidatePlan(candidate_id='baseline-continue',
                actions=(PlanAction(operation='continue', evidence_ids=assessment.evidence_ids,
                                    guideline_rule_ids=assessment.guideline_rule_ids),),
                review_metrics=plans[0].review_metrics,review_panels=plans[0].review_panels,
                reassess_after_hours=plans[0].reassess_after_hours,
                rationale='已有活动治疗；沿用本轮明确的监测安排，不强制固定血气检查。'))
        if len({p.candidate_id for p in plans}) != len(plans): raise PlanningError('候选方案ID重复。')
        log('proposal', '生成候选诊疗方案', '候选动作经过术语归一；未来操作尚未执行。',
            {'plans': [p.model_dump(mode='json') for p in plans]})
        belief = env.belief_from_visible()

        def evaluate(index, plan):
            try:
                validate_plan(plan, env.active, known_ids, belief['labs'], end-env.time,
                              assessment=assessment, knowledge=knowledge, visible=observations)
                ledger, active = copy.deepcopy(env.ledger), copy.deepcopy(env.active)
                for action_index, action in enumerate(plan.actions):
                    apply_action(active, ledger, action, env.time+action.offset_hours, f'plan:{plan.candidate_id}:{action_index}')
                counts = []
                for sample in range(request.planning_samples):
                    predicted = dynamics.transition(belief, env.context, ledger, end,
                        branch_id=f'plan:{env.episode_id}:{env.cycle}:{plan.candidate_id}:{sample}',
                        mode='planning', scenario=sample, visible_history=observations['observations'])
                    result = score(predicted['labs'], env.rules)
                    if not result['complete']: raise PlanningError('候选缺少完整可评分指标，不能以缺失获得低分。')
                    counts.append(result['abnormal_count'])
                return {'candidate_id': plan.candidate_id, 'validation_status': 'valid', 'clinically_eligible': True,
                        'expected_abnormal_count': mean(counts), 'sample_counts': counts,
                        'forecast_end_hours': end, 'predicted_metric_count': len(belief['labs']),
                        'scored_metric_count': len(env.rules), 'proposal_order': index,
                        'sample_spread': max(counts)-min(counts), 'plan': plan.model_dump(mode='json')}
            except (ModelResponseError, ContextLimitError): raise
            except (ValueError, RuntimeError) as exc:
                return {'candidate_id': plan.candidate_id, 'validation_status': 'invalid', 'clinically_eligible': False,
                        'expected_abnormal_count': None, 'forecast_end_hours': end,
                        'error': str(exc) if isinstance(exc, (PlanningError, DynamicsError)) else '候选校验未通过',
                        'proposal_order': index, 'plan': plan.model_dump(mode='json')}

        evaluations = []
        with ThreadPoolExecutor(max_workers=min(3, len(plans))) as pool:
            futures = [pool.submit(evaluate, i, plan) for i, plan in enumerate(plans)]
            for future in as_completed(futures):
                result = future.result(); evaluations.append(result)
                log('candidate_evaluation', '候选校验与联合预测',
                    '临床条件通过后才比较研究异常计数。' if result['validation_status']=='valid' else result['error'],
                    result, 'completed' if result['validation_status']=='valid' else 'rejected')
                if progress: progress(len(evaluations), len(plans), 'forecasting')
        evaluations.sort(key=lambda e: e['proposal_order'])
        valid = [e for e in evaluations if e['validation_status']=='valid']
        if not valid: raise PlanningError('全部候选均未通过临床条件或预测校验；请查看Agent步骤中的拒绝原因。')
        best = min(valid, key=lambda e: (e['expected_abnormal_count'], e['sample_spread'], e['proposal_order']))
        selection_payload = {'task':'select_next_action', 'protocol_version':PROTOCOL_VERSION, 'visible_patient':visible,
            'clinical_assessment':assessment.model_dump(mode='json'), 'evaluated_plans':evaluations,
            'approved_plan_id':best['candidate_id'], 'objective_order':['clinical_eligibility', 'expected_abnormal_count'],
            'tie_break_order':['sample_spread', 'proposal_order'], 'response_schema':PlanSelection.model_json_schema()}
        def validate(reply):
            if reply.status != 'selected' or reply.selected_plan_id != best['candidate_id']:
                raise PlanningError('策略选择与程序计算的合规候选不一致。')
        selection = self.client.request(policy_prompt('select_next_action'), selection_payload, PlanSelection,
                                         operation='dreamer_policy_select', validator=validate)
        validate(selection)
        chosen = next(p for p in plans if p.candidate_id == selection.selected_plan_id)
        log('decision', '确定本轮执行方案', selection.rationale,
            {'clinical_assessment':assessment.model_dump(mode='json'), 'selected_plan':chosen.model_dump(mode='json'),
             'forecast_end_hours':end, 'expected_abnormal_count':best['expected_abnormal_count']})
        return chosen, evaluations, selection.rationale, tuple(d for d in proposal.working_diagnoses if d in CONDITIONS)
