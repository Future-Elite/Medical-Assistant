from .contracts import InverseProposal, InverseCandidate


def infer(models,before,after,backend):
    """Retrospective only. No actual intervening actions or hidden states supplied."""
    if after['time_hours']<=before['time_hours']:raise ValueError('后一个报告时间必须晚于前一个报告。')
    if before['target']!=after['target']:raise ValueError('请选择同类检查的两个报告进行回顾。')
    if backend=='demo':
        proposal=InverseProposal(candidates=(InverseCandidate(target='support_or_natural_evolution',explanation='变化可能来自支持、自然病程或测量差异，不能唯一反推出治疗。'),),
                                 uncertainty='离线候选演示；仅有前后报告不能确定实际执行过什么动作。')
    else:
        proposal=models.clients['outcome'].request(
            'You are a retrospective inverse dynamics helper. Infer POSSIBLE abstract interventions between two '
            'released observations. Natural progression and measurement noise are alternatives. Actual actions '
            'are intentionally withheld. Never claim unique identification or ground truth; give uncertainty. '
            'Do not recommend treatment or doses. Return JSON matching schema.',
            {'before_observation':before,'after_observation':after,'schema':InverseProposal.model_json_schema()},
            InverseProposal,operation='dreamer_inverse')
    return {**proposal.model_dump(mode='json'),'evidence_status':'inferred','ambiguous':True,
            'online_state_modified':False,'before_event_id':before['event_id'],'after_event_id':after['event_id']}
