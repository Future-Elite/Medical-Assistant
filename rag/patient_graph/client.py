"""Small stdlib HTTP client for the read-only patient-graph service.

External EHR integrations can copy this class or import it when the RAG package
is available on the same Python path.  It sends only the fields the service
needs and never sends a chart narrative or identifier beyond ``patient_ref``.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib import error, request


class PatientGraphClientError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class PatientGraphClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8798", *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def build(
        self,
        *,
        patient_ref: str,
        ehr_view: Mapping[str, Any] | None = None,
        patient_snapshot: Mapping[str, Any] | None = None,
        as_of: str | None = None,
        start_time: str | None = None,
    ) -> dict[str, Any]:
        payload = self._payload(
            patient_ref=patient_ref, ehr_view=ehr_view,
            patient_snapshot=patient_snapshot, as_of=as_of, start_time=start_time,
        )
        return self._request("POST", "/api/patient-graph", payload)["data"]

    def search(
        self,
        *,
        patient_ref: str,
        queries: Sequence[str],
        ehr_view: Mapping[str, Any] | None = None,
        patient_snapshot: Mapping[str, Any] | None = None,
        as_of: str | None = None,
        start_time: str | None = None,
        top_k: int = 10,
        max_hops: int = 2,
        max_nodes: int = 240,
    ) -> dict[str, Any]:
        payload = self._payload(
            patient_ref=patient_ref, ehr_view=ehr_view,
            patient_snapshot=patient_snapshot, as_of=as_of, start_time=start_time,
        )
        payload.update({
            "queries": [str(item) for item in queries],
            "top_k": int(top_k), "max_hops": int(max_hops), "max_nodes": int(max_nodes),
        })
        return self._request("POST", "/api/patient-graph/search", payload)["data"]

    @staticmethod
    def _payload(
        *,
        patient_ref: str,
        ehr_view: Mapping[str, Any] | None,
        patient_snapshot: Mapping[str, Any] | None,
        as_of: str | None,
        start_time: str | None,
    ) -> dict[str, Any]:
        if ehr_view is not None:
            payload: dict[str, Any] = {"patient_ref": str(patient_ref), "ehr_view": dict(ehr_view)}
            if as_of:
                payload["as_of"] = as_of
            if start_time:
                payload["start_time"] = start_time
            return payload
        if patient_snapshot is not None:
            return {"patient_snapshot": dict(patient_snapshot)}
        raise ValueError("ehr_view or patient_snapshot is required")

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            raise PatientGraphClientError(
                f"patient graph service returned HTTP {exc.code}", status=exc.code, body=detail,
            ) from exc
        except error.URLError as exc:
            raise PatientGraphClientError(f"patient graph service unavailable: {exc.reason}") from exc
