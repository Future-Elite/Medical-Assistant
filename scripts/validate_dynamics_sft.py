"""Validate four compact SFT CSVs against source workbook observations."""
import csv
import hashlib
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrworld.io import ROOT
from ehrdreamer.source import FIELDS,value_text
from ehrdreamer.rl_data.dataset import dumps
from ehrdreamer.rl_data.extract import labs_from_row
from ehrdreamer.rl_data.compact import report_json
from ehrdreamer.rl_data.dynamics_sft import FILES,_state

csv.field_size_limit(2**31-1)


def validate(directory='data/dynamics_sft_2024_v2'):
    directory=(ROOT/directory).resolve()
    if not directory.is_relative_to(ROOT):raise ValueError('Project paths only')
    counts=Counter();ids={};parents=[];patient_split={};checks=defaultdict(list)
    for kind,name in FILES.items():
        with (directory/name).open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                metadata=json.loads(row['provenance']);payload=json.loads(row['input']);output=json.loads(row['output']);messages=json.loads(row['messages'])
                assert row['sample_id'] not in ids and row['task_type']==kind
                assert row['split']==patient_split.setdefault(row['patient_id'],row['split'])
                n=int(row['n_steps']);numbers=metadata['documented_primary_treatment_counts']
                assert len(numbers)==n and list(payload['actions'])==[str(i) for i in range(1,n+1)]
                assert all(len(payload['actions'][str(i+1)])==numbers[i] for i in range(n))
                if kind=='single_step':assert n==1 and numbers==[1]
                elif kind=='combination':assert n==1 and numbers[0]>=2
                elif kind=='multi_step':assert n>=2 and max(numbers)==1
                else:assert n>=2 and min(numbers)==1 and max(numbers)>=2
                assert set(payload)<={'patient','labs','actions','history','past_treatments','support'}
                assert all(h['day']<0 for h in payload.get('history',[]))
                assert all(not isinstance(v,(dict,list)) for v in payload['labs'].values())
                reports=[output] if n==1 else list(output.values())
                if n>1:assert list(output)==[f'第{i}天' for i in range(1,n+1)]
                assert all(report and all(not isinstance(v,(dict,list)) for v in report.values()) for report in reports)
                assert [m['role'] for m in messages]==['system','user','assistant']
                assert messages[1]['content']==row['input'] and messages[2]['content']==row['output']
                assert len(messages[0]['content'])<200 and 'source_span' not in row['input']
                baseline=metadata['baseline_day'];days=metadata['target_days']
                assert days==list(range(baseline+1,baseline+n+1))
                ids[row['sample_id']]={'patient':row['patient_id'],'split':row['split'],'first':reports[0],
                                       'initial':payload['labs'],'actions':payload['actions']['1']}
                if row['parent_ids']!='[]':parents.append((row['sample_id'],json.loads(row['parent_ids'])))
                checks[metadata['source_row']].append((baseline,payload['labs'],days,reports));counts[kind]+=1
    for sid,references in parents:
        for parent in references:
            assert parent in ids
            assert ids[sid]==ids[parent]
    wb=openpyxl.load_workbook(ROOT/'2024带孕周的出院小结汇总.xlsx',read_only=True,data_only=True)
    source_reports=0
    try:
        it=wb.worksheets[0].iter_rows(values_only=True);headers=list(next(it))
        for row,values in enumerate(it,2):
            if row not in checks:continue
            raw={str(k):value_text(v) for k,v in zip(headers,values) if k in FIELDS}
            labs,_=labs_from_row(raw,row);daily=defaultdict(list)
            for item in labs:daily[item['day']].append(item)
            reports={day:report_json(_state(items)) for day,items in daily.items()}
            for baseline,initial,days,targets in checks[row]:
                assert initial==reports[baseline]
                for day,target in zip(days,targets):assert target==reports[day];source_reports+=1
    finally:wb.close()
    auditdir=ROOT/'outputs/dynamics_sft_v2' if directory==ROOT/'data/dynamics_sft_2024_v2' else directory.with_name(directory.name+'-audit')
    manifest=json.loads((auditdir/'manifest.json').read_text(encoding='utf-8'))
    assert dict(counts)==manifest['counts']
    assert hashlib.sha256((ROOT/'2024带孕周的出院小结汇总.xlsx').read_bytes()).hexdigest()==manifest['source_sha256']
    result={'passed':True,'counts':dict(counts),'first_step_samples_verified':len(parents),
            'source_reports_crosschecked':source_reports,'every_report_is_one_flat_json':True,
            'no_interpolated_days':True,'split_groups_disjoint':True,'source_unchanged':True,'training_executed':False}
    (auditdir/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':print(json.dumps(validate(),ensure_ascii=False,indent=2))
