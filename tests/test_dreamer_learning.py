import copy
import hashlib
import json
import random
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import sklearn

from ehrdreamer.contracts import Intake, GenerateRequest
from ehrdreamer.learning import dataset as extraction
from ehrdreamer.learning.dataset import observed_values, treatment_intervals, action_features, dated_blocks
from ehrdreamer.learning.schema import FEATURES, features, group_split
from ehrdreamer.learning.runtime import get_engine, ModelArtifactError
from ehrdreamer.learning.train import fit_regression
from ehrdreamer.models import Models


def test_values_have_real_units_censoring_and_distinct_saturation_targets():
    audit={}
    from collections import Counter
    audit=Counter()
    values=observed_values('CRP:<0.50mg/L，PCT:2.1ng/mL，sO2:95%，SpO2:97%，PaCO2:4kPa，PH值:7.3',audit)
    assert 'crp' not in values and audit['censored_not_used_as_exact']==1
    assert values['sao2']==95 and values['spo2']==97
    assert values['paco2']==pytest.approx(30.00248)
    assert values['ph']==7.3


def test_respiration_parser_does_not_read_hr_as_r():
    values=observed_values('HR140次/分，R60次/分，RR58次/分，T36.7℃')
    assert values['respiratory_rate']==59
    assert values['heart_rate']==140 and values['temperature']==36.7


def test_unknown_treatment_is_not_a_negative_label():
    from collections import Counter
    audit=Counter()
    intervals=treatment_intervals('头孢呋辛抗感染（4.21-4.25）。予光疗退黄。',date(2024,4,21),date(2024,4,26),audit)
    features=action_features(intervals,date(2024,4,22),date(2024,4,23))
    assert features['antimicrobial']==1
    assert 'phototherapy' not in features
    assert 'antimicrobial' not in action_features(intervals,date(2024,4,25),date(2024,4,26))


def test_date_range_is_not_shared_between_different_treatment_mentions():
    from collections import Counter
    audit=Counter()
    result=treatment_intervals('保暖、心电监护，头孢抗感染（4.21-4.25）。未予光疗（4.21-4.23）。',date(2024,4,21),date(2024,4,26),audit)
    assert result['antimicrobial'] and not result.get('thermal_nutrition') and not result.get('phototherapy')
    assert audit['ambiguous_range_owned_by_another_treatment']==1


def test_dates_are_sorted_by_clock_not_excel_text_order_and_past_fill_has_age(tmp_path,monkeypatch):
    raw={'入院时间':'2024.04.21','出院时间':'2024.04.26',
         '实验室检查及主要会诊':'2024.04.24 CRP:3mg/L。2024.04.22 CRP:12mg/L,白细胞:10×10^9/L。2024.04.23 CRP:6mg/L。',
         '诊疗过程':'头孢抗感染（4.21-4.25）。','出院诊断':'PRIVATE_FUTURE_DX','治疗结果':'PRIVATE_FUTURE_OUTCOME'}
    case={'raw':raw,'intake':{'gestational_weeks':32},'group_id':hashlib.sha256(b'fixture').hexdigest(),'row':2,'quality_flags':[]}
    catalog=SimpleNamespace(rows=[case],metadata={'source_sha256':'f'*64},ensure=lambda:None)
    monkeypatch.setattr(extraction,'SourceCatalog',lambda *a,**k:catalog)
    result=extraction.build_dataset(test_json=tmp_path/'absent.json')
    rows=result['transitions'];assert len(rows)==2
    assert rows[0]['y']['crp']==6 and rows[0]['x'][FEATURES.index('previous_crp')]==12
    assert rows[1]['y']['crp']==3 and rows[1]['x'][FEATURES.index('previous_wbc')]==10
    assert rows[1]['x'][FEATURES.index('previous_age_wbc')]==24
    assert rows[0]['x'][FEATURES.index('action_phototherapy')] is None
    assert rows[0]['x'][FEATURES.index('action_antimicrobial')]==1
    assert 'PRIVATE_FUTURE' not in json.dumps(result)
    before=copy.deepcopy(result)
    raw['出院诊断']='changed';raw['治疗结果']='changed'
    assert extraction.build_dataset(test_json=tmp_path/'absent.json')==before


def test_no_dates_do_not_create_fake_temporal_pairs(tmp_path,monkeypatch):
    row={'raw':{'入院时间':'2024.01.01','出院时间':'2024.01.05','实验室检查及主要会诊':'CRP:10mg/L，复查CRP:3mg/L'},
         'intake':{},'group_id':'a'*64,'row':2,'quality_flags':[]}
    monkeypatch.setattr(extraction,'SourceCatalog',lambda *a,**k:SimpleNamespace(rows=[row],metadata={'source_sha256':'f'*64},ensure=lambda:None))
    assert extraction.build_dataset(test_json=tmp_path/'absent.json')['transitions']==[]


def test_trained_artifact_has_disjoint_groups_and_no_synthetic_training():
    engine=get_engine();report=engine.bundle['report'];groups=engine.bundle['groups']
    assert report['group_overlap']==0 and not report['training_uses_synthetic_trajectories']
    assert not groups['train']&groups['validation'] and not groups['train']&groups['test'] and not groups['test']&groups['validation']
    assert {'crp','paco2','bilirubin'}<=set(engine.bundle['transition'])
    assert 'spo2' not in engine.bundle['transition']
    assert 'feeding' not in engine.bundle['initial']
    for metric,head in engine.bundle['transition'].items():
        assert head['estimator'] is not None
        assert report['models']['transition'][metric]['selected_test']['n']>0


def test_initial_state_is_reproducible_and_subdaily_updates_are_not_invented():
    engine=get_engine();intake=Intake(chief_complaint='气促',gestational_weeks=32,birth_weight_g=1600)
    first=engine.initial(intake,random.Random(42));second=engine.initial(intake,random.Random(42))
    assert first.values==second.values
    original=dict(first.values);engine.advance(first,{},0,23,random.Random(1),intake)
    assert first.values==original
    engine.advance(first,{},23,24,random.Random(1),intake)
    assert first.updated_hours['crp']==24 and first.updated_hours['spo2']==0
    assert first.values['spo2']==original['spo2']


def test_observation_exposes_training_coverage_not_fake_physio_mappings():
    engine=get_engine();intake=Intake(chief_complaint='入院评估')
    state=engine.initial(intake,random.Random(42))
    vs=engine.sample('vitals',state,{},random.Random(1),intake)
    spo2=next(i for i in vs if i['key']=='spo2')
    assert spo2['value']==round(state.values['spo2'],2)
    assert not spo2['temporal_model_available'] and spo2['prediction_support']=='initial_distribution_only'
    with pytest.raises(ModelArtifactError):engine.sample('lung_ultrasound',state,{},random.Random(1),intake)
    assert engine.bundle['report']['models']['images']['chest_xray']['enabled_for_generation'] is False


def test_no_artifact_never_uses_handwritten_fallback(tmp_path,monkeypatch):
    from ehrdreamer.learning import runtime
    monkeypatch.setattr(runtime,'DEFAULT_DIRECTORY',tmp_path/'missing-model')
    with pytest.raises(ModelArtifactError,match='train_dreamer.py'):runtime.get_engine()


def test_corrupt_artifact_is_rejected_before_deserialization(tmp_path,monkeypatch):
    from ehrdreamer.learning import runtime
    (tmp_path/'manifest.json').write_text(json.dumps({'sha256':'f'*64,'sklearn_version':sklearn.__version__}),encoding='utf-8')
    (tmp_path/'bundle.joblib').write_bytes(b'not a pickle')
    monkeypatch.setattr(runtime,'DEFAULT_DIRECTORY',tmp_path)
    with pytest.raises(ModelArtifactError,match='哈希'):runtime.get_engine()


def test_imbalanced_feeding_labels_are_not_turned_into_a_quality_model():
    rows=[{'split':'train','group':str(i),'x':features({}),'y':{'feeding':1},'action_coverage':{}} for i in range(80)]
    head,report=fit_regression(rows,'feeding',False)
    assert head is None and report['status']=='insufficient_class_balance'


def test_dynamics_is_local_in_both_generation_modes(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,config):calls.append(config.model)
        def close(self):pass
    monkeypatch.setenv('EHRWORLD_API_KEY','fake-test-key')
    demo=Models(GenerateRequest(case_id='fixture',backend='demo'))
    assert demo.clients=={} and demo.dynamics.model_id
    api=Models(GenerateRequest(case_id='fixture',backend='api',policy_model='Qwen3.8-27B',dynamics_model='MiMo-V2.6-Flash',outcome_model='DeepSeek-V4.1-Flash'),Client)
    assert calls==['Qwen3.8-27B','DeepSeek-V4.1-Flash']
    assert api.dynamics.model_id==demo.dynamics.model_id
    api.close()
