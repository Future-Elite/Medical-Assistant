from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from ehrworld.llm import APIConfig, JSONClient, ModelResponseError
from ehrworld.schema import Action, PatientState
from ehrworld.simulator import APIPredictor, PatientSimulator, PersistencePredictor, PredictionSet, rollout


class Spy:
    name = "test-spy"

    def __init__(self):
        self.requests = []

    def predict(self, state, actions):
        self.requests.append((state, actions))
        return {a.action_id: 42.0 if a.value_type == "number" else "negative"
                for a in actions if a.kind == "inquiry"}


class FakeTransport:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.texts)), finish_reason="stop")])

    def close(self):
        pass


def test_full_and_next_use_different_histories_and_never_current_truth(episode):
    full, next_step = Spy(), Spy()
    result = rollout(episode, full, "full")
    rollout(episode, next_step, "next")
    assert full.requests[0][0].history == ()
    assert next_step.requests[0][0].history == ()
    assert full.requests[2][0].history[0].events[0].value == 42
    assert next_step.requests[2][0].history[0] == episode.steps[0]
    for index, (state, actions) in enumerate(full.requests):
        assert all(step.timestamp < state.timestamp for step in state.history)
        assert not any(hasattr(a, "value") for a in actions)
        assert state.timestamp == episode.steps[index].timestamp
    assert result.steps[1].events[0].value is None


def test_concurrent_actions_are_canonical_and_id_matched(episode):
    actions = tuple(e.action for e in episode.steps[0].events)
    a, b = Spy(), Spy()
    left = PatientSimulator(episode.context, episode.admitted_at, a).step(episode.admitted_at, actions)
    right = PatientSimulator(episode.context, episode.admitted_at, b).step(episode.admitted_at, actions[::-1])
    assert left == right
    assert a.requests[0][1] == b.requests[0][1]


def test_failure_is_atomic_and_fork_is_independent(episode):
    class Bad:
        name = "bad"
        def predict(self, state, actions):
            return {"wrong": 3.0}
    simulator = PatientSimulator(episode.context, episode.admitted_at, Bad())
    before = simulator.state.model_dump_json()
    with pytest.raises(ValueError):
        simulator.step(episode.admitted_at, tuple(e.action for e in episode.steps[0].events))
    assert simulator.state.model_dump_json() == before
    simulator.predictor = Spy()
    fork = simulator.fork()
    fork.step(episode.admitted_at, tuple(e.action for e in episode.steps[0].events))
    assert not simulator.state.history
    assert len(fork.state.history) == 1
    restored = PatientSimulator.restore(PatientState.model_validate_json(fork.state.model_dump_json()), Spy())
    assert restored.state == fork.state


def test_real_adapter_gets_all_actions_and_no_reference_or_future_values(episode):
    actions = tuple(e.action for e in episode.steps[0].events)
    medicine = episode.steps[1].events[0].action
    text = json.dumps({"outcomes": [{"action_id": a.action_id, "value": 1.0, "unit": a.unit} for a in actions[::-1]]})
    transport = FakeTransport([text])
    client = JSONClient(APIConfig(api_key="test"), transport)
    backend = APIPredictor(client)
    simulator = PatientSimulator(episode.context, episode.admitted_at, backend)
    result = simulator.step(episode.admitted_at, (*actions, medicine))
    payload = json.loads(transport.requests[0]["messages"][1]["content"])
    assert len(payload["concurrent_actions"]) == 3
    assert payload["history"] == []
    assert "reference_low" not in json.dumps(payload)
    assert "240" not in json.dumps(payload)
    assert next(e for e in result.events if e.action.kind == "intervention").value is None
    calls = len(transport.requests)
    simulator.step(episode.admitted_at + timedelta(hours=1), (medicine.model_copy(update={"action_id": "new-med"}),))
    assert len(transport.requests) == calls  # deterministic empty outcome, no API call


def test_json_repair_rejects_missing_duplicate_ids_and_wrong_units(episode):
    actions = tuple(e.action for e in episode.steps[0].events)
    good = {"outcomes": [{"action_id": a.action_id, "value": 1.0, "unit": a.unit} for a in actions]}
    wrong = {"outcomes": [{"action_id": a.action_id, "value": 1.0, "unit": "WRONG"} for a in actions]}
    transport = FakeTransport([json.dumps(wrong), "```json\n" + json.dumps(good) + "\n```"])
    backend = APIPredictor(JSONClient(APIConfig(api_key="test"), transport))
    simulator = PatientSimulator(episode.context, episode.admitted_at, backend)
    simulator.step(episode.admitted_at, actions)
    assert len(transport.requests) == 2
    transport = FakeTransport(['{"outcomes": []}', '{"outcomes": []}'])
    simulator = PatientSimulator(episode.context, episode.admitted_at, APIPredictor(JSONClient(APIConfig(api_key="test"), transport)))
    with pytest.raises(ModelResponseError):
        simulator.step(episode.admitted_at, actions)
    assert not simulator.state.history


@pytest.mark.parametrize("value", [True, False, None, float("nan"), float("inf"), "123"])
def test_invalid_numeric_output_not_coerced(value, episode):
    action = episode.steps[0].events[0].action
    from ehrworld.schema import validate_value
    with pytest.raises(ValueError):
        result = PredictionSet.model_validate({"outcomes": [{"action_id": action.action_id, "value": value, "unit": action.unit}]})
        validate_value(action, result.outcomes[0].value)


def test_time_cannot_move_backwards_or_repeat(episode):
    sim = PatientSimulator(episode.context, episode.admitted_at, Spy())
    actions = tuple(e.action for e in episode.steps[0].events)
    sim.step(episode.admitted_at, actions)
    with pytest.raises(ValueError):
        sim.step(episode.admitted_at, actions)


def test_empty_api_choices_are_repaired_without_partial_state(episode):
    class Empty(FakeTransport):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            return SimpleNamespace(usage=None, choices=[])
    transport = Empty([])
    simulator = PatientSimulator(episode.context, episode.admitted_at, APIPredictor(JSONClient(APIConfig(api_key="test"), transport)))
    with pytest.raises(ModelResponseError):
        simulator.step(episode.admitted_at, tuple(e.action for e in episode.steps[0].events))
    assert not simulator.state.history
    assert len(transport.requests) == 2


def test_history_size_guard_never_silently_truncates(episode):
    from ehrworld.llm import ContextLimitError
    transport = FakeTransport([])
    simulator = PatientSimulator(episode.context, episode.admitted_at,
                                 APIPredictor(JSONClient(APIConfig(api_key="test", max_input_chars=1), transport)))
    with pytest.raises(ContextLimitError):
        simulator.step(episode.admitted_at, tuple(e.action for e in episode.steps[0].events))
    assert not simulator.state.history
    assert not transport.requests


def test_offline_baseline_handles_censored_then_numeric_measurement(episode):
    from ehrworld.schema import Event, Step
    old_action = episode.steps[0].events[0].action.model_copy(update={"value_type": "label"})
    history = (Step(timestamp=episode.admitted_at, events=(Event(action=old_action, value="<10"),)),)
    state = PatientState(timestamp=episode.admitted_at + timedelta(hours=1), context=episode.context, history=history)
    action = old_action.model_copy(update={"value_type": "number", "action_id": "fresh"})
    assert PersistencePredictor().predict(state, (action,))["fresh"] == 0
