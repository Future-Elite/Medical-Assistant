"""Download the public BEIR SciFact release for local evaluation.

The dataset is not committed: it is third-party research data.  This script
downloads the official BEIR archive into a caller-selected directory and
verifies the expected files.

Usage::

    python3 tools/fetch_scifact.py --output data/datasets/scifact
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
EXPECTED = ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "datasets" / "scifact")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args(argv)
    target = args.output.expanduser()
    target.mkdir(parents=True, exist_ok=True)
    print(f"downloading {args.url}")
    request = urllib.request.Request(args.url, headers={"User-Agent": "ChinaLLM-research/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(target)
    missing = [name for name in EXPECTED if not (target / name).exists()]
    if missing:
        print("missing expected files: " + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"ready: {target}")
    for name in EXPECTED:
        print(f"  {name}: {(target / name).stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
