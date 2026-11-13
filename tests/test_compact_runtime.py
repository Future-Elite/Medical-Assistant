"""Runtime protocol checks; artificial fixtures only, no training-data generation."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from ehrworld.llm import APIConfig, JSONClient, ModelResponseError
from ehrworld.web.app import create_app
from ehrdreamer.dual.contracts import TransitionReply
from ehrdreamer.dual.dynamics import DynamicsError, LLMDynamics
from ehrdreamer.dual.examples import model_protocol_examples
from ehrdreamer.dual.protocol import dynamics_payload, labs_json, metric_labels, policy_view
from test_dual_llm import case, env, SyntheticTransport


def test_each_policy_report_is_one_flat_json_without_repeated_current():
    e = env()
    e.time = 12
    e.state['labs']['ph'].update(value=7.28, as_of_hours=12)
    e.collect(['ph'], sample_id='review', delay=3)
    before = policy_view(e.view())
    assert before['current']['at_hours'] == 0
    assert before['pending_tests']
    e.time = 15
    e._publish()
    visible = policy_view(e.view())
    assert len(visible['history']) == 1
    assert visible['history'][0]['labs'] == {'pH': 7.2, 'PaCO2(mmHg)': 62, 'CRP(mg/L)': '<0.5'}
    assert visible['current']['labs'] == {'pH': 7.28}
    assert visible['current']['at_hours'] == 12 and visible['current']['reported_at_hours'] == 15
    assert visible['history'][0]['id'] != visible['current']['id']
    assert all('items' not in r for r in [*visible['history'], visible['current']])
    assert len(e.observations[0]['items']) == 3  # Public EHR provenance is unchanged.


def test_dynamics_keeps_stale_anchor_times_and_ordered_actions_once():
    e = env()
    e.time = 12
    e.state['labs']['ph'].update(value=7.28, as_of_hours=12)
    e.collect(['ph'], sample_id='partial')
    ledger = [{'event_id': 'stop', 'at_hours': 8, 'operation': 'stop', 'target': 'oxygen', 'parameters': {}},
              {'event_id': 'start', 'at_hours': 0, 'operation': 'start', 'target': 'oxygen', 'parameters': {'fio2': .3}}]
    payload = dynamics_payload(e.belief_from_visible(), e.context, ledger, 24,
                               mode='planning', scenario=0, visible_history=e.observations)
    assert payload['current']['sampled_at_hours'] == {'pH': 12, 'PaCO2(mmHg)': 0, 'CRP(mg/L)': 0}
    assert [a['event_id'] for a in payload['actions']] == ['start', 'stop']
    assert payload['time_hours'] == 12 and payload['predict_at_hours'] == 24
    assert payload['current_kind'] == 'visible_observations'
    assert 'baseline' not in payload and 'exposure_ledger' not in payload and 'ordered_events' not in payload
    initial = dynamics_payload(env().state, e.context, [], 12, mode='execution', scenario=0, visible_history=())
    assert initial['history'] == []  # The initial current report is not sent twice.


def test_all_metrics_are_predicted_together_in_one_call_including_censor_and_null():
    e = env()
    for i in range(30):
        e.state['labs'][f'additional_{i}'] = {'key': f'additional_{i}', 'name': f'人工项目{i}',
            'unit': 'test-unit', 'value': i, 'as_of_hours': 0}
    calls = []
    class Client:
        def request(self, system, payload, schema, **kwargs):
            calls.append(payload)
            values = {label: 1 for label in payload['output_keys']}
            values.update({'pH': 7.28, 'PaCO2(mmHg)': 52, 'CRP(mg/L)': '<0.5', '人工项目0(test-unit)': None})
            result = schema.model_validate(values)
            kwargs['validator'](result)
            return result
    original = copy.deepcopy(e.state)
    result = LLMDynamics(Client()).transition(e.state, e.context, [], 12, branch_id='all', mode='execution')
    assert len(calls) == 1 and len(calls[0]['output_keys']) == 33
    assert len(result['labs']) == 33 and result['labs']['crp']['comparator'] == '<'
    assert result['labs']['crp']['bound'] == .5 and result['labs']['additional_0']['value'] is None
    assert result['labs']['ph']['as_of_hours'] == 12 and e.state == original
    assert labs_json(result['labs'])['CRP(mg/L)'] == '<0.5'


def test_duplicate_metric_names_are_rejected_before_losing_values():
    labs = case()['baseline']
    labs['duplicate'] = {**labs['ph'], 'name': 'pH'}
    with pytest.raises(ValueError): metric_labels(labs)


def test_locally_owned_branch_identity_and_state_version_are_not_echo_fields():
    e = env()
    t = SyntheticTransport()
    client = t.client(APIConfig(api_key='unit-test-token'))
    dynamics = LLMDynamics(client)
    try:
        dynamics.transition(e.state, e.context, [], 12, branch_id='a', mode='execution')
        dynamics.transition(e.state, e.context, [], 12, branch_id='b', mode='execution')
        e.state['version'] += 1
        dynamics.transition(e.state, e.context, [], 12, branch_id='b', mode='execution')
        assert len(t.calls) == 3
        assert all(not {'request_id', 'branch_id', 'state_version', 'response_schema'} & set(p) for _, p in t.calls)
    finally: client.close()


def test_changed_base_state_during_inference_cannot_commit_stale_prediction():
    e = env()
    class MutatingClient:
        def request(self, system, payload, schema, **kwargs):
            e.state['version'] += 1
            return schema.model_validate({'pH': 7.28, 'PaCO2(mmHg)': 52, 'CRP(mg/L)': .4})
    with pytest.raises(DynamicsError, match='基状态已改变'):
        LLMDynamics(MutatingClient()).transition(e.state, e.context, [], 12, branch_id='actual', mode='execution')
    assert e.state['labs']['ph']['value'] == 7.2 and e.state['clock_hours'] == 0


@pytest.mark.parametrize('content', [
    '{"pH":7.3,"pH":7.4,"PaCO2(mmHg)":42,"CRP(mg/L)":0.4}',
    '{"pH":NaN,"PaCO2(mmHg)":42,"CRP(mg/L)":0.4}',
    '{"pH":7.3,"PaCO2(mmHg)":42,"CRP(mg/L)":"<1e999"}',
    '[{"pH":7.3,"PaCO2(mmHg)":42,"CRP(mg/L)":0.4}]',
    '{"pH":7.3,"PaCO2(mmHg)":42,"CRP(mg/L)":0.4} extra text',
])
def test_flat_reply_strictly_rejects_duplicate_keys_nonfinite_and_extra_content(content):
    transport = SyntheticTransport()
    def handler(request):
        body = transport.handle(request).json()
        body['choices'][0]['message']['content'] = content
        return httpx.Response(200, json=body)
    sdk = OpenAI(base_url='https://example.invalid/v1', api_key='unit-test-token',
                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client = JSONClient(APIConfig(api_key='unit-test-token', repair_attempts=0), sdk)
    e = env()
    try:
        with pytest.raises(ModelResponseError):
            LLMDynamics(client).transition(e.state, e.context, [], 12, branch_id='x', mode='execution')
        assert e.state['clock_hours'] == 0
    finally: client.close()


def test_public_examples_use_runtime_builders_and_contain_no_workbook_or_secrets(tmp_path):
    examples = model_protocol_examples()
    assert examples['synthetic_example']
    policy = json.loads(examples['policy']['messages'][1]['content'])
    actual = json.loads(examples['dynamics_execution']['messages'][1]['content'])
    planning = json.loads(examples['dynamics_planning']['messages'][1]['content'])
    assert policy['visible_patient']['history'][0]['labs']['CRP(mg/L)'] == '<0.5'
    assert 'CRP(mg/L)' not in policy['visible_patient']['current']['labs']
    assert planning['current']['labs']['CRP(mg/L)'] == '<0.5'
    assert actual['current']['labs']['CRP(mg/L)'] == .4
    output = TransitionReply.model_validate(examples['dynamics_execution']['example_response'])
    assert set(output.root) == set(actual['output_keys'])
    with TestClient(create_app(tmp_path/'web.sqlite', dreamer_source_path=tmp_path/'missing.xlsx')) as client:
        response = client.get('/api/dreamer/model-protocol')
        assert response.status_code == 200 and response.json() == examples
        assert client.get('/api/dreamer/health').json()['protocol_version'] == 'compact-labs-1.0'
    text = json.dumps(examples)
    assert 'api_key' not in text and 'base_url' not in text
