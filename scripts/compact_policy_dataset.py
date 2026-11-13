"""Replace verbose GSPO model prompts with one JSON per report; never train."""
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrworld.io import ROOT
from ehrdreamer.rl_data.compact import compact_policy_prompt, POLICY_SYSTEM
from ehrdreamer.rl_data.verl_adapter import export_parquet

csv.field_size_limit(2**31-1)


def compact(source_directory='data/gspo_2024',output_directory='data/gspo_compact_2024'):
    source_directory=(ROOT/source_directory).resolve();directory=(ROOT/output_directory).resolve()
    if not directory.is_relative_to(ROOT) or not source_directory.is_relative_to(ROOT):raise ValueError('Project paths only')
    directory.mkdir(parents=True,exist_ok=True);(directory/'environments').mkdir(exist_ok=True)
    target=directory/'policy_gspo.csv';temp=directory/'policy_gspo.compact.tmp'
    count=before=after=0;eligible={k:0 for k in ('train','validation','test')};exclusions=Counter()
    streams={k:(directory/f'{k}.csv').open('w',encoding='utf-8-sig',newline='') for k in eligible}
    try:
        with (source_directory/'policy_gspo.csv').open(encoding='utf-8-sig',newline='') as src,temp.open('w',encoding='utf-8-sig',newline='') as dst:
            reader=csv.DictReader(src);fields=reader.fieldnames
            writer=csv.DictWriter(dst,fieldnames=fields);writer.writeheader()
            split_writers={k:csv.DictWriter(v,fieldnames=fields) for k,v in streams.items()}
            for w in split_writers.values():w.writeheader()
            for row in reader:
                messages=json.loads(row['prompt']);user=json.loads(messages[1]['content'])
                before+=len(row['prompt'])
                if 'clinical_history' not in user:
                    compacted=messages;evidence={}
                else:compacted,evidence=compact_policy_prompt(user)
                row['prompt']=json.dumps(compacted,ensure_ascii=False,separators=(',',':'))
                row['system_prompt']=POLICY_SYSTEM
                compact_user=json.loads(compacted[1]['content'])
                row['clinical_history']=json.dumps({'history':compact_user.get('history',[]),'treatments':compact_user.get('treatments',[])},ensure_ascii=False,separators=(',',':'))
                row['current_labs']=json.dumps(compact_user.get('current',{}).get('labs',{}),ensure_ascii=False,separators=(',',':'))
                reasons=json.loads(row['exclusion_reasons'])
                reasons=[r for r in reasons if r!='prompt_exceeds_configured_character_budget_no_truncation']
                if len(row['prompt'])>60000:reasons.append('prompt_exceeds_configured_character_budget_no_truncation')
                row['rl_eligible']='false' if reasons else 'true';row['exclusion_reasons']=json.dumps(reasons)
                exclusions.update(reasons)
                reward=json.loads(row['reward_model']);spec=json.loads(reward['ground_truth'])
                path=(ROOT/spec['context_path']).resolve()
                if not path.is_relative_to(source_directory):raise ValueError('Unexpected context path')
                context=json.loads(path.read_text(encoding='utf-8'))
                if evidence:
                    context['visible_evidence_ids']=sorted(set(evidence.values()))
                    context['compact_evidence_map']=evidence
                context['prompt_version']='compact-reports-2.0'
                context['guideline_rule_ids']=list(compact_user.get('guidelines',{}))
                encoded=json.dumps(context,ensure_ascii=False,separators=(',',':')).encode()
                new_path=directory/'environments'/path.name
                new_path.write_bytes(encoded);spec['context_path']=new_path.relative_to(ROOT).as_posix()
                spec['context_sha256']=hashlib.sha256(encoded).hexdigest()
                reward['ground_truth']=json.dumps(spec,ensure_ascii=False,separators=(',',':'))
                row['reward_model']=json.dumps(reward,ensure_ascii=False,separators=(',',':'))
                writer.writerow(row)
                if row['rl_eligible']=='true':split_writers[row['split']].writerow(row);eligible[row['split']]+=1
                after+=len(row['prompt']);count+=1
    finally:
        for stream in streams.values():stream.close()
    temp.replace(target)
    write_training_csv(directory)
    splits=export_parquet(target,directory/'verl')
    manifest_path=directory/'manifest.json';manifest=json.loads((source_directory/'manifest.json').read_text(encoding='utf-8'))
    manifest.update(rl_eligible_patients=sum(eligible.values()),eligible_split_counts=eligible,parquet_counts=splits,
                    exclusion_reasons=dict(exclusions),
                    source_dataset_dir=source_directory.relative_to(ROOT).as_posix(),
                    policy_prompt_format='compact-reports-2.0',compact_prompt_stats={'rows':count,'before_chars':before,'after_chars':after})
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'rows':count,'eligible':sum(eligible.values()),'splits':eligible,'before_chars':before,'after_chars':after}


def write_training_csv(directory):
    """Final training CSV contains only the five fields consumed by verl."""
    directory=Path(directory)
    fields=['data_source','prompt','ability','reward_model','extra_info'];count=0
    with (directory/'policy_gspo.csv').open(encoding='utf-8-sig',newline='') as src, (directory/'policy_gspo_compact.csv').open('w',encoding='utf-8-sig',newline='') as dst:
        writer=csv.DictWriter(dst,fieldnames=fields);writer.writeheader()
        for row in csv.DictReader(src):
            if row['rl_eligible']=='true':writer.writerow({k:row[k] for k in fields});count+=1
    return count


if __name__=='__main__':print(json.dumps(compact(),ensure_ascii=False,indent=2))
