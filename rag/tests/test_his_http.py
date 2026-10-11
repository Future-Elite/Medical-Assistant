"""Read-only HIS HTTP adapter: mapping, identifier stripping, errors, time window.

Offline and deterministic. Responses are canned JSON dicts (via the transport
seam) or served by a local throwaway HTTP server; nothing here touches a real
HIS, MySQL or an external network.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rag.his_http import DEFAULT_HIS_BASE_URL, HISHttpError, HisHttpAdapter

PATIENT = {
    "id": 2001,
    "name": "演示患者A",
    "gender": 1,
    "birthDate": "1958-04-12",
    "idCard": "110101195804120011",
    "phone": "13800000000",
    "address": "合成地址（非真实）",
    "createdAt": "2024-01-05T09:00:00",
}

VISIT = {"id": 3101, "patientId": 2001, "visitNo": "V20250301", "department": "心内科", "visitTime": "2025-03-01T08:30:00"}
DIAGNOSIS = {"id": 4101, "patientId": 2001, "visitId": 3101, "icdCode": "I10", "diagnosisName": "原发性高血压", "diagnosedAt": "2025-03-01T09:10:00"}
OUTPATIENT_LAB = {"id": 5101, "patientId": 2001, "visitId": 3101, "admissionId": None, "itemCode": "K", "itemName": "血钾", "resultValue": "3.4", "unit": "mmol/L", "testedAt": "2025-03-02T07:15:00"}
FUTURE_LAB = {"id": 5102, "patientId": 2001, "admissionId": None, "itemCode": "Cr", "itemName": "肌酐", "resultValue": "88", "testedAt": "2026-01-20T07:15:00"}
ADMISSION = {
    "id": 7101, "patientId": 2001, "admissionNo": "A20250301", "admissionTime": "2025-03-01T10:00:00",
    "dischargeTime": "2025-03-08T09:30:00", "dischargeDiagnosis": "原发性高血压 3级 高危",
}
ORDER = {"id": 6101, "patientId": 2001, "admissionId": 7101, "itemName": "硝苯地平控释片", "startTime": "2025-03-01T11:00:00", "stopTime": None}


def envelope(data):
    return {"code": 0, "message": "success", "data": data}


def canned(patient=None, events=None, admission_events=None):
    """Build a transport seam plus the list of URLs it was asked for."""
    calls: list[str] = []
    table = {
        f"{DEFAULT_HIS_BASE_URL}/api/patients/2001": envelope(PATIENT if patient is None else patient),
        f"{DEFAULT_HIS_BASE_URL}/api/patients/2001/timeline": envelope({"patientId": 2001, "events": events if events is not None else []}),
        f"{DEFAULT_HIS_BASE_URL}/api/admissions/7101/timeline": envelope({"patientId": 2001, "events": admission_events if admission_events is not None else []}),
    }

    def transport(url: str, timeout: float):
        calls.append(url)
        if url not in table:
            raise HISHttpError(f"unexpected url {url}", stage="test")
        return table[url]

    return transport, calls


def facts_by_path(snapshot):
    return {fact.record_path: fact for fact in snapshot.facts}


class MappingTests(unittest.TestCase):
    def test_events_map_to_patient_facts(self):
        transport, calls = canned(
            events=[
                {"type": "VISIT", "time": VISIT["visitTime"], "data": VISIT},
                {"type": "DIAGNOSIS", "time": DIAGNOSIS["diagnosedAt"], "data": DIAGNOSIS},
                {"type": "LAB_TEST", "time": OUTPATIENT_LAB["testedAt"], "data": OUTPATIENT_LAB},
            ]
        )
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        by_path = facts_by_path(snapshot)
        self.assertEqual(
            list(by_path),
            ["his:patient", "his:timeline:VISIT:1", "his:timeline:DIAGNOSIS:1", "his:timeline:LAB_TEST:1"],
        )
        diagnosis = by_path["his:timeline:DIAGNOSIS:1"]
        self.assertEqual((diagnosis.collection, diagnosis.event_type), ("diagnosis", "DIAGNOSIS"))
        self.assertEqual(diagnosis.time, "2025-03-01T09:10:00")
        self.assertEqual(diagnosis.fields["diagnosisName"], "原发性高血压")
        self.assertEqual(diagnosis.fields["visitId"], 3101)
        self.assertEqual(snapshot.source, "his_demo")
        self.assertEqual(snapshot.warnings, ())
        self.assertEqual(len(calls), 2)  # patient + patient timeline (no admissions in this set)

    def test_identifiers_never_enter_the_snapshot(self):
        transport, _ = canned(events=[{"type": "VISIT", "time": VISIT["visitTime"], "data": VISIT}])
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        blob = json.dumps([dict(fact.fields) for fact in snapshot.facts], ensure_ascii=False)
        for leaked in ("演示患者A", "110101195804120011", "13800000000", "合成地址", "patientId"):
            self.assertNotIn(leaked, blob)
        self.assertEqual(facts_by_path(snapshot)["his:patient"].fields, {"gender": 1, "birthDate": "1958-04-12"})

    def test_range_events_carry_start_and_end(self):
        transport, _ = canned(
            events=[{"type": "ADMISSION", "time": ADMISSION["admissionTime"], "data": ADMISSION}],
            admission_events=[{"type": "ORDER", "time": ORDER["startTime"], "data": ORDER}],
        )
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        by_path = facts_by_path(snapshot)
        admission = by_path["his:timeline:ADMISSION:1"]
        self.assertEqual((admission.start, admission.end), ("2025-03-01T10:00:00", "2025-03-08T09:30:00"))
        order = by_path["his:timeline:ORDER:1"]
        self.assertEqual((order.start, order.end), ("2025-03-01T11:00:00", None))  # 进行中：无 stopTime

    def test_future_facts_are_suppressed_and_counted(self):
        transport, _ = canned(
            events=[
                {"type": "LAB_TEST", "time": OUTPATIENT_LAB["testedAt"], "data": OUTPATIENT_LAB},
                {"type": "LAB_TEST", "time": FUTURE_LAB["testedAt"], "data": FUTURE_LAB},
            ]
        )
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual(snapshot.suppressed_future, 1)
        visible = {fact.record_path for fact in snapshot.facts}
        self.assertIn("his:timeline:LAB_TEST:1", visible)
        self.assertNotIn("his:timeline:LAB_TEST:2", visible)

    def test_empty_timeline_yields_patient_fact_only(self):
        transport, _ = canned()
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual([fact.record_path for fact in snapshot.facts], ["his:patient"])

    def test_missing_payload_and_missing_type_are_warned_not_dropped(self):
        transport, _ = canned(events=[{"type": "CHARGE", "time": "2025-03-08T10:00:00", "data": None}, {"time": "2025-03-08T11:00:00", "data": {"amount": 1}}])
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual(facts_by_path(snapshot)["his:timeline:CHARGE:1"].fields, {})
        self.assertIn("his:timeline:UNKNOWN:1", facts_by_path(snapshot))
        self.assertTrue(any("缺少实体载荷" in warning for warning in snapshot.warnings))
        self.assertTrue(any("没有 type" in warning for warning in snapshot.warnings))

    def test_non_object_payload_keeps_raw_value(self):
        transport, _ = canned(events=[{"type": "VITAL_SIGN", "time": "2025-03-02T06:30:00", "data": "36.6"}])
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual(facts_by_path(snapshot)["his:timeline:VITAL_SIGN:1"].fields, {"value": "36.6"})


class AdmissionTimelineTests(unittest.TestCase):
    def test_admission_feed_is_fetched_once_per_admission(self):
        admission_events = [{"type": "ORDER", "time": ORDER["startTime"], "data": ORDER}]
        transport, calls = canned(
            events=[
                {"type": "ADMISSION", "time": ADMISSION["admissionTime"], "data": ADMISSION},
                {"type": "DISCHARGE", "time": ADMISSION["dischargeTime"], "data": ADMISSION},
            ],
            admission_events=admission_events,
        )
        snapshot = HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        admission_calls = [url for url in calls if "/api/admissions/" in url]
        self.assertEqual(admission_calls, [f"{DEFAULT_HIS_BASE_URL}/api/admissions/7101/timeline"])
        self.assertIn("his:timeline:ORDER:1", facts_by_path(snapshot))

    def test_admission_feed_for_another_patient_is_ignored(self):
        transport, _ = canned(
            events=[{"type": "ADMISSION", "time": ADMISSION["admissionTime"], "data": ADMISSION}],
            admission_events=[{"type": "ORDER", "time": ORDER["startTime"], "data": ORDER}],
        )
        table_calls = []

        def leaky(url: str, timeout: float):
            payload = transport(url, timeout)
            table_calls.append(url)
            if "/api/admissions/" in url:
                return envelope({"patientId": 9999, "events": [{"type": "ORDER", "time": ORDER["startTime"], "data": ORDER}]})
            return payload

        snapshot = HisHttpAdapter(transport=leaky).get_snapshot("2001", "2025-12-31")
        self.assertNotIn("his:timeline:ORDER:1", facts_by_path(snapshot))
        self.assertTrue(any("与本快照患者" in warning for warning in snapshot.warnings))

    def test_admission_feed_can_be_disabled(self):
        transport, calls = canned(
            events=[{"type": "ADMISSION", "time": ADMISSION["admissionTime"], "data": ADMISSION}],
            admission_events=[{"type": "ORDER", "time": ORDER["startTime"], "data": ORDER}],
        )
        snapshot = HisHttpAdapter(transport=transport, include_admission_timelines=False).get_snapshot("2001", "2025-12-31")
        self.assertEqual([url for url in calls if "/api/admissions/" in url], [])
        self.assertNotIn("his:timeline:ORDER:1", facts_by_path(snapshot))


class ErrorTests(unittest.TestCase):
    def test_unknown_patient_raises_value_error(self):
        transport, _ = canned(patient={})
        with self.assertRaises(ValueError) as ctx:
            HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertIn("unknown patient_ref: 2001", str(ctx.exception))

    def test_business_error_code_becomes_his_http_error(self):
        def transport(url: str, timeout: float):
            return {"code": 1, "message": "患者不存在", "data": None}

        with self.assertRaises(HISHttpError) as ctx:
            HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual(ctx.exception.stage, "patient")
        self.assertIn("code=1", str(ctx.exception))

    def test_missing_envelope_is_rejected(self):
        def transport(url: str, timeout: float):
            return {"data": PATIENT}

        with self.assertRaises(HISHttpError) as ctx:
            HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertIn("envelope", str(ctx.exception))

    def test_non_array_events_are_rejected(self):
        transport, _ = canned(events="not-a-list")
        with self.assertRaises(HISHttpError) as ctx:
            HisHttpAdapter(transport=transport).get_snapshot("2001", "2025-12-31")
        self.assertEqual(ctx.exception.stage, "timeline")

    def test_invalid_as_of_is_rejected(self):
        transport, _ = canned()
        with self.assertRaises(ValueError):
            HisHttpAdapter(transport=transport).get_snapshot("2001", "2025/12/31")


class LocalHttpTransportTests(unittest.TestCase):
    """Exercise the real urllib transport against a throwaway local server."""

    @classmethod
    def setUpClass(cls):
        routes = {
            "/api/patients/2001": envelope(PATIENT),
            "/api/patients/2001/timeline": envelope(
                {"patientId": 2001, "events": [{"type": "DIAGNOSIS", "time": DIAGNOSIS["diagnosedAt"], "data": DIAGNOSIS}]}
            ),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                payload = routes.get(self.path)
                if payload is None:
                    self.send_error(404)
                    return
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802 - must never be reached by the adapter
                self.send_error(405)

            def log_message(self, *args):  # keep test output clean
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_default_transport_reads_snapshot_over_http(self):
        snapshot = HisHttpAdapter(base_url=self.base_url).get_snapshot("2001", "2025-12-31")
        self.assertEqual(facts_by_path(snapshot)["his:timeline:DIAGNOSIS:1"].fields["diagnosisName"], "原发性高血压")

    def test_http_error_is_wrapped_with_status(self):
        with self.assertRaises(HISHttpError) as ctx:
            HisHttpAdapter(base_url=self.base_url).get_snapshot("404404", "2025-12-31")
        self.assertEqual((ctx.exception.stage, ctx.exception.status_code), ("http", 404))
        self.assertFalse(ctx.exception.retryable)

    def test_unknown_patient_on_live_transport_is_value_error(self):
        class EmptyHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps(envelope(None)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), EmptyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(ValueError):
                HisHttpAdapter(base_url=f"http://127.0.0.1:{server.server_address[1]}").get_snapshot("2001", "2025-12-31")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
