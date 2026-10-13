"""Check the configured HIS endpoint; own only a mock process started here."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

DEFAULT_HIS_URL = "http://127.0.0.1:18080"


def probe_his(base_url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base_url.rstrip("/") + "/api/patients?page=1&size=1", timeout=2) as response:
        result = json.load(response)
    if result.get("code") != 0 or not isinstance(result.get("data"), dict):
        raise ValueError("HIS patient-list endpoint returned an invalid envelope")


@contextmanager
def managed_his_service(base_url=None):
    url = (base_url or os.getenv("HIS_BASE_URL") or DEFAULT_HIS_URL).rstrip("/")
    os.environ["HIS_BASE_URL"] = url
    process = None
    handle = None
    try:
        try:
            probe_his(url)
        except Exception as exc:
            if url != DEFAULT_HIS_URL:
                raise RuntimeError(f"Configured HIS is unavailable at {url}. Start that service or use {DEFAULT_HIS_URL} for synthetic fixtures.") from exc
            root = Path(__file__).resolve().parents[2]
            log = root / "agent/logs/his_mock.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            handle = log.open("a", encoding="utf-8")
            print(f"Starting read-only synthetic HIS mock at {url}...", flush=True)
            process = subprocess.Popen(
                [sys.executable, str(root / "tests/mock_his_server.py"), "--port", "18080"],
                stdout=handle, stderr=handle,
            )
            deadline = time.monotonic() + 8
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"HIS mock failed to start; inspect {log}")
                try:
                    probe_his(url)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"HIS mock startup timed out; inspect {log}")
                    time.sleep(0.1)
        print(f"HIS ready: {url}", flush=True)
        yield
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if handle is not None:
            handle.close()
