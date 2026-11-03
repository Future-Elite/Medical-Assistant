"""Build an aggregate workflow profile from training groups only."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrworld.io import output_path
from ehrdreamer.learning.runtime import get_engine
from ehrdreamer.workflow import build_profile

dataset=json.loads(output_path('outputs/dreamer_ml/dataset.json').read_text(encoding='utf-8'))
engine=get_engine()
profile=build_profile(dataset,engine.model_id)
output_path('outputs/dreamer_ml/model/workflow_profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(profile,ensure_ascii=True))
