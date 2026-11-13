"""All fixtures below are artificial; no model API or training is invoked."""
import copy
import csv
import json
from dataclasses import asdict

import openpyxl
import pytest

from ehrdreamer.source import FIELDS
from ehrdreamer.dual.references import rules_for
from ehrdreamer.rl_data.extract import extract_lab_block, unique_day_state, labs_from_row, treatments_from_row
from ehrdreamer.rl_data.dataset import build_dataset, split_for
from ehrdreamer.rl_data.reward import RewardConfig, RewardUnavailable, score_trajectory
from ehrdreamer.rl_data import verl_adapter


def labs(ph=7.2,co2=62):
    return {'ph':{'key':'ph','unit':'unitless','value':ph},'paco2':{'key':'paco2','unit':'mmHg','value':co2}}


def snapshots(values):
    return [{'time_hours':t,'labs':{k:{**v,'as_of_hours':t} for k,v in labs(*x).items()}}
            for t,x in zip((24,48,72),values)]


def test_all_lab_labels_censoring_and_source_flags_without_normal_imputation():
    text='总胆红素:100umol/L↑,白细胞:12×10^9/L ,CRP:<0.50mg/L ,钾:5.1mmol/L ,ABO血型:B型 ,pCO2:6kPa'
    items=extract_lab_block(text,row=2,field='labs',offset=0,day=0)
    values={x['key']:x for x in items}
    assert values['bilirubin']['unit']=='µmol/L' and values['bilirubin']['source_flag']=='↑'
    assert values['wbc']['unit']=='10^9/L' and values['wbc']['source_flag'] is None
    assert values['crp']['value'] is None and values['crp']['bound']==.5
    assert values['potassium']['value']==5.1 and any(x.get('text')=='B型' for x in items)
    assert values['paco2']['value']==pytest.approx(45.00372)


def test_same_day_conflicts_are_not_averaged_or_ordered():
    items=extract_lab_block('pH:7.2,pH:7.4',row=2,field='labs',offset=0,day=0)
    state=unique_day_state(items)
    assert state['ph']['ambiguous_same_day'] and state['ph']['value'] is None
    assert [x['value'] for x in state['ph']['same_day_results']]==[7.2,7.4]


def test_report_identifiers_and_enumerated_imaging_are_not_lab_metrics():
    items=extract_lab_block('西院彩超(A82EBE5B6A5D12345678):1.目前可视范围内未见异常,DX报告(461301):未见异常,耳聋基因检测(R2024-00224):阴性,8% 血小板:180×10^9/L,cHCO3-(P,st):22mmol/L,培养结果:5天培养未生长',
                            row=2,field='labs',offset=0,day=0)
    assert not any('A82E' in i['name'] for i in items)
    assert not any('报告' in i['name'] or '461301' in i['name'] for i in items)
    assert not any('R2024-' in i['name'] for i in items)
    assert any(i['key']=='platelet' and i['name']=='血小板' for i in items)
    assert any(i['name']=='cHCO3-(P,st)' and i['value']==22 for i in items)
    assert any(i.get('text')=='5天培养未生长' for i in items)


def test_dated_labs_are_not_made_daily_and_outside_window_is_excluded():
    raw={'入院时间':'2024-01-01','出院时间':'2024-01-06',
         '实验室检查及主要会诊':'2024.01.01 pH:7.2,2024.01.04 pH:7.3,2024.01.09 pH:7.9'}
    items,audit=labs_from_row(raw,2)
    assert {x['day'] for x in items}=={0,3} and audit['outside_encounter_lab_blocks']==1


def test_treatment_ranges_remain_retrospective_and_unknown_is_not_no_treatment():
    raw={'入院时间':'2024-01-01','出院时间':'2024-01-10',
         '诊疗过程':'予头孢抗感染（1.1-1.3），CPAP支持（1.2-1.5）。予以光疗。未予输血。'}
    items=treatments_from_row(raw,2)
    assert any(x['target']=='antimicrobial' and x['day']==0 and x['end_day']==2 for x in items)
    assert any(x['target']=='cpap' and x['day']==1 and x['end_day']==4 for x in items)
    assert any(x['target']=='phototherapy' and x['day'] is None for x in items)
    assert not any(x['target']=='transfusion' for x in items)


def test_unspecified_respiratory_support_does_not_invent_mode_or_intubation():
    raw={'入院时间':'2024-01-01','出院时间':'2024-01-10','诊疗过程':'给予无创呼吸支持（1.1-1.3）。'}
    targets={x['target'] for x in treatments_from_row(raw,2)}
    assert targets=={'noninvasive_support'}
    raw['诊疗过程']='CPAP无创通气支持（1.1-1.3）。'
    assert {x['target'] for x in treatments_from_row(raw,2)}=={'cpap'}


def test_terminal_abnormal_count_dominates_every_shaping_difference():
    initial=labs();rules=rules_for({'gestational_weeks':32},initial)
    terminal_better=score_trajectory(initial,rules,snapshots([(7.2,62),(7.2,62),(7.35,42)]),safety_passed=True)
    terminal_worse=score_trajectory(initial,rules,snapshots([(7.35,42),(7.35,42),(7.35,62)]),safety_passed=True)
    assert terminal_better['score']>terminal_worse['score']


def test_long_horizon_burden_and_rebound_change_equal_terminal_rewards():
    initial=labs();rules=rules_for({'gestational_weeks':32},initial)
    early=score_trajectory(initial,rules,snapshots([(7.35,42)]*3),safety_passed=True)
    late=score_trajectory(initial,rules,snapshots([(7.2,62),(7.2,62),(7.35,42)]),safety_passed=True)
    rebound=score_trajectory(initial,rules,snapshots([(7.35,42),(7.2,62),(7.35,42)]),safety_passed=True)
    assert early['score']>late['score'] and early['score']>rebound['score']
    assert rebound['new_or_rebound_fraction']==1
    assert score_trajectory(initial,rules,snapshots([(7.35,42)]*3),safety_passed=False)['score']==-1


@pytest.mark.parametrize('failure',['missing','stale','short','duplicate','safety_unknown'])
def test_reward_cannot_be_gamed_by_short_rollout_or_missing_measurements(failure):
    initial=labs();rules=rules_for({},initial);trace=snapshots([(7.35,42)]*3);safety=True
    if failure=='missing':trace[-1]['labs'].pop('ph')
    if failure=='stale':trace[-1]['labs']['ph']['as_of_hours']=48
    if failure=='short':trace.pop()
    if failure=='duplicate':trace.append(copy.deepcopy(trace[-1]))
    if failure=='safety_unknown':safety=None
    with pytest.raises(RewardUnavailable):score_trajectory(initial,rules,trace,safety_passed=safety)


def synthetic_workbook(path):
    wb=openpyxl.Workbook();ws=wb.active;ws.append(FIELDS)
    base={'性别':'男','住院号':'SYNTHETIC_PRIVATE_1','入院时间':'2024-01-01','出院时间':'2024-01-08','孕周':'32',
          '入院时主要症状及体征':'患儿男，初生。主诉：气促。母孕32周，出生体重1600g。',
          '实验室检查及主要会诊':'2024.01.01 pH:7.20,pCO2:62mmHg,CRP:<0.50mg/L,钾:5.1mmol/L,2024.01.03 pH:7.25,pCO2:59mmHg,2024.01.07 pH:7.44,pCO2:39mmHg',
          '诊疗过程':'患儿入院予CPAP支持（1.1-1.7），抗感染（1.1-1.5）。FUTURE_COURSE_SENTINEL。',
          '出院时情况':'FUTURE_RESULT_SENTINEL','出院诊断':'FUTURE_DIAGNOSIS_SENTINEL'}
    # A duplicated encounter and a second encounter of the same patient.
    for row in (base,base,{**base,'入院时间':'2024-02-01','出院时间':'2024-02-02',
                          '实验室检查及主要会诊':'2024.02.01 pH:7.20,pCO2:62mmHg'}):
        ws.append([row.get(k,'') for k in FIELDS])
    wb.save(path);wb.close()


def test_patient_level_csv_and_nested_parquet_have_no_future_label_leakage(tmp_path):
    pq=pytest.importorskip('pyarrow.parquet')
    source=tmp_path/'source.xlsx';synthetic_workbook(source);before=source.read_bytes()
    report=build_dataset(source,tmp_path/'dataset')
    assert source.read_bytes()==before and report['patients']==1
    assert report['encounters_after_deduplication']==2 and report['counts']['duplicate_encounters_excluded']==1
    with (tmp_path/'dataset/policy_gspo.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    row=rows[0];assert row['rl_eligible']=='true' and row['output']==''
    prompt=json.loads(row['prompt']);text=json.dumps(prompt,ensure_ascii=False)
    assert 'SYNTHETIC_PRIVATE_1' not in text and 'FUTURE_' not in text
    assert '7.44' not in text and '39.0' not in text
    assert 'end_day' not in text and row['anchor_day']=='2'
    compact_user=json.loads(prompt[1]['content'])
    assert compact_user['history']
    assert all(not isinstance(v,dict) for v in compact_user['current']['labs'].values())
    assert 'response_schema' not in compact_user and len(prompt[0]['content'])<500
    assert json.loads(row['reference_treatment'])
    assert 'FUTURE_COURSE_SENTINEL' in row['reference_treatment_text']
    data=pq.read_table(tmp_path/'dataset/verl'/f"{row['split']}.parquet").to_pylist()
    assert len(data)==1 and isinstance(data[0]['prompt'],list)
    assert set(data[0])=={'data_source','prompt','ability','reward_model','extra_info'}
    assert 'reference_treatment' not in data[0] and data[0]['extra_info']['split']==split_for(row['patient_id'])
    rerun=build_dataset(source,tmp_path/'dataset')
    assert rerun['eligible_split_counts']==report['eligible_split_counts']


def test_reward_adapter_requires_independent_environment_and_ignores_reference(tmp_path,monkeypatch):
    source=tmp_path/'source.xlsx';synthetic_workbook(source);build_dataset(source,tmp_path/'dataset',parquet=False)
    with (tmp_path/'dataset/policy_gspo.csv').open(encoding='utf-8-sig',newline='') as stream:row=next(csv.DictReader(stream))
    truth=json.loads(row['reward_model'])['ground_truth'];context=verl_adapter._load_context(truth)
    response={'actions':[{'operation':'observe','target':'observation','reassess_after_hours':24,
              'evidence_ids':[context['visible_evidence_ids'][0]],'guideline_rule_ids':[context['guideline_rule_ids'][0]],'rationale':'合成协议验证'}],
              'monitoring_metric_keys':list(context['reference_rules']),'uncertainty':'人工测试，不作医学预测'}
    monkeypatch.delenv('EHR_GSPO_ROLLOUT_PROVIDER',raising=False)
    monkeypatch.setattr(verl_adapter,'_rollout_provider',None)
    with pytest.raises(RewardUnavailable):verl_adapter.compute_score(row['data_source'],json.dumps(response),truth)
    monkeypatch.setattr(verl_adapter,'_rollout_provider',lambda context,plan:{'snapshots':snapshots([(7.35,42)]*3),'safety_passed':True})
    assert verl_adapter.compute_score(row['data_source'],json.dumps(response),truth)>0
    assert verl_adapter.compute_score(row['data_source'],json.dumps({**response,'predicted_labs':labs(7.35,42)}),truth)==-1


def test_output_path_must_stay_inside_project(tmp_path):
    from ehrdreamer.rl_data.dataset import project_path
    with pytest.raises(ValueError):project_path('D:/outside-project-data.csv')


def test_active_treatments_do_not_depend_on_retrospective_section_order():
    from ehrdreamer.rl_data.dataset import _action_history
    episode={'treatments':[{'event_id':'stopped','target':'cpap','operation':'stop','day':3,'end_day':None,'time_precision':'calendar_day','same_day_order':'unknown'},
        {'event_id':'started','target':'cpap','operation':'documented_treatment','day':0,'end_day':3,'time_precision':'calendar_day','same_day_order':'unknown'}]}
    history,active=_action_history(episode,4)
    assert not active and [x['day'] for x in history]==sorted(x['day'] for x in history)
