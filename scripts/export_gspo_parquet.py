"""Convert GSPO CSV JSON columns to native nested verl Parquet; no training."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrdreamer.rl_data.verl_adapter import export_parquet

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',default='data/gspo_2024/policy_gspo.csv')
    parser.add_argument('--output-dir',default='data/gspo_2024/verl')
    args=parser.parse_args()
    print(json.dumps(export_parquet(args.csv,args.output_dir)))
