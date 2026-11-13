"""Synthetic interface examples, produced with the runtime's actual payload builders."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ehrdreamer.prompts import DYNAMICS_SYSTEM_PROMPT, PROMPT_VERSION, policy_prompt
from .contracts import GenerationRequest, PlanAction
from .dynamics import apply_action
from .environment import PatientEnvironment
from .planner import proposal_payload
from .protocol import PROTOCOL_VERSION, dynamics_payload
from .references import rules_for
from .source import extract_baseline


def messages(system, payload):
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]


def model_protocol_examples():
    # Entirely artificial, independent of the workbook, credentials and API.
    intake = {'sex': '男', 'gestational_weeks': 32, 'gestational_days': 0,
              'postnatal_age_hours': 0, 'birth_weight_g': 1600, 'chief_complaint': '气促',
              'symptoms': ['气促'], 'perinatal_risks': []}
    baseline = extract_baseline({'入院时间': '2024-01-01',
                                '入院时主要症状及体征': 'pH:7.20，PaCO2:62mmHg，CRP:<0.50mg/L'})
    request = GenerationRequest(case_id='synthetic-interface-example', max_cycles=3,
                                start_time=datetime(2024, 1, 1, tzinfo=timezone.utc))
    env = PatientEnvironment({'intake': intake, 'baseline': baseline}, request, 'EXAMPLE',
                             rules_for(intake, baseline), {})
    action = PlanAction(operation='start', target='cpap', parameters={'fio2': .3, 'pressure_cm_h2o': 6})
    event = env.record('treatment', '合成示例：呼吸支持', 'executed', actor='simulated_doctor', target='cpap')
    apply_action(env.active, env.ledger, action, 0, event['event_id'])
    # Interface fixture numbers only, never a claim about treatment efficacy.
    for key, value in {'ph': 7.28, 'paco2': 52, 'crp': .4}.items():
        env.state['labs'][key].update(value=value, comparator=None, bound=None,
                                      as_of_hours=12, origin='llm_prediction')
    env.state.update(clock_hours=12, version=1)
    env.time = 12
    env.cycle = 1
    env.collect(['ph', 'paco2'], sample_id='example-review')
    end = 24
    def dynamics_input(state, mode):
        return dynamics_payload(state, env.context, env.ledger, end, mode=mode,
                                scenario=0, visible_history=env.observations)
    return {'protocol_version': PROTOCOL_VERSION, 'prompt_version': PROMPT_VERSION,
            'synthetic_example': True,
            'description': '纯人工接口示例，使用实际运行时组装函数；数值不表示医学预测效果，未调用API。',
            'policy': {'messages': messages(policy_prompt('propose_plans'), proposal_payload(env, 36))},
            'dynamics_planning': {'messages': messages(DYNAMICS_SYSTEM_PROMPT, dynamics_input(env.belief_from_visible(), 'planning'))},
            'dynamics_execution': {'messages': messages(DYNAMICS_SYSTEM_PROMPT, dynamics_input(env.state, 'execution')),
                                   'example_response': {'pH': 7.31, 'PaCO2(mmHg)': 48, 'CRP(mg/L)': .4}}}
