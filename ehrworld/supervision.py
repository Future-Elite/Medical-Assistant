"""Export causal inquiry supervision for inspecting the data paradigm; no training."""
from __future__ import annotations

import json
from typing import Iterable

from .prompts import SIMULATE_SYSTEM
from .schema import Episode
from .simulator import canonical_actions


def causal_samples(episodes: Iterable[Episode]):
    for episode in episodes:
        for index, step in enumerate(episode.steps):
            inquiry = [e for e in step.events if e.action.kind == "inquiry"]
            if not inquiry:
                # The intervention still occurs in all following histories.
                continue
            payload = {"physiological_timestamp": step.timestamp.isoformat(),
                       "static_context": episode.context.model_dump(mode="json", by_alias=True),
                       "history": [s.history_dict() for s in episode.steps[:index]],
                       "concurrent_actions": [a.model_dump(mode="json") for a in canonical_actions(tuple(e.action for e in step.events))]}
            target = {"outcomes": [{"action_id": e.action.action_id, "value": e.value, "unit": e.action.unit}
                                   for e in sorted(inquiry, key=lambda e: e.action.action_id)]}
            yield {"subject_id": episode.subject_id, "episode_id": episode.episode_id,
                   "timestamp": step.timestamp.isoformat(),
                   "messages": [{"role": "system", "content": SIMULATE_SYSTEM},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                                {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}],
                   "loss_policy": "assistant inquiry outcomes only; mask system/user/past history; no intervention value target",
                   "inquiry_action_ids": [e.action.action_id for e in inquiry]}
