"""Send a tiny synthetic probe; never submit workbook rows or print credentials."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from ehrworld.io import output_path
from ehrworld.llm import APIConfig, JSONClient, ModelResponseError
from ehrworld.model_policy import MODEL_NAMES


class Probe(BaseModel):
    ok: bool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODEL_NAMES, default='Qwen3.8-27B')
    args = parser.parse_args()
    config = replace(APIConfig.from_env(), model=args.model, timeout=12, max_tokens=32, repair_attempts=0)
    client = JSONClient(config)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'model': config.model,
              'endpoint': config.base_url, 'patient_data_sent': False}
    try:
        result = client.request('Return JSON only.', {'message': 'Return an object with ok=true.'},
                                Probe, operation='dreamer_connectivity_probe')
        report.update(status='ok', ok=result.ok)
    except ModelResponseError as exc:
        report.update(status='failed', reason=str(exc))
    finally:
        report['usage'] = client.statistics()
        client.close()
    target = output_path('outputs/dreamer_validation/api_probe.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'usage'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
