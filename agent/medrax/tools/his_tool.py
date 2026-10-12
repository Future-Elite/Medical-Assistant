"""Read-only HIS tool. Patient selection comes from the caller, never the model."""

import json
import os
from typing import Literal, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from contracts.agent_his_adapter import AgentHisClient
from rag.his import filter_snapshot, snapshot_from_dict
from medrax.medical_session import require_turn


class SnapshotInput(BaseModel):
    pass


class PatientSnapshotTool(BaseTool):
    name: str = "get_patient_snapshot"
    description: str = (
        "Read the caller-selected synthetic patient's HIS snapshot, including outpatient "
        "and admission timelines. Use for current patient facts, medication history, "
        "laboratory results, and vitals. If no patient is selected, ask the user to select "
        "one; never guess a patient ID. No arguments or write operations are accepted."
    )
    args_schema: Type[BaseModel] = SnapshotInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"

    def _run(self):
        turn = require_turn()
        turn.consume(self.name)
        session = turn.session
        if not session.patient_ref:
            return "No patient selected. Ask the user to select a synthetic patient.", {"error": "patient_required"}
        try:
            payload = AgentHisClient(base_url=os.getenv("HIS_BASE_URL", "http://127.0.0.1:8080")).snapshot_payload(
                session.patient_ref, session.as_of
            )
            # RAG owns the single visibility implementation. Keep the fetched payload
            # for RAG, but expose only the same filtered view to the language model.
            visible = filter_snapshot(snapshot_from_dict(payload), session.as_of)
            session.snapshot = payload
            facts = [{
                "record_path": fact.record_path, "fields": dict(fact.fields),
                "time": fact.time, "start": fact.start, "end": fact.end,
            } for fact in visible.facts]
            artifact = {
                "patient_ref": session.patient_ref, "as_of": session.as_of,
                "facts": facts, "warnings": list(visible.warnings),
                "suppressed_future": visible.suppressed_future,
            }
            turn.emit({"event": "his_snapshot", "artifact": artifact, "tool_name": self.name})
            return json.dumps(artifact, ensure_ascii=False), artifact
        except Exception as exc:
            artifact = {"error": type(exc).__name__, "message": str(exc)}
            turn.emit({"event": "his_error", "artifact": artifact, "tool_name": self.name})
            return "HIS unavailable; patient facts are not available. Ask the user or report the gap.", artifact
