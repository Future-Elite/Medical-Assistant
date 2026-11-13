"""Draft long-horizon research reward, not an empirically validated clinical utility."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from ehrdreamer.dual.references import score


class RewardUnavailable(RuntimeError):
    """Environment/reference failures must stop scoring, not penalize the policy."""


@dataclass(frozen=True)
class RewardConfig:
    horizon_hours: float = 72
    checkpoint_hours: float = 24
    shaping_cap: float = .49
    burden_weight: float = .7
    rebound_weight: float = .3

    def __post_init__(self):
        values=asdict(self)
        if not all(math.isfinite(v) for v in values.values()):raise ValueError('Finite reward settings required')
        if self.horizon_hours<=0 or self.checkpoint_hours<=0 or not 0<=self.shaping_cap<1:
            raise ValueError('Invalid horizon or shaping cap')
        if min(self.burden_weight,self.rebound_weight)<0 or not math.isclose(self.burden_weight+self.rebound_weight,1):
            raise ValueError('Shaping weights must be nonnegative and sum to one')


def _measure(labs,rules):
    result=score(labs,rules)
    if not result['complete']:raise RewardUnavailable('Missing, ambiguous or unscorable fixed-cohort laboratory result')
    for key,rule in rules.items():
        item=labs[key]
        if item.get('ambiguous_same_day'):raise RewardUnavailable('Within-day order cannot be resolved')
        for name in ('value','bound'):
            value=item.get(name)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise RewardUnavailable('Invalid laboratory representation')
        if item['unit']!=rule['unit']:raise RewardUnavailable('Reference unit mismatch')
    return result


def score_trajectory(initial_labs,rules,snapshots,*,safety_passed,config=RewardConfig()):
    """Snapshots must come from a trusted environment, never from policy JSON.

    Exact checkpoints are required, so shortening a rollout or omitting an adverse
    measurement cannot improve reward. Clinical safety review is a separate gate.
    """
    if safety_passed is None:raise RewardUnavailable('A trusted clinical-safety evaluation is required')
    if type(safety_passed) is not bool:raise RewardUnavailable('Invalid safety evaluation')
    if not safety_passed:
        return {'score':-1.,'safety_rejected':True,'reward_version':'long-horizon-1.0'}
    initial=_measure(initial_labs,rules);m=len(rules)
    if not snapshots:raise RewardUnavailable('No independent environment rollout supplied')
    times=[s['time_hours'] for s in snapshots]
    if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in times):
        raise RewardUnavailable('Invalid rollout clock')
    if times!=sorted(set(times)) or times[0]<=0 or times[-1]!=config.horizon_hours:
        raise RewardUnavailable('Rollout must reach the common horizon exactly once')
    if any(b-a>config.checkpoint_hours+1e-9 for a,b in zip([0]+times,times)):
        raise RewardUnavailable('Long-horizon checkpoint coverage is incomplete')
    required=[config.checkpoint_hours*i for i in range(1,int(config.horizon_hours//config.checkpoint_hours)+1)]
    if any(not any(math.isclose(t,x,abs_tol=1e-8) for x in times) for t in required):
        raise RewardUnavailable('A fixed checkpoint is missing')
    measurements=[initial]
    for snapshot in snapshots:
        for key in rules:
            item=snapshot.get('labs',{}).get(key,{})
            if item.get('as_of_hours')!=snapshot['time_hours']:
                raise RewardUnavailable('A stale result cannot masquerade as a fresh checkpoint')
        measurements.append(_measure(snapshot['labs'],rules))
    counts=[r['abnormal_count'] for r in measurements]
    grid=[0]+times
    burden=sum((b-a)*(x+y)/2 for a,b,x,y in zip(grid,grid[1:],counts,counts[1:]))/(config.horizon_hours*m)
    rebounded=set()
    for key in rules:
        was_normal=measurements[0]['statuses'][key]=='normal'
        for measurement in measurements[1:]:
            status=measurement['statuses'][key]
            if was_normal and status in ('high','low'):rebounded.add(key)
            was_normal=was_normal or status=='normal'
    rebound=len(rebounded)/m
    shaping=config.shaping_cap*(config.burden_weight*(1-burden)+config.rebound_weight*(1-rebound))
    result=(m-counts[-1]+shaping)/(m+1)
    return {'score':result,'terminal_abnormal_count':counts[-1], 'metric_count':m,
            'normalized_abnormal_time_burden':burden,'new_or_rebound_fraction':rebound,
            'shaping':shaping,'safety_rejected':False,'reward_version':'long-horizon-1.0'}
