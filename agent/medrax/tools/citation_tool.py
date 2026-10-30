"""Open only citations belonging to the active patient's conversation scope."""

import json
from typing import Literal, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from medrax.medical_session import require_turn
from .rag_tool import _get_rag


class CitationInput(BaseModel):
    retrieval_id: str = Field(..., description="ID of a retrieval returned in this conversation")
    citation_id: str = Field(..., description="Complete citation ID from that retrieval")


class OpenMedicalCitationTool(BaseTool):
    name: str = "open_medical_citation"
    description: str = (
        "Open a retrieved citation's quote, key evidence sentences, version and locator. "
        "Use when more detail is needed before answering. This opens retained evidence; "
        "it does not fetch a full paper or guideline. Only active-session citations are allowed."
    )
    args_schema: Type[BaseModel] = CitationInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"

    def _run(self, retrieval_id: str, citation_id: str):
        turn = require_turn()
        turn.consume(self.name)
        allowed_retrievals = (set(turn.current_retrieval_ids) if turn.retrieval_attempts
                              else set(turn.session.retrievals))
        if retrieval_id not in allowed_retrievals:
            raise ValueError("retrieval_id is outside the current answer evidence scope")
        if citation_id not in turn.answer_citation_map or not citation_id.startswith(retrieval_id + ":"):
            raise ValueError("citation_id does not belong to the current answer evidence scope")
        result = _get_rag().open_citation({"retrieval_id": retrieval_id, "citation_id": citation_id})
        turn.emit({"event": "citation_opened", "artifact": result, "tool_name": self.name})
        return json.dumps(result, ensure_ascii=False), result
