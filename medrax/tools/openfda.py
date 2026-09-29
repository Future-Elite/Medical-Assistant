"""Official drug-label evidence from the openFDA API."""

import os
from typing import Any, Dict, List, Literal, Optional, Tuple, Type

import requests
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from .evidence import EvidenceAggregator


class OpenFDASearchInput(BaseModel):
    drug_name: str = Field(..., min_length=2, description="Generic or brand drug name")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum labels")


class OpenFDADrugLabelTool(BaseTool):
    """Search official FDA prescribing-label content."""

    name: str = "search_openfda_drug_labels"
    description: str = (
        "Search official openFDA drug labels. Use for FDA-approved dosage and "
        "administration, contraindications, boxed warnings, adverse reactions, "
        "drug interactions, and indications. Input a generic or brand drug name."
    )
    args_schema: Type[BaseModel] = OpenFDASearchInput
    response_format: Literal["content_and_artifact"] = "content_and_artifact"
    base_url: str = "https://api.fda.gov/drug/label.json"
    timeout: float = 20.0

    _sections = (
        "indications_and_usage",
        "dosage_and_administration",
        "contraindications",
        "boxed_warning",
        "warnings",
        "adverse_reactions",
        "drug_interactions",
    )

    @staticmethod
    def _first(values: Any, default: str = "") -> str:
        if isinstance(values, list) and values:
            return str(values[0])
        return str(values or default)

    def _normalize(self, labels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records = []
        for label in labels:
            openfda = label.get("openfda", {})
            set_id = str(label.get("set_id") or "").strip()
            spl_id = str(label.get("id") or "").strip()
            identifier = set_id or spl_id
            if not identifier:
                continue
            drug_name = self._first(
                openfda.get("brand_name"), self._first(openfda.get("generic_name"), "Drug")
            )
            sections = {
                section: "\n".join(label.get(section, []))[:4000]
                for section in self._sections
                if label.get(section)
            }
            effective_time = str(label.get("effective_time") or "")
            if len(effective_time) == 8 and effective_time.isdigit():
                effective_time = (
                    f"{effective_time[:4]}-{effective_time[4:6]}-{effective_time[6:]}"
                )
            records.append({
                "source_type": "openfda",
                "source_id": f"FDA_SET_ID:{set_id}" if set_id else f"SPL_ID:{spl_id}",
                "title": f"{drug_name} Drug Label",
                "content": "\n\n".join(
                    f"{section}: {text}" for section, text in sections.items()
                ),
                "date": effective_time,
                "url": f"https://api.fda.gov/drug/label.json?search=set_id:{set_id}"
                if set_id else self.base_url,
                "metadata": {
                    "sections": list(sections),
                    "spl_id": spl_id or None,
                    "generic_name": openfda.get("generic_name", []),
                    "brand_name": openfda.get("brand_name", []),
                },
            })
        return EvidenceAggregator.aggregate(records)

    def _run(
        self,
        drug_name: str,
        max_results: int = 5,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        value = drug_name.strip().replace('"', r'\"')
        search = (
            f'openfda.generic_name:"{value}" OR '
            f'openfda.brand_name:"{value}" OR openfda.substance_name:"{value}"'
        )
        params = {"search": search, "limit": max_results}
        if api_key := os.getenv("OPENFDA_API_KEY"):
            params["api_key"] = api_key
        try:
            response = requests.get(self.base_url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            artifact = {
                "query": drug_name.strip(),
                "count": 0,
                "evidence": [],
                "error": str(exc),
            }
            return "openFDA is currently unreachable; no label evidence was retrieved.", artifact
        if response.status_code == 404:
            artifact = {"query": drug_name.strip(), "count": 0, "evidence": []}
            return "No openFDA drug labels were found for this drug.", artifact
        response.raise_for_status()
        evidence = self._normalize(response.json().get("results", []))
        artifact = {"query": drug_name.strip(), "count": len(evidence), "evidence": evidence}
        if not evidence:
            return "openFDA returned no usable drug-label records.", artifact
        return EvidenceAggregator.format_for_model(evidence), artifact
