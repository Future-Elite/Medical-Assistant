"""Teacher-free value rollouts on held-out groups; recorded actions remain inputs."""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrdreamer.learning.runtime import get_engine
from ehrdreamer.learning.schema import FEATURES,METRICS
from ehrdreamer.learning.train import matrix,regression_scores
from ehrworld.io import output_path


def main():
    dataset=json.loads(output_path('outputs/dreamer_ml/dataset.json').read_text(encoding='utf-8'))
    engine=get_engine();sequences=defaultdict(list)
    for row in dataset['transitions']:
        if row['split']=='test':sequences[(row['group'],row['provenance']['row'])].append(row)
    states=[]
    for rows in sequences.values():
        rows.sort(key=lambda r:r['provenance']['from_day'])
        first=rows[0]
        values={m:first['x'][FEATURES.index('previous_'+m)] for m in METRICS}
        ages={m:first['x'][FEATURES.index('previous_age_'+m)] for m in METRICS}
        states.append({'rows':rows[:3],'values':values,'baseline':dict(values),'ages':ages})
    results={}
    with threadpool_limits(limits=2):
        for step in range(3):
            active=[s for s in states if len(s['rows'])>step and (step==0 or s['rows'][step]['provenance']['from_day']==s['rows'][step-1]['provenance']['to_day'])]
            xs=[]
            for state in active:
                x=list(state['rows'][step]['x'])
                for metric in METRICS:
                    x[FEATURES.index('previous_'+metric)]=state['values'][metric]
                    x[FEATURES.index('previous_age_'+metric)]=state['ages'][metric]
                xs.append({'x':x})
            scores={}
            for metric,head in engine.bundle['transition'].items():
                indices=[i for i,s in enumerate(active) if s['values'][metric] is not None]
                if not indices:continue
                deltas=head['estimator'].predict(matrix([xs[i] for i in indices]))
                actual,predicted,baseline=[],[],[]
                for index,delta in zip(indices,deltas):
                    state=active[index];row=state['rows'][step]
                    value=min(head['observed_max'],max(head['observed_min'],state['values'][metric]+float(delta)))
                    state['values'][metric]=value;state['ages'][metric]=0
                    if metric in row['y']:
                        actual.append(row['y'][metric]);predicted.append(value);baseline.append(state['baseline'][metric])
                if actual:
                    scores[metric]={'learned':regression_scores(actual,predicted),'initial_value_persistence':regression_scores(actual,baseline)}
            results[str(step+1)]={'encounters':len(active),'metrics':scores}
            for state in active:
                delta=state['rows'][step]['x'][FEATURES.index('delta_hours')]
                for metric,age in state['ages'].items():
                    if metric not in engine.bundle['transition'] and age is not None:
                        state['ages'][metric]=age+delta
    report={'model_id':engine.model_id,'split':'test','group_overlap':0,'stochastic_sampling':False,
            'future_observations_as_inputs':False,'actions':'recorded date-range covariates, not a causal estimate',
            'horizon':'first 1/2/3 observed follow-up intervals; calendar-day spacing varies','steps':results}
    output_path('outputs/dreamer_ml/model/rollout_evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'model_id':engine.model_id,'encounters':[results[str(i)]['encounters'] for i in (1,2,3)]}))


if __name__=='__main__':main()
