"""Inference from trained artifacts. No hand-written physiological transitions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import sklearn
from threadpoolctl import threadpool_limits

from ehrworld.io import ROOT
from .schema import FEATURES, METRICS, MODEL_VERSION, PANELS, features, observation_features

DEFAULT_DIRECTORY = ROOT/'outputs/dreamer_ml/model'


class ModelArtifactError(RuntimeError):
    pass


@dataclass
class LearnedState:
    values: dict
    intervals: dict
    updated_hours: dict
    model_id: str
    last_update: float = 0
    clock_hours: float = 0
    image_cache: dict = field(default_factory=dict)


def array(row):
    return np.asarray([[np.nan if value is None else value for value in row]], dtype=float)


class LearnedDynamics:
    def __init__(self, bundle=None, model_id=None):
        if bundle is None:
            shared = get_engine()
            self.bundle, self.model_id = shared.bundle, shared.model_id
        else:
            self.bundle, self.model_id = bundle, model_id

    def capabilities(self):
        return {'model_id':self.model_id,'version':self.bundle['version'],
                'initial_metrics':list(self.bundle['initial']), 'transition_metrics':list(self.bundle['transition']),
                'supported_exams':[k for k,metrics in PANELS.items() if any(m in self.bundle['initial'] for m in metrics)] + list(self.bundle['images']),
                'unsupported_exams':[k for k in ('chest_xray','lung_ultrasound','culture') if k not in self.bundle['images']],
                'time_resolution_hours':self.bundle['step_hours'],
                'treatment_effect_identification':'observational_documentation_association_only_not_causal',
                'limitations':self.bundle['report']['limitations']}

    def membership(self, group):
        return next((s for s,groups in self.bundle['groups'].items() if group in groups),'not_in_training_sources')

    def _noise(self, kind, rng):
        pool = self.bundle['noise_pools'].get(kind, [])
        return pool[rng.randrange(len(pool))] if pool else {}

    def initial(self, intake, rng):
        x = array(features(intake))
        values, intervals, noise = {}, {}, self._noise('initial',rng)
        with threadpool_limits(limits=2):
            for metric,head in self.bundle['initial'].items():
                center = float(head['estimator'].predict(x)[0])
                values[metric] = min(head['observed_max'],max(head['observed_min'],center+noise.get(metric,0)))
                intervals[metric] = [min(head['observed_max'],max(head['observed_min'],center+offset)) for offset in (-head['radius90'],head['radius90'])]
        return LearnedState(values,intervals,{k:0 for k in values},self.model_id)

    def prepare_action(self, state, treatments, action, intake):
        return {'model_id':self.model_id,'parameter_response':'not_identified',
                'time_resolution_hours':self.bundle['step_hours']}

    def advance(self, state, treatments, start, end, rng, intake):
        step = self.bundle['step_hours']
        while state.last_update+step <= end+1e-8:
            actions = {target:1.0 for target,item in treatments.items() if item['started_at']<=state.last_update}
            ages = {m:state.last_update-t for m,t in state.updated_hours.items()}
            x = array(features(intake,state.values,state.last_update,step,actions,ages))
            updated, noise = {}, self._noise('transition',rng)
            with threadpool_limits(limits=2):
                for metric,head in self.bundle['transition'].items():
                    if metric not in state.values:
                        continue
                    center = state.values[metric]+float(head['estimator'].predict(x)[0])
                    updated[metric] = min(head['observed_max'],max(head['observed_min'],center+noise.get(metric,0)))
                    state.intervals[metric] = [min(head['observed_max'],max(head['observed_min'],center+offset)) for offset in (-head['radius90'],head['radius90'])]
                    state.updated_hours[metric] = state.last_update+step
            state.values.update(updated)
            state.last_update += step
            state.image_cache.clear()
        state.clock_hours = end

    def sample(self, target, state, treatments, rng, intake):
        if target in PANELS:
            result = []
            for metric in PANELS[target]:
                if metric not in state.values:
                    continue
                label,unit = METRICS[metric]
                result.append({'key':metric,'name':label,'value':round(state.values[metric],3 if metric=='ph' else 2),'unit':unit,
                               'model_id':self.model_id,'interval90':state.intervals.get(metric),
                               'prediction_support':'trained_transition' if state.updated_hours[metric]>0 else 'initial_distribution_only',
                               'state_updated_at_hours':state.updated_hours[metric],
                               'temporal_model_available':metric in self.bundle['transition']})
            if target=='vitals':
                fio2 = max((v.get('fio2') or .21 for k,v in treatments.items() if k in ('oxygen','cpap')),default=.21)
                pressure = treatments.get('cpap',{}).get('pressure_cm_h2o') or 0
                result += [{'key':'fio2','name':'已记录吸入氧浓度设置','value':fio2,'unit':'fraction','prediction_support':'doctor_control_setting'},
                           {'key':'cpap','name':'已记录CPAP压力设置','value':pressure,'unit':'cmH2O','prediction_support':'doctor_control_setting'}]
            if not result:
                raise ModelArtifactError('该检查缺少足够的真实训练标签。')
            return result
        if target not in self.bundle['images']:
            raise ModelArtifactError('该检查缺少足够的真实训练标签，禁止回退到手写结果。')
        if target not in state.image_cache:
            head = self.bundle['images'][target]
            with threadpool_limits(limits=2):
                probabilities = head['estimator'].predict_proba(array(observation_features(intake,state.values)))[0]
            label = rng.choices(head['classes'],weights=probabilities,k=1)[0]
            descriptions = {'no_definite_abnormality':'模型预测：未见明确肺部异常类别。',
                            'diffuse_or_airspace_abnormality':'模型预测：弥漫或肺泡性异常类别，需结合临床鉴别。',
                            'other_pulmonary_abnormality':'模型预测：其他肺部异常类别，需结合临床鉴别。'}
            state.image_cache[target] = [{'key':'finding','name':'从影像原文标签学习的表现类别',
                'value':descriptions[label],'unit':None,'model_id':self.model_id,
                'class_probabilities':{str(k):float(v) for k,v in zip(head['classes'],probabilities)},
                'prediction_support':'trained_text_label_classifier_not_image_pixel_model'}]
        return state.image_cache[target]


@lru_cache(maxsize=2)
def _load(directory, digest):
    path = Path(directory)/'bundle.joblib'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise ModelArtifactError('训练产物哈希不匹配，请重新训练。')
    bundle = joblib.load(path)
    if bundle['version']!=MODEL_VERSION or bundle['features']!=FEATURES:
        raise ModelArtifactError('训练产物版本不匹配。')
    # Predicting one patient per call does not benefit from parallel tree
    # reduction; serial reduction also makes seeded traces bit-reproducible.
    for kind in ('initial','transition','images'):
        for head in bundle[kind].values():
            estimator=head['estimator']
            steps=getattr(estimator,'named_steps',{'model':estimator})
            for component in steps.values():
                if hasattr(component,'n_jobs'):component.set_params(n_jobs=1)
    return LearnedDynamics(bundle,digest[:16])


def get_engine():
    directory = DEFAULT_DIRECTORY.resolve()
    if not directory.is_relative_to(ROOT):
        raise ModelArtifactError('模型产物必须位于项目内。')
    try:
        manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):
        raise ModelArtifactError('缺少已训练模型，请先运行 python -B scripts/train_dreamer.py。') from None
    if manifest['sklearn_version'] != sklearn.__version__:
        raise ModelArtifactError('scikit-learn版本已变化，请在当前环境重新训练模型。')
    return _load(str(directory),manifest['sha256'])


def snapshot(state):
    return {'values':dict(state.values),'intervals':dict(state.intervals),
            'updated_hours':dict(state.updated_hours),'model_id':state.model_id,
            'last_update':state.last_update,'clock_hours':state.clock_hours}
