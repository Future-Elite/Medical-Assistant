import unittest
from unittest.mock import Mock, patch

import requests

from medrax.tools.clinical_trials import ClinicalTrialsTool
from medrax.tools.evidence import EvidenceAggregator
from medrax.tools.openfda import OpenFDADrugLabelTool


class EvidenceToolsTest(unittest.TestCase):
    def test_aggregator_deduplicates_and_sorts(self):
        base = {
            "source_type": "pubmed",
            "title": "Title",
            "content": "Content",
            "url": "https://example.test",
            "metadata": {},
        }
        result = EvidenceAggregator.aggregate([
            {**base, "source_id": "PMID:1", "date": "2020"},
            {**base, "source_id": "PMID:2", "date": "2025"},
            {**base, "source_id": "PMID:1", "date": "2020"},
        ])
        self.assertEqual([item["source_id"] for item in result], ["PMID:2", "PMID:1"])

    @patch("medrax.tools.openfda.requests.get")
    def test_openfda_returns_standard_evidence(self, get):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [{
            "set_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "id": "11111111-2222-3333-4444-555555555555",
            "effective_time": "20250101",
            "openfda": {"brand_name": ["ExampleDrug"], "generic_name": ["example"]},
            "contraindications": ["Do not use when..."],
            "drug_interactions": ["Interaction text."],
        }]}
        get.return_value = response

        result = OpenFDADrugLabelTool().invoke({
            "name": "search_openfda_drug_labels",
            "args": {"drug_name": "example"},
            "id": "fda-call",
            "type": "tool_call",
        })
        evidence = result.artifact["evidence"][0]
        self.assertEqual(evidence["source_type"], "openfda")
        self.assertTrue(evidence["source_id"].startswith("FDA_SET_ID:"))
        self.assertEqual(evidence["date"], "2025-01-01")
        self.assertIn("drug_interactions", evidence["metadata"]["sections"])

    @patch(
        "medrax.tools.openfda.requests.get",
        side_effect=requests.ConnectionError("offline"),
    )
    def test_openfda_returns_auditable_network_failure(self, get):
        content, artifact = OpenFDADrugLabelTool()._run("example")
        self.assertIn("unreachable", content)
        self.assertEqual(artifact["evidence"], [])
        self.assertIn("offline", artifact["error"])

    @patch("medrax.tools.clinical_trials.requests.get")
    def test_trials_returns_standard_evidence(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"studies": [{"protocolSection": {
            "identificationModule": {
                "nctId": "NCT01234567",
                "briefTitle": "Example Trial",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "lastUpdateSubmitDate": "2025-01-01",
            },
            "designModule": {"phases": ["PHASE3"], "studyType": "INTERVENTIONAL"},
            "conditionsModule": {"conditions": ["Hypertension"]},
            "armsInterventionsModule": {
                "interventions": [{"name": "ExampleDrug"}]
            },
            "descriptionModule": {"briefSummary": "Trial summary."},
            "eligibilityModule": {"eligibilityCriteria": "Adults."},
        }}]}
        get.return_value = response

        result = ClinicalTrialsTool().invoke({
            "name": "search_clinical_trials",
            "args": {"query": "hypertension"},
            "id": "trial-call",
            "type": "tool_call",
        })
        evidence = result.artifact["evidence"][0]
        self.assertEqual(evidence["source_id"], "NCT01234567")
        self.assertEqual(evidence["metadata"]["status"], "RECRUITING")
        self.assertEqual(evidence["metadata"]["phase"], "PHASE3")


if __name__ == "__main__":
    unittest.main()
