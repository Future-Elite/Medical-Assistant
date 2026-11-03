"""Run from the project root: python -m examples.interactive_simulation.

This sends only artificial data. Set EHRWORLD_API_KEY in the project .env.
"""
from datetime import datetime, timedelta

from ehrworld.data import construct_episode
from ehrworld.demo import synthetic_admissions
from ehrworld.io import write_json
from ehrworld.llm import APIConfig, JSONClient
from ehrworld.schema import Action, PatientState
from ehrworld.simulator import APIPredictor, PatientSimulator


def main():
    episode, _ = construct_episode(next(synthetic_admissions()))
    client = JSONClient(APIConfig.from_env())
    try:
        simulator = PatientSimulator(episode.context, episode.admitted_at, APIPredictor(client))
        first = simulator.step(episode.admitted_at, tuple(e.action for e in episode.steps[0].events))
        # New interventions are allowed: actions need not come from a prerecorded trajectory.
        intervention = Action(action_id="user-intervention-1", kind="intervention",
                              name="Synthetic glucose-lowering treatment", concept="med:synthetic", source="custom",
                              parameters={"route": "synthetic demonstration"})
        simulator.step(episode.admitted_at + timedelta(hours=1), (intervention,))
        checkpoint = write_json("outputs/interactive/checkpoint.json", simulator.state)
        restored = PatientSimulator.restore(PatientState.model_validate_json(checkpoint.read_text(encoding="utf-8")), simulator.predictor)
        branch = restored.fork()
        action = first.events[0].action.model_copy(update={"action_id": "user-recheck-1"})
        observed = branch.step(episode.admitted_at + timedelta(hours=8), (action,))
        write_json("outputs/interactive/branch_result.json", observed)
        print("Saved checkpoint and branch result in outputs/interactive/")
    finally:
        client.close()


if __name__ == "__main__":
    main()
