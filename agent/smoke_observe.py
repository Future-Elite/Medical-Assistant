"""Manual live smoke: synthetic HIS + online RAG + Qwen, then a follow-up."""
import json
import os
from pathlib import Path
from uuid import uuid4
import importlib.util
import urllib.request

os.environ.setdefault("ANSWER_AUDIT_MODE", "observe")
os.environ.setdefault("MINICHECK_CACHE_DIR", "/media/hdd2/xiaoying/model-weights/minicheck")
os.environ.setdefault("HIS_BASE_URL", "http://127.0.0.1:18080")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from main import initialize_medical_agent
from medrax.his_service import managed_his_service

run_id = "observe-smoke-" + uuid4().hex

def emit(row):
    if row["event"] in {"his_snapshot", "evidence_search", "answer_audit", "final_response", "agent_error"}:
        artifact = row.get("artifact", {})
        print(json.dumps({"event": row["event"], "status": row.get("status"),
                          "reason": row.get("reason"), "passed": row.get("passed"),
                          "documents": len(artifact.get("ranked_documents", [])),
                          "tool_call_counts": row.get("tool_call_counts")}, ensure_ascii=False), flush=True)

with managed_his_service():
    # Verify the running service matches the repository's synthetic-only fixtures
    # before any external model request; never send unknown HIS data in this smoke.
    spec = importlib.util.spec_from_file_location("synthetic_mock", "../tests/mock_his_server.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    routes = fixture.load_routes()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for route in ("/api/patients/2001", "/api/patients/2001/timeline", "/api/admissions/7101/timeline"):
        with opener.open(os.environ["HIS_BASE_URL"] + route, timeout=5) as response:
            if json.load(response) != routes[route]:
                raise SystemExit("HIS response differs from synthetic fixtures; no external call allowed")
    print("Verified: all patient 2001 routes match synthetic-only repository fixtures", flush=True)
    controller, _ = initialize_medical_agent("medrax/docs/system_prompts.txt")
    controller.audit_path = Path("logs") / (run_id + ".jsonl")
    questions = [
        "请读取所选演示患者最新的诊断、用药记录、血压和血钾，检索几篇有关成人高血压管理的文献。请按患者记录、检索证据、结合说明与信息缺口组织回答，不给出个体化处方，简洁回答。",
        "沿用上一轮患者记录和已经返回的文献，不重新检索，请简要说明患者记录里哪些信息还需要更新，以及现有文献的适用限制。",
    ]
    for question in questions:
        result = controller.run_turn(question, run_id, patient_ref="2001", emit=emit)
        output = Path("logs") / (result["turn_id"] + "-observe-smoke.txt")
        output.write_text(result["answer"], encoding="utf-8")
        print(json.dumps({"thread_id": run_id, "turn_id": result["turn_id"], "status": result["status"],
                          "answer_file": str(output), "answer_chars": len(result["answer"]),
                          "audit_log": str(controller.audit_path)}, ensure_ascii=False), flush=True)
        if result["status"] != "observed":
            raise SystemExit("Live smoke did not return an observe-mode answer")
