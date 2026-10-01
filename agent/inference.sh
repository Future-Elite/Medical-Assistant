#!/usr/bin/env bash
set -euo pipefail

cd /media/hdd2/xiaoying/projects/MedicalAgent
export MEDRAX_AGENT_MODE=medical_evidence

exec /media/hdd2/xiaoying/conda_envs/medrax/bin/python main.py
