"""Fixture HTTP and mapping checks; they do not assert Qwen or medical correctness."""

from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
import urllib.error
import urllib.request

from contracts.agent_his_adapter import AgentHisClient
from rag.his import filter_snapshot, snapshot_from_dict
from tests.mock_his_server import load_routes, make_handler


class HisAgentCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(load_routes()))
        cls.worker = Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.client = AgentHisClient(base_url=cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def snapshot(self, patient, cutoff):
        payload = self.client.snapshot_payload(patient, cutoff)
        return filter_snapshot(snapshot_from_dict(payload), cutoff)

    def test_normal_case_hides_future_creatinine(self):
        snapshot = self.snapshot("2001", "2025-12-31")
        self.assertEqual(len(snapshot.facts), 12)
        self.assertEqual(snapshot.suppressed_future, 1)
        potassium = [fact.fields["resultValue"] for fact in snapshot.facts if fact.fields.get("itemCode") == "K"]
        self.assertEqual(potassium, ["3.4", "3.9"])
        self.assertFalse(any(fact.fields.get("itemCode") == "Cr" for fact in snapshot.facts))

    def test_event_cutoff_and_ongoing_order(self):
        earlier = self.snapshot("2001", "2025-03-02")
        later = self.snapshot("2001", "2025-03-03")
        self.assertEqual((len(earlier.facts), earlier.suppressed_future), (9, 4))
        self.assertEqual((len(later.facts), later.suppressed_future), (10, 3))
        self.assertTrue(any(fact.event_type == "ORDER" and fact.end is None for fact in earlier.facts))
        self.assertFalse(any(fact.event_type == "DISCHARGE" for fact in earlier.facts))

    def test_missing_fields_and_unknown_patient(self):
        snapshot = self.snapshot("2002", "2025-12-31")
        self.assertEqual(len(snapshot.facts), 4)
        self.assertFalse(any(fact.event_type in {"LAB_TEST", "VITAL_SIGN"} for fact in snapshot.facts))
        prescription = next(fact for fact in snapshot.facts if fact.event_type == "PRESCRIPTION")
        self.assertNotIn("dosage", prescription.fields["items"][0])
        with self.assertRaises(ValueError):
            self.client.snapshot_payload("999999", "2025-12-31")

    def test_mock_rejects_writes(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(urllib.request.Request(self.url + "/api/patients", data=b"{}", method="POST"))
        self.assertEqual(caught.exception.code, 405)

    @unittest.expectedFailure
    def test_historical_admission_must_not_expose_future_discharge_fields(self):
        snapshot = self.snapshot("2001", "2025-03-02")
        admission = next(fact for fact in snapshot.facts if fact.event_type == "ADMISSION")
        # Known gap: event-level as_of filtering cannot reconstruct historical
        # field values from an admission entity containing later discharge data.
        self.assertNotIn("dischargeTime", admission.fields)


if __name__ == "__main__":
    unittest.main()
