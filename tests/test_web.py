from __future__ import annotations

import csv
import io
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from ehrworld.llm import ModelResponseError
from ehrworld.web.app import create_app
from ehrworld.web.models import GenerationRequest
from ehrworld.web.scenarios import DemoPredictor
from ehrworld.web.store import JobStore, now


def wait_job(client, identifier):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/generations/{identifier}")
        assert response.status_code == 200
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.005)
    pytest.fail("Generation failed to finish within the test deadline")


@pytest.mark.parametrize("scenario", ["diabetes", "pneumonia"])
def test_ten_step_generation_persistence_exports_and_history(tmp_path, scenario):
    path = tmp_path / "web.sqlite"
    with TestClient(create_app(path)) as client:
        assert client.get('/api/health').json()["steps"] == 10
        catalog = client.get('/api/scenarios').json()
        scenarios = catalog["items"] + catalog["legacy_items"]
        patient = next(s for s in scenarios if s["id"] == scenario)["default_patient"]
        response = client.post('/api/generations', json={"backend": "demo", "scenario_id": scenario})
        assert response.status_code == 202
        identifier = response.json()["id"]
        job = wait_job(client, identifier)
        assert job["status"] == "completed"
        assert job["request"]["patient"] == patient
        assert job["completed_steps"] == 10
        assert len(job["trajectory"]["steps"]) == 10
        assert len({s["timestamp"] for s in job["trajectory"]["steps"]}) == 10
        events = [e for s in job["trajectory"]["steps"] for e in s["events"]]
        assert len(events) == 63
        assert len({e["action"]["action_id"] for e in events}) == 63
        assert all(e["value"] is None for e in events if e["action"]["kind"] == "intervention")
        document = client.get(f'/api/generations/{identifier}/export?format=json')
        assert document.json()["step_count"] == 10
        assert document.json()["backend"] == "demo"
        assert document.json()["synthetic"] is True
        assert 'attachment' in document.headers['content-disposition']
        csv_result = client.get(f'/api/generations/{identifier}/export?format=csv')
        assert len(list(csv.reader(io.StringIO(csv_result.content.decode('utf-8-sig'))))) == 64
        recent = client.get('/api/generations').json()['items']
        assert recent[0]['id'] == identifier
        assert 'trajectory' not in recent[0]
    with TestClient(create_app(path)) as restarted:
        assert restarted.get(f'/api/generations/{identifier}').json()['status'] == 'completed'


def test_web_uses_complete_generated_history_in_every_step(tmp_path):
    calls = []
    def factory(body):
        class Spy(DemoPredictor):
            name = 'api:injected-test-predictor'
            def predict(self, state, actions):
                calls.append((state, actions))
                return super().predict(state, actions)
        return Spy(body), None
    with TestClient(create_app(tmp_path / 'web.sqlite', factory)) as client:
        identifier = client.post('/api/generations', json={"backend": "api", "scenario_id": "diabetes"}).json()['id']
        job = wait_job(client, identifier)
        assert job['status'] == 'completed'
    assert [len(state.history) for state, _ in calls] == list(range(10))
    assert all(all(s.timestamp < state.timestamp for s in state.history) for state, _ in calls)
    assert [len(actions) for _, actions in calls] == [7,6,6,7,6,6,7,6,6,6]


def test_remote_failure_preserves_partial_progress_without_fake_fallback(tmp_path):
    def factory(body):
        class Broken(DemoPredictor):
            name = 'api:broken-test'
            def predict(self, state, actions):
                if len(state.history) == 3:
                    raise ModelResponseError('HTTP 403 with sensitive secret sk-test-do-not-echo')
                return super().predict(state, actions)
        return Broken(body), None
    with TestClient(create_app(tmp_path / 'web.sqlite', factory)) as client:
        identifier = client.post('/api/generations', json={"backend": "api"}).json()['id']
        result = wait_job(client, identifier)
        assert result['status'] == 'failed'
        assert result['completed_steps'] == 3
        assert result['error']['code'] == 'upstream_forbidden'
        assert 'sk-test-do-not-echo' not in str(result)
        assert result['request']['backend'] == 'api'
        assert client.get(f'/api/generations/{identifier}/export').status_code == 409
        retry = client.post(f'/api/generations/{identifier}/retry')
        assert retry.status_code == 202
        assert retry.json()['id'] != identifier


@pytest.mark.parametrize('body', [{'steps':0},{'steps':11},{'steps':True},{'steps':2.5},{'model':'unapproved-model'},{'interval_hours':0},{'patient':{'age':17}},{'backend':'secret'},{'scenario_id':'unknown'},{'start_time':'not-a-date'}])
def test_invalid_requests_are_rejected_without_echoing_input(tmp_path, body):
    with TestClient(create_app(tmp_path / 'web.sqlite')) as client:
        response = client.post('/api/generations', json=body)
        assert response.status_code == 422
        assert 'input' not in response.text


def test_plan_preview_missing_ids_and_recovery(tmp_path):
    path = tmp_path / 'web.sqlite'
    with TestClient(create_app(path)) as client:
        plan = client.post('/api/plans/preview', json={'interval_hours': 3}).json()['steps']
        assert len(plan) == 10
        assert 'value' not in plan[0]['actions'][0]
        assert client.get(f'/api/generations/{uuid.uuid4()}').status_code == 404
        assert client.get('/api/generations/not-a-uuid').status_code == 422
    identifier = str(uuid.uuid4())
    store = JobStore(path)
    store.save({'id':identifier,'created_at':now(),'status':'running'})
    with TestClient(create_app(path)) as client:
        interrupted = client.get(f'/api/generations/{identifier}').json()
        assert interrupted['status'] == 'failed'
        assert interrupted['error']['code'] == 'server_restarted'


def test_queue_capacity_and_retry_guard(tmp_path):
    gate = threading.Event()
    def factory(body):
        gate.wait(timeout=5)
        return DemoPredictor(body), None
    with TestClient(create_app(tmp_path / 'web.sqlite', factory)) as client:
        try:
            responses = [client.post('/api/generations', json={'backend':'demo'}) for _ in range(6)]
            assert all(r.status_code == 202 for r in responses)
            assert client.post('/api/generations', json={'backend':'demo'}).status_code == 429
            identifier = responses[0].json()['id']
            assert client.post(f'/api/generations/{identifier}/retry').status_code == 409
        finally:
            gate.set()


def test_csv_generated_text_cannot_be_a_formula(tmp_path):
    def factory(body):
        class Formula(DemoPredictor):
            def predict(self, state, actions):
                result = super().predict(state, actions)
                for action in actions:
                    if action.concept == 'web:summary':
                        result[action.action_id] = '=1+1'
                return result
        return Formula(body), None
    with TestClient(create_app(tmp_path / 'web.sqlite', factory)) as client:
        identifier = client.post('/api/generations', json={'backend':'demo'}).json()['id']
        assert wait_job(client, identifier)['status'] == 'completed'
        response = client.get(f'/api/generations/{identifier}/export?format=csv')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        summaries = [r for r in rows[1:] if r[6] == 'web:summary']
        assert len(summaries) == 10
        assert all(r[7] == "'=1+1" for r in summaries)
