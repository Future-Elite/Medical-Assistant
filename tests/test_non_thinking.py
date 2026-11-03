from __future__ import annotations

import json

import httpx
from openai import OpenAI
from pydantic import BaseModel
from fastapi.testclient import TestClient

from ehrworld.llm import APIConfig, JSONClient
from ehrworld.web.app import create_app


class Probe(BaseModel):
    ok: bool


def test_sdk_wire_requests_disable_thinking_including_json_repair():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        content = 'invalid JSON' if len(bodies)==1 else '{"ok": true}'
        return httpx.Response(200, json={
            'id':'test', 'object':'chat.completion', 'created':0, 'model':'Qwen3.8-27B',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':content}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
    with OpenAI(base_url='https://example.invalid/v1',api_key='fake-test-key',
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        client = JSONClient(APIConfig(api_key='fake-test-key'), sdk)
        assert client.request('Return JSON only',{'test':True},Probe,operation='test').ok
        assert len(bodies)==2
        for body in bodies:
            assert body['model']=='Qwen3.8-27B'
            assert body['enable_thinking'] is False
            assert body['chat_template_kwargs']['enable_thinking'] is False
        assert client.statistics()['enable_thinking'] is False


def test_health_reports_active_model_and_disabled_thinking(tmp_path,monkeypatch):
    monkeypatch.setenv('EHRWORLD_API_KEY','fake-test-key')
    monkeypatch.setenv('EHRWORLD_MODEL','Qwen3.8-27B')
    with TestClient(create_app(tmp_path/'web.sqlite')) as client:
        health=client.get('/api/health').json()
        assert health['model']=='Qwen3.8-27B'
        assert health['enable_thinking'] is False
