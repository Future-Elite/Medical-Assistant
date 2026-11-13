"""Build four CSV SFT datasets only. Never starts model inference or training."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrdreamer.rl_data.dynamics_sft import build_sft

if __name__=='__main__':print(json.dumps(build_sft(),ensure_ascii=False,indent=2))
