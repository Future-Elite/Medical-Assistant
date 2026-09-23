#!/usr/bin/env bash
set -euo pipefail

cd /share/home/u16050/apps/XiaoYing/projects/MedicalAgent
export MEDRAX_AGENT_MODE=pubmed

exec /share/home/u16050/.conda/envs/medrax/bin/python main.py