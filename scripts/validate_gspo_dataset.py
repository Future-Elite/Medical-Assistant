"""Independent streaming validation of generated CSV/Parquet and chronology."""
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrdreamer.rl_data.dataset import project_path
from ehrdreamer.rl_data.extract import REPORT_HEADER
from ehrdreamer.rl_data.verl_adapter import _load_context
from ehrdreamer.dual.references import score

csv.field_size_limit(2**31-1)


def validate(directory):
    directory=project_path(directory);manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    groups={};eligible=set();counts=Counter();max_prompt=0
    with (directory/'policy_gspo.csv').open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            pid=row['patient_id'];assert pid not in groups;groups[pid]=row['split'];counts['policy_rows']+=1
            messages=json.loads(row['prompt']);assert [m['role'] for m in messages]==['system','user']
            user=json.loads(messages[1]['content']);anchor=int(row['anchor_day'])
            assert row['output']=='' and '出院诊断' not in user and 'reward' not in user
            history=user.get('history',user.get('clinical_history',{}).get('lab_history',[]))
            treatments=user.get('treatments',user.get('clinical_history',{}).get('treatment_history',[]))
            assert all(h['day']<anchor for h in history)
            assert all(t['day']<=anchor and 'end_day' not in t for t in treatments)
            if 'current' in user:assert all(not isinstance(v,dict) for v in user['current']['labs'].values())
            context=_load_context(json.loads(row['reward_model'])['ground_truth'])
            assert context['future_real_records_in_context'] is False
            if row['rl_eligible']=='true':
                eligible.add(pid);assert score(context['initial_labs'],context['reference_rules'])['complete']
            max_prompt=max(max_prompt,len(row['prompt']))
    assert len(groups)==manifest['patients'] and len(eligible)==manifest['rl_eligible_patients']
    auxiliary_dir=directory if (directory/'daily_labs.csv').exists() else project_path(manifest['source_dataset_dir'])
    for filename,key in [('daily_labs.csv','labs'),('treatment_events.csv','actions'),('dynamics_transitions.csv','transitions')]:
        with (auxiliary_dir/filename).open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                assert groups[row['patient_id']]==row['split'];counts[key]+=1
                if key=='labs':assert not REPORT_HEADER.search(row['name'])
                if key=='transitions':
                    payload=json.loads(row['input']);target_day=int(row['to_day']);current_day=int(row['from_day'])
                    assert target_day>current_day and all(v['observed_day']<=current_day for v in payload['last_known_labs'].values())
                    assert all(t.get('end_day') is None or t['end_day']<=target_day for t in payload['documented_treatment_events'])
                    assert row['causal_effect_identified']=='false'
    import pyarrow.parquet as pq
    exported=set()
    for split in ('train','validation','test'):
        table=pq.read_table(directory/'verl'/f'{split}.parquet')
        assert set(table.column_names)=={'data_source','prompt','ability','reward_model','extra_info'}
        for item in table.select(['extra_info','prompt']).to_pylist():
            info=item['extra_info'];assert info['split']==split and groups[info['patient_id']]==split
            assert info['patient_id'] not in exported and isinstance(item['prompt'],list);exported.add(info['patient_id'])
        counts['parquet_'+split]=table.num_rows
    assert exported==eligible
    source=project_path('2024带孕周的出院小结汇总.xlsx')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==manifest['source_sha256']
    assert counts['labs']==manifest['lab_observations'] and counts['transitions']==manifest['observational_transitions']
    result={'passed':True,'counts':dict(counts),'max_prompt_characters_in_master_csv':max_prompt,
            'source_unchanged':True,'patient_splits_disjoint':True,'nested_parquet_verified':True,
            'calendar_cutoff_verified':True,'clinical_sampling_and_release_times_available':False,
            'optimal_labels_or_counterfactual_outcomes_proven':False,'training_executed':False}
    (directory/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',default='data/gspo_2024')
    print(json.dumps(validate(parser.parse_args().directory),ensure_ascii=False,indent=2))
