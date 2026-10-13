#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
MEDRAX_PYTHON="${MEDRAX_PYTHON:-/media/hdd2/xiaoying/conda_envs/medrax/bin/python}"

if (( $# > 2 )); then
    echo "Usage: bash inference.sh [MiniCheck cache directory] [HIS base URL]" >&2
    exit 2
fi
export MINICHECK_CACHE_DIR="${1:-${MINICHECK_CACHE_DIR:-/media/hdd2/xiaoying/model-weights/minicheck}}"
if [[ ! -d "$MINICHECK_CACHE_DIR" ]]; then
    echo "MiniCheck cache directory does not exist: $MINICHECK_CACHE_DIR" >&2
    exit 2
fi
echo "MiniCheck cache: $MINICHECK_CACHE_DIR"
export HIS_BASE_URL="${2:-${HIS_BASE_URL:-http://127.0.0.1:18080}}"
echo "HIS endpoint: $HIS_BASE_URL"

cd "$SCRIPT_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export MEDRAX_AGENT_MODE=medical_evidence

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

exec "${MEDRAX_PYTHON:-python}" main.py
