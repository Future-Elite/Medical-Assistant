import csv
import json
from pathlib import Path

import openpyxl

from ehrdreamer.source import FIELDS
from ehrdreamer.rl_data.compact import report_json
from ehrdreamer.rl_data.dynamics_sft import build_sft, detailed_treatments, episode_samples
from ehrdreamer.rl_data.extract import labs_from_row


def raw_case(gaps=False):
    days=[0,2,3,4] if gaps else range(5)
    reports=''.join(f'2024.01.{d+1:02} pH:{7.11+d*.01:.2f},PaCO2:{60-d}mmHg,CRP:<0.50mg/L,' for d in days)
    return {'性别':'男','住院号':'ARTIFICIAL_ID','入院时间':'2024-01-01','出院时间':'2024-01-06','孕周':'32',
            '入院时主要症状及体征':'患儿初生。母孕32周。主诉：气促。',
            '实验室检查及主要会诊':reports,'诊疗过程':'头孢呋辛抗感染（1.1-1.5）。CPAP支持（1.1-1.1）。'}


def make_samples(raw):
    labs,_=labs_from_row(raw,2);therapies,_=detailed_treatments(raw,2)
    return episode_samples('P-test','E-test','train',{'sex':'男','gestational_weeks':32},labs,therapies,2)


def test_four_categories_and_first_step_lineage_without_duplicate_samples():
    samples=make_samples(raw_case())
    assert {s['task_type'] for s in samples}=={'single_step','multi_step','combination','mixed'}
    assert len({s['sample_id'] for s in samples})==len(samples)
    parent=next(s for s in samples if s['task_type']=='mixed')
    first=next(s for s in samples if parent['sample_id'] in s['parent_ids'])
    assert first['task_type']=='combination'
    assert json.loads(first['output'])==json.loads(parent['output'])['第1天']
    assert first['split']==parent['split']=='train'


def test_compact_model_messages_and_no_intermediate_future_labs_in_input():
    parent=next(s for s in make_samples(raw_case()) if s['task_type']=='mixed')
    user=json.loads(parent['input']);answer=json.loads(parent['output'])
    assert len(parent['messages'][0]['content'])<200
    assert all(not isinstance(v,dict) for v in user['labs'].values())
    assert all(not isinstance(v,dict) for report in answer.values() for v in report.values())
    assert '7.12' not in parent['input'] and answer['第1天']['pH']==7.12
    assert parent['messages'][-1]['content']==parent['output']
    assert answer['第1天']['CRP(mg/L)']=='<0.5'


def test_missing_days_are_not_interpolated_into_a_daily_trajectory():
    samples=make_samples(raw_case(gaps=True))
    assert all(s['provenance']['baseline_day']!=0 for s in samples)
    assert all(s['provenance']['target_days']==list(range(s['provenance']['baseline_day']+1,s['provenance']['baseline_day']+s['n_steps']+1)) for s in samples)


def test_joint_antibiotics_remain_a_combination_not_false_single_drug_labels():
    raw=raw_case();raw['诊疗过程']='优立新联合头孢噻肟抗感染（1.1-1.5）。'
    samples=make_samples(raw)
    assert not any(s['task_type']=='single_step' for s in samples)
    assert not any(s['task_type']=='mixed' for s in samples)
    combined=next(s for s in samples if s['task_type']=='combination')
    names=[a['name'] for a in json.loads(combined['input'])['actions']['1']]
    assert names==['优立新','头孢噻肟']


def test_unknown_course_duration_is_not_extended_across_hospitalization():
    raw=raw_case();raw['诊疗过程']='入院予CPAP支持。'
    samples=make_samples(raw)
    assert len(samples)==1 and samples[0]['task_type']=='single_step'
    assert samples[0]['provenance']['baseline_day']==0


def test_each_drug_owns_its_own_course_not_all_ranges_in_the_paragraph():
    raw=raw_case();raw['出院时间']='2024-02-05'
    raw['诊疗过程']='加用头孢他啶（1.9-1.17）、丙球对症支持治疗4天（1.10-1.13）、美平（1.17-1.24）、优立新（1.17-1.30）、阿奇霉素（1.17-1.22）。'
    therapies,_=detailed_treatments(raw,2)
    intervals={}
    for t in therapies:intervals.setdefault(t['name'],set()).add((t['start'],t['end']))
    assert intervals['免疫球蛋白']=={(9,12)}
    assert intervals['头孢他啶']=={(8,16)}
    assert intervals['美平']=={(16,23)}
    assert intervals['优立新']=={(16,29)}
    assert intervals['阿奇霉素']=={(16,21)}


def test_repeated_courses_expand_only_inside_the_same_drugs_date_parentheses():
    raw=raw_case();raw['出院时间']='2024-01-20'
    raw['诊疗过程']='阿奇霉素（1.2-1.4及1.8-1.10）、丙球（1.12-1.13）。'
    therapies,_=detailed_treatments(raw,2)
    assert {(t['start'],t['end']) for t in therapies if t['name']=='阿奇霉素'}=={(1,3),(7,9)}
    assert {(t['start'],t['end']) for t in therapies if t['name']=='免疫球蛋白'}=={(11,12)}


def test_relative_admission_day_and_explicit_duration_are_not_silently_lost():
    raw=raw_case();raw['诊疗过程']='入院第2天黄疸高予以光疗3天。'
    therapies,_=detailed_treatments(raw,2)
    assert len(therapies)==1
    assert therapies[0]['start']==1 and therapies[0]['end']==3
    assert therapies[0]['time_precision']=='relative_admission_day'


def test_export_only_four_csvs_and_original_workbook_is_unchanged(tmp_path):
    source=tmp_path/'source.xlsx';wb=openpyxl.Workbook();ws=wb.active;ws.append(FIELDS)
    raw=raw_case();ws.append([raw.get(k,'') for k in FIELDS]);wb.save(source);wb.close()
    original=source.read_bytes();target=tmp_path/'sft'
    report=build_sft(source,target,secret=b'x'*32)
    assert source.read_bytes()==original and report['training_executed'] is False
    assert sorted(p.name for p in target.iterdir())==['combination.csv','mixed.csv','multi_step.csv','single_step.csv']
    for p in target.iterdir():
        rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
        assert rows
        assert all(len(json.loads(r['messages']))==3 for r in rows)
