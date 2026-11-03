"""Validate the running local Dreamer service using demo data, never an LLM call."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ehrworld.io import ROOT, output_path


def main():
    source_path = ROOT / '2024带孕周的出院小结汇总.xlsx'
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=30) as client:
        def get(path):
            response = client.get('/api/dreamer' + path)
            response.raise_for_status()
            return response.json()

        def wait(path):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                item = get(path)
                if item['status'] not in ('queued', 'running'):
                    return item
                time.sleep(.2)
            raise AssertionError('Demo generation did not finish within 90 seconds')

        health = get('/health')
        assert health['framework'] == 'EHR Dreamer'
        source = get('/source')
        cases = get('/cases?limit=100')['items']
        selected = next(c for c in cases if c['intake']['gestational_weeks'] is not None
                        and c['intake']['gestational_weeks'] < 35
                        and any(s in c['intake']['symptoms'] for s in ('气促', '呼吸急促', '呻吟', '呼吸困难')))
        response = client.post('/api/dreamer/generations', json={
            'case_id': selected['id'], 'backend': 'demo', 'max_operations': 96, 'seed': 42,
            'source_sha256': source['source_sha256']})
        assert response.status_code == 202
        job = wait('/generations/' + response.json()['id'])
        assert job['status'] == 'completed', job.get('error')
        record = job['record']
        assert 'private' not in job
        assert {e['phase'] for e in record['events']} == {'assessment', 'treatment', 'discharge'}
        known = set()
        for event in record['events']:
            assert set(event['evidence_ids']) <= known
            assert event['parent_order_id'] is None or event['parent_order_id'] in known
            known.add(event['event_id'])
            if event['event_type'] == 'treatment':
                assert event['value'] is None
                assert event['parameters']['exposure_model'] == 'documented_action_covariate'
        assert all(a['time_hours'] <= b['time_hours'] for a, b in zip(record['events'], record['events'][1:]))
        assert all(o['collected_at_hours'] <= o['time_hours'] for o in record['observations'])
        document = get('/generations/' + job['id'] + '/export')
        output_path('outputs/dreamer_ml/validation/patient.json').write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        batches = get('/batches')['items']
        batch = next((b for b in batches if b['status'] == 'completed' and b['request']['backend'] == 'demo' and b['patients'][0]['engine_version']==health['engine_version'] and b['patients'][0].get('learning',{}).get('model_id')==health['learning']['model_id']), None)
        if batch is None:
            response=client.post('/api/dreamer/batches',json={'backend':'demo','source_sha256':source['source_sha256']})
            assert response.status_code==202
            batch=wait('/batches/'+response.json()['id'])
        if batch:
            assert len(batch['patients']) == 10 and batch['completed_patients'] == 10
            assert batch['distribution'] == {'short': 3, 'medium': 5, 'long': 2}
            exported = get('/batches/' + batch['id'] + '/export')
            assert len(exported['patients']) == 10
            assert all(p['encounter']['complete_episode'] == (p['encounter']['terminal_status'] in ('discharged', 'transferred')) for p in exported['patients'])
            output_path('outputs/dreamer_ml/validation/batch.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
        after = hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert before == after == source['source_sha256']
        summary = {'framework': health['framework'], 'source_records': source['records'],
                   'eligible_cases': source['eligible_cases'], 'source_fields': source['fields'],
                   'source_sha256': before, 'source_unchanged': True, 'backend': 'demo', 'engine_version': health['engine_version'],
                   'patient': {'id': job['id'], 'case_id': job['case']['id'], 'operations': job['operations'],
                               'time_hours': job['time_hours'], 'terminal_status': job['terminal_status'],
                               'initial_assessments':sum(o.get('session_type')=='admission' for o in record['orders']),'followup_rounds':record['workflow']['review_count'], 'events': len(record['events']), 'reports': len(record['observations']),
                               'treatments': sum(e['event_type'] == 'treatment' for e in record['events'])},
                   'batch': {'id': batch['id'], 'tasks_completed': batch['completed_patients'],
                             'complete_episodes': batch['completed_episodes'], 'complete_trajectories':batch['completed_trajectories'], 'failed': batch['failed_patients']} if batch else None}
        output_path('outputs/dreamer_ml/validation/runtime.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=True))


if __name__ == '__main__':
    main()
