"""verl CSV/Parquet and custom reward bridge. No rollout provider is enabled by default."""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError
from ehrworld.io import ROOT
from .contracts import PolicyResponse
from .reward import RewardConfig, RewardUnavailable, score_trajectory

csv.field_size_limit(min(sys.maxsize,2**31-1))
_rollout_provider=None


def configure_rollout(provider):
    """For trusted worker initialization/tests; never called with model output."""
    global _rollout_provider
    _rollout_provider=provider


def _load_context(ground_truth):
    spec=json.loads(ground_truth) if isinstance(ground_truth,str) else ground_truth
    path=(ROOT/spec['context_path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():raise RewardUnavailable('Reward context must exist inside the project')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=spec['context_sha256']:raise RewardUnavailable('Reward context hash changed')
    context=json.loads(data)
    if context['patient_id']!=spec['patient_id']:raise RewardUnavailable('Reward context patient mismatch')
    return context


def validate_response(response,context):
    evidence=set(context['visible_evidence_ids']);guides=set(context['guideline_rule_ids'])
    if not set(response.monitoring_metric_keys)<=set(context['known_metric_keys']):raise ValueError('Unknown monitoring metric')
    if not set(context['reference_rules'])<=set(response.monitoring_metric_keys):raise ValueError('Cannot hide a scored metric')
    active=set(context['known_active_treatments'])
    for action in response.actions:
        if action.at_hours>context['reward_config']['horizon_hours'] or action.at_hours+action.reassess_after_hours>context['reward_config']['horizon_hours']:
            raise ValueError('Action/review exceeds common horizon')
        if not set(action.evidence_ids)<=evidence or not set(action.guideline_rule_ids)<=guides:raise ValueError('Unknown evidence or guideline')
        if action.operation=='start':
            if action.target in active:raise ValueError('Duplicate active treatment')
            active.add(action.target)
        elif action.operation in ('adjust','continue','stop'):
            if action.target not in active:raise ValueError('Unknown treatment execution cannot be assumed')
            if action.operation=='stop':active.remove(action.target)
        if len(active & {'oxygen','cpap','nippv','noninvasive_support','respiratory_support_unspecified','invasive_ventilation'})>1:raise ValueError('Conflicting respiratory support orders')


def compute_score(data_source,solution_str,ground_truth,extra_info=None):
    """Official verl custom reward signature. No exact match to a doctor's chart.

    The trusted provider must independently roll out the candidate to 24/48/72h
    and return snapshots + a clinical-safety gate. Model-supplied lab values are
    forbidden by the response contract and are never used for reward.
    """
    if data_source!='ehrdreamer/neonatal_gspo_v1':raise RewardUnavailable('Unknown data source')
    context=_load_context(ground_truth)
    try:
        response=PolicyResponse.model_validate_json(solution_str)
        validate_response(response,context)
    except (ValueError,ValidationError):return -1.
    provider=_rollout_provider
    if provider is None:
        configured=os.environ.get('EHR_GSPO_ROLLOUT_PROVIDER','')
        if not configured:raise RewardUnavailable('No independent rollout provider configured; training is not enabled')
        module,name=configured.rsplit(':',1);provider=getattr(importlib.import_module(module),name)
    # The provider is responsible for freezing dynamics/checkpoints and policy
    # continuation across each group; real future chart values are not substitutes.
    rollout=provider(context,response.model_dump(mode='json'))
    return score_trajectory(context['initial_labs'],context['reference_rules'],rollout['snapshots'],
                            safety_passed=rollout.get('safety_passed'),
                            config=RewardConfig(**context['reward_config']))['score']


def export_parquet(csv_path,output_dir):
    """Decode JSON columns to nested Arrow types; never call a trainer/tokenizer."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    csv_path,output_dir=Path(csv_path).resolve(),Path(output_dir).resolve()
    if not csv_path.is_relative_to(ROOT) or not output_dir.is_relative_to(ROOT):raise ValueError('Files must remain inside project')
    output_dir.mkdir(parents=True,exist_ok=True)
    by_split={key:[] for key in ('train','validation','test')}
    with csv_path.open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            if row['rl_eligible']!='true':continue
            by_split[row['split']].append({'data_source':row['data_source'],'prompt':json.loads(row['prompt']),
                'ability':row['ability'],'reward_model':json.loads(row['reward_model']),'extra_info':json.loads(row['extra_info'])})
    # Explicit schema also permits empty partitions without a schema mismatch.
    schema=pa.schema([('data_source',pa.string()),('prompt',pa.list_(pa.struct([('role',pa.string()),('content',pa.string())]))),
        ('ability',pa.string()),('reward_model',pa.struct([('style',pa.string()),('ground_truth',pa.string())])),
        ('extra_info',pa.struct([('patient_id',pa.string()),('episode_id',pa.string()),('split',pa.string()),('anchor_day',pa.int64()),('data_version',pa.string())]))])
    for split,rows in by_split.items():pq.write_table(pa.Table.from_pylist(rows,schema=schema),output_dir/f'{split}.parquet')
    return {key:len(value) for key,value in by_split.items()}
