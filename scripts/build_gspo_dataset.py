"""Build CSV and optionally verl Parquet, offline. This script never trains."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ehrdreamer.rl_data.dataset import build_dataset

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook',default='2024带孕周的出院小结汇总.xlsx')
    parser.add_argument('--output-dir',default='data/gspo_2024')
    parser.add_argument('--max-prompt-chars',type=int,default=60000)
    parser.add_argument('--no-parquet',action='store_true')
    args=parser.parse_args()
    report=build_dataset(args.workbook,args.output_dir,max_prompt_chars=args.max_prompt_chars,parquet=not args.no_parquet)
    print(json.dumps({k:report[k] for k in ('data_version','patients','rl_eligible_patients','eligible_split_counts',
        'lab_observations','treatment_mentions','observational_transitions','training_executed')},ensure_ascii=False,indent=2))
