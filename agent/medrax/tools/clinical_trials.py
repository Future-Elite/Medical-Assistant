"""Registered trial evidence from the ClinicalTrials.gov API v2."""

from typing import Any, Dict, List, Literal, Optional, Tuple, Type

import requests
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from .evidence import EvidenceAggregator


class ClinicalTrialsSearchInput(BaseModel):
    query: str = Field(..., min_length=2, description="Condition, drug, or trial query")
    max_results: int = Field(default=8, ge=1, le=20, description="Maximum trials")


class ClinicalTrialsTool(BaseTool):
    """Search registered studies on ClinicalTrials.gov."""

    name: str = "search_clinical_trials"
    description: str = (
        "Search ClinicalTrials.gov registered studies. Use for ongoing or completed "
        "trials, recruitment status, trial phase, interventions, study design, and "
        "eligibility criteria. Input a condition, treatment, or trial question."
    )
    args_schema: Type[BaseModel] = ClinicalTrialsSearchInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"
    base_url: str = "https://clinicaltrials.gov/api/v2/studies"
    timeout: float = 20.0

    @staticmethod
    def _normalize(studies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records = []
        for study in studies:
            protocol = study.get("protocolSection", {})
            identification = protocol.get("identificationModule", {})
            status = protocol.get("statusModule", {})
            design = protocol.get("designModule", {})
            description = protocol.get("descriptionModule", {})
            conditions = protocol.get("conditionsModule", {}).get("conditions", [])
            interventions = protocol.get("armsInterventionsModule", {}).get(
                "interventions", []
            )
            eligibility = protocol.get("eligibilityModule", {})
            nct_id = str(identification.get("nctId") or "").upper()
            if not nct_id:
                continue
            intervention_names = [
                item.get("name") for item in interventions if item.get("name")
            ]
            content_parts = [
                description.get("briefSummary", ""),
                f"Conditions: {', '.join(conditions)}" if conditions else "",
                f"Interventions: {', '.join(intervention_names)}"
                if intervention_names else "",
                f"Eligibility: {eligibility.get('eligibilityCriteria', '')}",
            ]
            phases = design.get("phases", [])
            records.append({
                "source_type": "clinicaltrials",
                "source_id": nct_id,
                "title": identification.get("briefTitle") or identification.get(
                    "officialTitle", "Clinical trial"
                ),
                "content": "\n".join(part for part in content_parts if part)[:6000],
                "date": status.get("lastUpdateSubmitDate") or status.get(
                    "studyFirstSubmitDate", ""
                ),
                "url": f"https://clinicaltrials.gov/study/{nct_id}",
                "metadata": {
                    "status": status.get("overallStatus"),
                    "phase": phases[0] if len(phases) == 1 else phases,
                    "interventions": intervention_names,
                    "conditions": conditions,
                    "study_type": design.get("studyType"),
                },
            })
        return EvidenceAggregator.aggregate(records)

    def _run(
        self,
        query: str,
        max_results: int = 8,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        search_query = query.strip()
        try:
            response = requests.get(
                self.base_url,
                params={
                    "query.term": search_query,
                    "pageSize": max_results,
                    "format": "json",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            artifact = {
                "query": search_query,
                "count": 0,
                "evidence": [],
                "error": str(exc),
            }
            return (
                "ClinicalTrials.gov is currently unreachable; no trial evidence was retrieved.",
                artifact,
            )
        response.raise_for_status()
        evidence = self._normalize(response.json().get("studies", []))
        artifact = {"query": search_query, "count": len(evidence), "evidence": evidence}
        if not evidence:
            return "No ClinicalTrials.gov studies were found for this query.", artifact
        return EvidenceAggregator.format_for_model(evidence), artifact
