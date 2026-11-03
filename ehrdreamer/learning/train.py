"""Train on observed labels, choose on validation groups, report untouched test groups."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

import joblib
import numpy as np
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from threadpoolctl import threadpool_limits

from ehrworld.io import output_path
from .schema import ACTIONS, FEATURES, METRICS, MODEL_VERSION, OBS_FEATURES


def matrix(rows):
    return np.asarray([[np.nan if v is None else v for v in r['x']] for r in rows], dtype=float)


def group_weights(rows):
    counts = Counter(r['group'] for r in rows)
    weights = np.asarray([1 / counts[r['group']] for r in rows])
    return weights / weights.mean()


def regression_scores(y, predicted):
    return {'mae':float(mean_absolute_error(y, predicted)),
            'rmse':float(math.sqrt(mean_squared_error(y, predicted))), 'n':len(y)}


def fit_regression(rows, metric, transition, minimum=60):
    previous_column = FEATURES.index('previous_' + metric)
    selected = [r for r in rows if metric in r['y'] and
                (not transition or r['x'][previous_column] is not None)]
    subsets = {s:[r for r in selected if r['split']==s] for s in ('train','validation','test')}
    counts = {s:len(r) for s,r in subsets.items()}
    if metric=='feeding':
        labels = Counter(r['y'][metric] for r in subsets['train'])
        if min(labels.get(0,0),labels.get(1,0))<20:
            return None, {'status':'insufficient_class_balance','counts':counts,'train_label_counts':dict(labels)}
    if counts['train'] < minimum or counts['validation'] < 15 or counts['test'] < 15:
        return None, {'status':'insufficient_observed_labels','counts':counts}
    xs, ys = {}, {}
    for split, subset in subsets.items():
        xs[split] = matrix(subset)
        ys[split] = np.asarray([r['y'][metric] - (r['x'][previous_column] if transition else 0) for r in subset])
    candidates = {
        'hist_gradient_boosting': HistGradientBoostingRegressor(loss='absolute_error', max_iter=110,
                   max_leaf_nodes=15, min_samples_leaf=18, l2_regularization=3, early_stopping=False, random_state=42),
        'extra_trees': make_pipeline(SimpleImputer(add_indicator=True, keep_empty_features=True),
                   ExtraTreesRegressor(n_estimators=72, min_samples_leaf=7, max_features=.8, n_jobs=2, random_state=42)),
        'persistence' if transition else 'training_median': DummyRegressor(strategy='constant',constant=0) if transition else DummyRegressor(strategy='median'),
    }
    fitted, validation = {}, {}
    for name, estimator in candidates.items():
        weights = group_weights(subsets['train'])
        kwargs = {'extratreesregressor__sample_weight':weights} if name=='extra_trees' else {'sample_weight':weights}
        estimator.fit(xs['train'], ys['train'], **kwargs)
        fitted[name] = estimator
        validation[name] = regression_scores(ys['validation'], estimator.predict(xs['validation']))
    choice = min(validation, key=lambda key:validation[key]['mae'])
    estimator = fitted[choice]
    residuals = ys['validation'] - estimator.predict(xs['validation'])
    quantile = min(1, math.ceil((len(residuals)+1)*.9)/len(residuals))
    radius = float(np.quantile(np.abs(residuals), quantile, method='higher'))
    physical = np.asarray([r['y'][metric] for r in subsets['train']])
    head = {'estimator':estimator, 'algorithm':choice, 'delta':transition, 'radius90':radius,
            'residuals':residuals, 'observed_min':float(physical.min()), 'observed_max':float(physical.max()),
            'counts':counts}
    test = {name:regression_scores(ys['test'], est.predict(xs['test'])) for name,est in fitted.items()}
    lower, upper = estimator.predict(xs['test']) - radius, estimator.predict(xs['test']) + radius
    coverage = float(np.mean((ys['test']>=lower)&(ys['test']<=upper)))
    actions = {a:sum(r['action_coverage'].get(a)==1 for r in subsets['train']) for a in ACTIONS}
    report = {'status':'trained','selected_algorithm':choice,'counts':counts,
              'train_groups':len({r['group'] for r in subsets['train']}),
              'validation_candidates':validation,'test_candidates':test,
              'selected_test':test[choice], 'test_interval90_coverage':coverage,
              'calibration_radius90':radius,'documented_action_positive_rows':actions,
              'documented_negative_action_rows':0,
              'treatment_effect_identification':'observational_documentation_association_only_not_causal'}
    return head, report


def fit_image(rows, target):
    selected = [r for r in rows if r['target']==target]
    subsets = {s:[r for r in selected if r['split']==s] for s in ('train','validation','test')}
    counts = {s:len(r) for s,r in subsets.items()}
    class_counts = Counter(r['label'] for r in subsets['train'])
    if counts['train'] < 60 or counts['validation'] < 15 or counts['test'] < 15 or len(class_counts)<2:
        return None, {'status':'insufficient_observed_labels','counts':counts,'train_classes':dict(class_counts)}
    estimator = make_pipeline(SimpleImputer(add_indicator=True, keep_empty_features=True),
                              ExtraTreesClassifier(n_estimators=100,min_samples_leaf=5,class_weight='balanced',n_jobs=2,random_state=42))
    estimator.fit(matrix(subsets['train']), [r['label'] for r in subsets['train']],
                  extratreesclassifier__sample_weight=group_weights(subsets['train']))
    actual = [r['label'] for r in subsets['test']]
    predicted = estimator.predict(matrix(subsets['test']))
    majority = class_counts.most_common(1)[0][0]
    report = {'status':'trained','algorithm':'extra_trees_classifier','counts':counts,'train_classes':dict(class_counts),
              'test_accuracy':float(accuracy_score(actual,predicted)),
              'test_balanced_accuracy':float(balanced_accuracy_score(actual,predicted)),
              'test_macro_f1':float(f1_score(actual,predicted,average='macro',zero_division=0)),
              'majority_test_accuracy':float(accuracy_score(actual,[majority]*len(actual))),
              'label_source':'explicit radiology text keyword abstraction, not radiologist adjudication',
              'clinical_validation':False}
    val_actual = [r['label'] for r in subsets['validation']]
    val_predicted = estimator.predict(matrix(subsets['validation']))
    report['validation_accuracy'] = float(accuracy_score(val_actual,val_predicted))
    report['validation_majority_accuracy'] = float(accuracy_score(val_actual,[majority]*len(val_actual)))
    if min(class_counts.values())<20 or report['validation_accuracy']<=report['validation_majority_accuracy']:
        report.update(status='trained_not_qualified',enabled_for_generation=False,
                      reason='minority_training_class_below_20_or_validation_does_not_beat_majority')
        return None, report
    report['enabled_for_generation'] = True
    return {'estimator':estimator,'classes':list(estimator.classes_),'counts':counts}, report


def train(dataset, directory='outputs/dreamer_ml/model'):
    directory = output_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    all_rows = dataset['initial'] + dataset['transitions'] + dataset['images']
    groups = {s:{r['group'] for r in all_rows if r['split']==s} for s in ('train','validation','test')}
    assert not groups['train']&groups['test'] and not groups['train']&groups['validation'] and not groups['validation']&groups['test']
    report = {'version':MODEL_VERSION,'trained_at':datetime.now(timezone.utc).isoformat(),'sklearn_version':sklearn.__version__,
              'training_code_sha256':{name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('dataset.py','schema.py','train.py')},
              'source_hashes':dataset['source_hashes'],'extraction_audit':dataset['audit'],
              'dataset_counts':{k:len(dataset[k]) for k in ('initial','transitions','images')},
              'split_counts':dataset['split_counts'],'group_counts':{s:len(v) for s,v in groups.items()},
              'group_overlap':0, 'feature_names':FEATURES,'image_feature_names':OBS_FEATURES,
              'models':{'initial':{},'transition':{},'images':{}},'training_uses_synthetic_trajectories':False,
              'time_resolution_hours':24,'causal_treatment_effect_validated':False,
              'limitations':['检验时间主要精确到日，不能据此学习小时或分钟级治疗响应。',
                             '未记录治疗不等于未治疗；动作特征仅表示有日期覆盖的文书记录，未识别因果治疗效应。',
                             '无足够重复测量的指标只能预测初始分布，不生成未经训练的后续变化。',
                             '低于检出限的数值未当作精确值训练，可能产生选择偏差。',
                             '影像标签由原文明确词汇抽取，尚未经过影像专家独立标注验证。',
                             '测试集按来源标识分组留出；仍需要外部临床验证。']}
    bundle = {'version':MODEL_VERSION,'source_hashes':dataset['source_hashes'],'initial':{},'transition':{},'images':{},
              'groups':groups,'features':FEATURES,'report':report,'step_hours':24}
    with threadpool_limits(limits=2):
        for kind, data_key in [('initial','initial'),('transition','transitions')]:
            for metric in METRICS:
                head, result = fit_regression(dataset[data_key], metric, kind=='transition')
                report['models'][kind][metric] = result
                if head:
                    bundle[kind][metric] = head
                print(json.dumps({'stage':kind,'metric':metric,'status':result['status'],
                                  'algorithm':result.get('selected_algorithm'),
                                  'test_mae':result.get('selected_test',{}).get('mae')},ensure_ascii=True),flush=True)
        for target in ('chest_xray','lung_ultrasound'):
            head, result = fit_image(dataset['images'], target)
            report['models']['images'][target] = result
            if head:
                bundle['images'][target] = head
        # Shared validation-row residual vectors preserve observed error co-movement.
        bundle['noise_pools'] = {}
        for kind in ('initial','transition'):
            pool = {}
            validation_rows = [r for r in dataset['initial' if kind=='initial' else 'transitions'] if r['split']=='validation']
            for metric,head in bundle[kind].items():
                column = FEATURES.index('previous_'+metric)
                selected = [(i,r) for i,r in enumerate(validation_rows) if metric in r['y'] and (kind=='initial' or r['x'][column] is not None)]
                predictions = head['estimator'].predict(matrix([r for _,r in selected]))
                for (row_index,row),predicted in zip(selected,predictions):
                    prior = row['x'][column] if kind=='transition' else 0
                    pool.setdefault(row_index,{})[metric] = float(row['y'][metric]-prior-predicted)
            bundle['noise_pools'][kind] = list(pool.values())
    # Never refit on validation or test after model selection/calibration.
    model_path = directory/'bundle.joblib'
    joblib.dump(bundle, model_path, compress=3)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    report['artifact_sha256'] = digest
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    manifest = {'version':MODEL_VERSION,'artifact':'bundle.joblib','sha256':digest,'sklearn_version':sklearn.__version__,
                'source_hashes':dataset['source_hashes'],'report':'report.json'}
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
