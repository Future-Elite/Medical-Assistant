"""Compact model-facing projections. Full provenance remains in the EHR store."""
from __future__ import annotations

import copy
import math
import re

PROTOCOL_VERSION = 'compact-labs-1.0'
DISPLAY = {'ph': 'pH', 'paco2': 'PaCO2', 'sao2': 'SaO2', 'spo2': 'SpO2',
           'crp': 'CRP', 'pct': 'PCT', 'wbc': 'WBC', 'bilirubin': '总胆红素'}


def metric_labels(labs):
    result = {}
    for key, item in labs.items():
        name = DISPLAY.get(key, item.get('name', key))
        unit = item.get('unit', '')
        label = name if unit in ('', 'unitless') else f'{name}({unit})'
        if label in result.values():
            raise ValueError('指标名称与单位重复，不能无损表示为化验字典。')
        result[key] = label
    return result


def labs_json(labs):
    """One report -> one dict. No thresholds, per-value prose or point imputation."""
    labels = metric_labels(labs)
    result = {}
    for key, item in labs.items():
        value = item.get('value')
        if value is None and item.get('comparator') and item.get('bound') is not None:
            value = f"{item['comparator']}{item['bound']:g}"
        result[labels[key]] = value
    return result


def patient_json(intake):
    fields = {'sex': 'sex', 'gestational_weeks': 'ga_weeks', 'gestational_days': 'ga_days',
              'postnatal_age_hours': 'age_hours_at_admission', 'birth_weight_g': 'birth_weight_g',
              'chief_complaint': 'complaint', 'symptoms': 'symptoms', 'perinatal_risks': 'risks'}
    return {label: copy.deepcopy(intake[key]) for key, label in fields.items()
            if intake.get(key) not in (None, '', [], ())}


def report_json(report):
    result = {'id': report['event_id'], 'at_hours': report['collected_at_hours'],
              'labs': labs_json({item['key']: item for item in report['items']})}
    if report['time_hours'] != report['collected_at_hours']:
        result['reported_at_hours'] = report['time_hours']
    if report.get('session_type') == 'admission':
        result['time_precision'] = 'admission_window_not_verified_simultaneous'
    return result


def policy_view(visible):
    """Only published reports; current is the latest report, never a hidden state."""
    reports = [report_json(report) for report in visible['observations']]
    return {'patient': patient_json(visible['intake']), 'time_hours': visible['time_hours'],
            'history': reports[:-1], 'current': reports[-1] if reports else None,
            'conditions': [c['code'] for c in visible['conditions']],
            'active_treatments': copy.deepcopy(visible['active_treatments']),
            'treatments': copy.deepcopy(visible['executed_actions']),
            'pending_tests': copy.deepcopy(visible['pending_tests']),
            'remaining_cycles': visible['remaining_cycles']}


def dynamics_payload(state, context, ledger, end, *, mode, scenario, visible_history):
    """All times are admission-relative hours; stale anchors keep their sample ages."""
    labels = metric_labels(state['labs'])
    # The baseline and prior published reports appear once each in history.
    # A snapshot already represented by current labs need not be sent again.
    current_labs = labs_json(state['labs'])
    sampled_at = {labels[k]: item['as_of_hours'] for k, item in state['labs'].items()}
    reports = [report_json(report) for report in visible_history]
    baseline = {'at_hours': 0, 'labs': labs_json(state['baseline']),
                'time_precision': 'admission_window_not_verified_simultaneous'}
    if not any(r['at_hours'] == 0 and r['labs'] == baseline['labs'] for r in reports):
        reports.insert(0, baseline)
    history = [r for r in reports if not all(
        label in current_labs and current_labs[label] == value and sampled_at[label] == r['at_hours']
        for label, value in r['labs'].items())]
    current = {'labs': current_labs}
    if len(set(sampled_at.values())) == 1:
        current['at_hours'] = next(iter(sampled_at.values()))
    else:
        current['sampled_at_hours'] = sampled_at
    return {'protocol_version': PROTOCOL_VERSION, 'branch_mode': mode, 'scenario_index': scenario,
            'patient': patient_json(context), 'time_hours': state['clock_hours'],
            'current_kind': 'visible_observations' if mode == 'planning' else 'simulated_hidden_state',
            'history': history, 'current': current,
            'initial_time_precision': 'admission_window_not_verified_simultaneous',
            'actions': sorted(copy.deepcopy(ledger), key=lambda e: e['at_hours']),
            'predict_at_hours': end, 'output_keys': list(labels.values()),
            'signed_metric_keys': [labels[k] for k, v in state['labs'].items() if v.get('allows_negative')]}


def prediction_fields(value):
    """Translate a strict flat value into the internal representation, not a range clamp."""
    if isinstance(value, str):
        match = re.fullmatch(r'(<=|>=|<|>|≤|≥)\s*(.*)', value)
        if not match:
            raise ValueError('无效的检出限表示。')
        bound = float(match[2])
        if not math.isfinite(bound):
            raise ValueError('检出限必须是有限值。')
        return {'value': None, 'comparator': {'<=': '≤', '>=': '≥'}.get(match[1], match[1]), 'bound': bound}
    return {'value': value, 'comparator': None, 'bound': None}
