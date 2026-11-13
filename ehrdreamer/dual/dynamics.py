from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import Future
from threading import RLock
from ehrdreamer.prompts import DYNAMICS_SYSTEM_PROMPT
from .contracts import TransitionReply, PlanAction
from .protocol import dynamics_payload, metric_labels, prediction_fields
from .catalog import TREATMENTS


class DynamicsError(ValueError):
    pass


def apply_action(active, ledger, action: PlanAction, at, event_id):
    """Bookkeeping only. No physiological response is encoded here."""
    target = action.target
    if action.operation in ('wait', 'continue', 'order_test'):
        return
    if action.operation == 'stop':
        if target not in active:
            raise DynamicsError('不能停止尚未开始的治疗。')
        active.pop(target)
    elif action.operation == 'start':
        if target in active:
            raise DynamicsError('不能重复开始同一治疗。')
        if target not in ('surfactant','transfusion'):
            active[target] = {'target': target, 'started_at': at, 'parameters': action.parameters.model_dump(exclude_none=True)}
    elif action.operation == 'adjust':
        if target not in active:
            raise DynamicsError('不能调整尚未开始的治疗。')
        active[target] = {**active[target], 'parameters': {**active[target]['parameters'], **action.parameters.model_dump(exclude_none=True)}}
    if len(set(active)&{'oxygen','cpap','nippv','invasive_ventilation'})>1:
        raise DynamicsError('同一时刻不能登记多个相互替代的主要呼吸支持方式。')
    ledger.append({'event_id': event_id, 'at_hours': at, 'operation': action.operation,
                   'target': target, 'parameters': action.parameters.model_dump(exclude_none=True),
                   'exposure_kind':'procedure_event' if target in ('surfactant','transfusion') else 'ongoing'})


def time_segments(ledger, start, end):
    boundaries = sorted({start, end} | {e['at_hours'] for e in ledger if start < e['at_hours'] < end})
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        active = {}
        for event in sorted(ledger, key=lambda e: e['at_hours']):
            if event['at_hours'] > left:
                break
            if event.get('exposure_kind')=='procedure_event':continue
            target = event['target']
            if event['operation'] == 'stop':
                active.pop(target, None)
            elif event['operation'] in ('start', 'adjust'):
                previous = active.get(target, {'started_at': event['at_hours'], 'parameters': {}})
                active[target] = {'target': target, 'started_at': previous['started_at'],
                                  'parameters': {**previous['parameters'], **event.get('parameters', {})}}
        result.append({'from_hours': left, 'to_hours': right,
                       'active_treatments': [{**a, 'elapsed_at_start_hours': left-a['started_at']} for a in active.values()]})
    return result


class LLMDynamics:
    def __init__(self, client):
        self.client = client
        self._cache = {}
        self._lock = RLock()

    def transition(self, state, context, ledger, end, *, branch_id, mode, scenario=0, visible_history=()):
        start = state['clock_hours']
        if end < start:
            raise DynamicsError('模拟时间不能倒退。')
        if end == start:
            return copy.deepcopy(state)
        source_state = state
        state = copy.deepcopy(state)
        payload = dynamics_payload(state, context, ledger, end, mode=mode, scenario=scenario,
                                   visible_history=visible_history)
        # Identity belongs to the controller; the LLM only returns lab values.
        identity = {'branch_id': branch_id, 'state_version': state['version'], 'payload': payload}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        labels = metric_labels(state['labs'])
        with self._lock:
            future = self._cache.get(digest)
            owner = future is None
            if owner:
                future = self._cache[digest] = Future()
        if not owner:
            result = copy.deepcopy(future.result())
            if source_state != state:
                raise DynamicsError('预测期间患者基状态已改变，请重新预测。')
            return result
        try:
            def validate(reply):
                if set(reply.root) != set(labels.values()):
                    raise DynamicsError('动力学响应必须完整覆盖同一患者指标集合。')
                for key, label in labels.items():
                    fields = prediction_fields(reply.root[label])
                    value = fields['bound'] if fields['comparator'] else fields['value']
                    if value is not None and (value < 0 and not state['labs'][key].get('allows_negative') or key == 'ph' and value > 14 or key in ('spo2','sao2') and value > 100):
                        raise DynamicsError('动力学输出不能按正常范围裁剪，当前数值不符合表示约束。')
            reply = self.client.request(DYNAMICS_SYSTEM_PROMPT, payload, TransitionReply,
                                        operation='dreamer_dynamics_'+mode, validator=validate)
            validate(reply)
            if source_state != state:
                raise DynamicsError('预测期间患者基状态已改变，请重新预测。')
            result = copy.deepcopy(state)
            result.update(clock_hours=end, version=state['version']+1)
            for key, label in labels.items():
                value = reply.root[label]
                result['labs'][key].update(**prediction_fields(value), as_of_hours=end,
                    origin='llm_prediction', support='unsupported' if value is None else 'medical_prior',
                    uncertainty='high', prediction_method='conditional_llm_not_calibrated', driver_event_ids=[])
            future.set_result(result)
            return copy.deepcopy(result)
        except BaseException as exc:
            future.set_exception(exc)
            # Failures do not commit state or poison retry identities permanently.
            with self._lock:
                self._cache.pop(digest, None)
            raise
