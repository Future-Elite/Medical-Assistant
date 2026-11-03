"""Train EHR Dreamer from real project sources, with grouped held-out evaluation."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ehrworld.io import output_path
from ehrdreamer.learning.dataset import build_dataset
from ehrdreamer.learning.train import train
from ehrdreamer.learning.schema import FEATURES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-dataset',action='store_true')
    parser.add_argument('--output',default='outputs/dreamer_ml/model')
    args = parser.parse_args()
    target = output_path('outputs/dreamer_ml/dataset.json')
    if args.reuse_dataset and target.is_file():
        dataset = json.loads(target.read_text(encoding='utf-8'))
        if dataset.get('feature_names')!=FEATURES:
            raise SystemExit('Cached extraction schema changed; run without --reuse-dataset.')
    else:
        dataset = build_dataset()
        target.write_text(json.dumps(dataset,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    report = train(dataset,args.output)
    from ehrdreamer.workflow import build_profile
    profile=build_profile(dataset,report['artifact_sha256'][:16])
    (output_path(args.output)/'workflow_profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'trained','output':args.output,'artifact_sha256':report['artifact_sha256'],
                      'groups':report['group_counts'],'rows':report['dataset_counts']},ensure_ascii=True))


if __name__ == '__main__':
    main()
