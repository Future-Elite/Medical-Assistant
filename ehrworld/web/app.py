from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ehrworld.io import ROOT
from ehrworld.llm import APIConfig
from .models import GenerationRequest, BatchRequest
from .seeds import SeedCatalog, SeedDataError
from ehrworld.model_policy import model_catalog, thinking_policy
from .scenarios import SCENARIOS, action_plan
from .service import GenerationService, default_predictor
from .store import JobStore
from .guidelines import manifest, source_path
from ehrdreamer.source import SourceError


def create_app(db_path: str | Path = "outputs/web/ehrworld.sqlite", predictor_factory=default_predictor, seed_path=None,
               dreamer_source_path=None, dreamer_models_factory=None, dreamer_legacy_ml=False):
    if dreamer_legacy_ml:
        from ehrdreamer.api import router as dreamer_router
        from ehrdreamer.service import DreamerService
        from ehrdreamer.source import SourceCatalog
        from ehrdreamer.models import Models as DreamerModels
    else:
        from ehrdreamer.dual.api import router as dreamer_router
        from ehrdreamer.dual.service import DreamerService
        from ehrdreamer.dual.source import BaselineCatalog as SourceCatalog
        from ehrdreamer.dual.models import DualModels as DreamerModels
    dreamer_models_factory = dreamer_models_factory or DreamerModels
    @asynccontextmanager
    async def lifespan(app):
        store = JobStore(db_path)
        store.interrupt_unfinished()
        app.state.store = store
        app.state.service = GenerationService(store, predictor_factory)
        app.state.seeds = SeedCatalog(seed_path)
        dreamer_path = Path('outputs/dreamer/jobs.sqlite') if str(db_path)=='outputs/web/ehrworld.sqlite' else Path(db_path).with_name(Path(db_path).stem+'-dreamer.sqlite')
        dreamer_store = JobStore(dreamer_path)
        dreamer_store.interrupt_unfinished()
        source_archive = dreamer_path.with_name('source.sqlite')
        app.state.dreamer = DreamerService(dreamer_store, SourceCatalog(dreamer_source_path, source_archive), dreamer_models_factory)
        yield
        app.state.service.close()
        app.state.dreamer.close()

    app = FastAPI(title="EHR Dreamer API", version="5.0.0" if dreamer_legacy_ml else "6.0.0",
                  description="双LLM策略规划与患者动力学、真实化验基线、时间账本及三阶段病历；兼容旧版EHRWorld接口。", lifespan=lifespan)
    app.include_router(dreamer_router)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": "请求参数不符合要求，请检查病例、模型、操作预算和时间设置。",
                                                       "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]})

    @app.exception_handler(SeedDataError)
    async def seed_error(request, exc):
        return JSONResponse(status_code=409 if '已变化' in str(exc) else 400, content={'detail':str(exc)})

    @app.exception_handler(SourceError)
    async def dreamer_source_error(request, exc):
        return JSONResponse(status_code=409 if '已变化' in str(exc) else 400, content={'detail':str(exc)})

    if dreamer_legacy_ml:
        from ehrdreamer.learning.runtime import ModelArtifactError
        @app.exception_handler(ModelArtifactError)
        async def trained_model_error(request, exc):
            return JSONResponse(status_code=503,content={'detail':str(exc)})

    @app.get('/api/models')
    def models():
        return model_catalog()

    @app.get('/api/seed-data')
    def seed_data(request: Request):
        return request.app.state.seeds.summary()

    @app.post('/api/batches/preview')
    def batch_preview(body: BatchRequest, request: Request):
        return request.app.state.seeds.preview(body)

    @app.post('/api/batches', status_code=202)
    def batch_create(body: BatchRequest, request: Request):
        batch = request.app.state.service.submit_batch(body, request.app.state.seeds)
        if batch is None: raise HTTPException(429, '已有批次正在生成，请等待完成后再提交。')
        return batch

    @app.get('/api/batches')
    def batches(request: Request, limit: int = Query(20, ge=1, le=100)):
        return {'items':[request.app.state.service.batch_summary(b) for b in request.app.state.store.list_batches(limit)]}

    def get_batch(request, identifier):
        batch = request.app.state.store.get_batch(str(identifier))
        if batch is None: raise HTTPException(404, '未找到该批次。')
        return request.app.state.service.batch_summary(batch)

    @app.get('/api/batches/{identifier}')
    def batch_detail(identifier: UUID, request: Request):
        return get_batch(request, identifier)

    @app.get('/api/batches/{identifier}/export')
    def batch_export(identifier: UUID, request: Request):
        batch = get_batch(request, identifier)
        if batch['status'] != 'completed': raise HTTPException(409, '只有10名患者全部完成后才能导出整批数据。')
        jobs = request.app.state.store.batch_jobs(batch)
        document = {'schema_version':'3.0','synthetic':True, **{k:v for k,v in batch.items() if k!='patients'},
                    'patients':[export_document(j) for j in jobs]}
        return Response(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="ehrworld-batch-{str(identifier)[:8]}.json"','Cache-Control':'no-store'})

    @app.get("/api/health")
    def health():
        try:
            config = APIConfig.from_env()
            configured, model = True, config.model
        except (ValueError, TypeError):
            configured, model = False, None
        return {"status": "ok", "api_configured": configured, "model": model, "steps": 10,
                "enable_thinking": thinking_policy(model or '')['enabled'],
                "remote_connectivity": "not_checked", "backend_modes": ["api", "demo"]}

    @app.get("/api/scenarios")
    def scenarios():
        return {"items": [s for k, s in SCENARIOS.items() if k.startswith("neonatal_")],
                "legacy_items": [s for k, s in SCENARIOS.items() if not k.startswith("neonatal_")]}

    @app.get("/api/guidelines")
    def guidelines():
        return manifest()

    @app.get("/api/guidelines/{source_id}/document")
    def guideline_document(source_id: str):
        try:
            path = source_path(source_id)
        except KeyError:
            raise HTTPException(404, "未找到该指南。") from None
        if not path.is_file():
            raise HTTPException(404, "本地指南文件缺失。")
        return FileResponse(path, media_type="application/pdf")

    @app.post("/api/plans/preview")
    def preview(body: GenerationRequest):
        if body.scenario_id.startswith("neonatal_"):
            from ehrworld.schema import PatientState
            from .physician import plan_step
            from .scenarios import patient_context
            state = PatientState(timestamp=body.start_time, context=patient_context(body))
            plans = [plan_step(body, state, i, preview=True) for i in range(body.steps)]
            return {"policy": "guideline-conditioned; later actions await generated history", "steps": [
                {"index": i+1, "timestamp": p.timestamp.isoformat(), "actions": [a.model_dump(mode="json") for a in p.actions],
                 "physician_record": p.record} for i, p in enumerate(plans)]}
        return {"steps": [{"index": i + 1, "timestamp": timestamp.isoformat(),
                           "actions": [a.model_dump(mode="json") for a in actions]}
                          for i, (timestamp, actions) in enumerate(action_plan(body))]}

    @app.post("/api/generations", status_code=202)
    def generate(body: GenerationRequest, request: Request):
        job = request.app.state.service.submit(body)
        if job is None:
            raise HTTPException(429, "生成队列已满，请稍后重试。")
        return job

    @app.get("/api/generations")
    def generations(request: Request, limit: int = Query(30, ge=1, le=100)):
        jobs = request.app.state.store.list(limit)
        return {"items": [{k: v for k, v in j.items() if k not in {"trajectory", "usage"}} for j in jobs]}

    def get_job(request, identifier):
        job = request.app.state.store.get(str(identifier))
        if job is None:
            raise HTTPException(404, "未找到该生成任务。")
        return job

    @app.get("/api/generations/{identifier}")
    def generation(identifier: UUID, request: Request):
        return get_job(request, identifier)

    @app.post("/api/generations/{identifier}/retry", status_code=202)
    def retry(identifier: UUID, request: Request):
        previous = get_job(request, identifier)
        if previous["status"] in {"running", "queued"}:
            raise HTTPException(409, "该任务仍在生成中。")
        try:
            job = request.app.state.service.submit(GenerationRequest.model_validate(previous["request"]), previous=previous)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if job is None:
            raise HTTPException(429, "生成队列已满，请稍后重试。")
        return job

    def export_document(job):
        return {"schema_version": "3.0", "synthetic": True, "generation_id": job["id"],
                "patient_id": job["patient_id"], "patient": job["request"]["patient"],
                "backend": job["request"]["backend"], "model": job.get("model"),
                "requested_model":job['request'].get('model'), 'thinking_policy':job.get('thinking_policy'),
                "step_count":job['total_steps'], **job["trajectory"]}

    @app.get("/api/generations/{identifier}/export")
    def export(identifier: UUID, request: Request, format: Literal["json", "csv"] = "json"):
        job = get_job(request, identifier)
        if job["status"] != "completed" or job["completed_steps"] != job['total_steps'] or len(job['trajectory']['steps']) != job['total_steps']:
            raise HTTPException(409, "只有达到该患者目标步数的完整轨迹可以导出。")
        filename = f"ehrworld-{str(identifier)[:8]}.{format}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"}
        if format == "json":
            document = export_document(job)
            return Response(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json", headers=headers)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["patient_id", "step", "timestamp", "action_id", "kind", "name", "concept", "value", "unit", "parameters",
                         "actor", "indication", "execution_status", "guideline_references", "decision_observation_cutoff"])
        def safe(value):
            # Untrusted generated labels must not become spreadsheet formulas.
            return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
        for index, step in enumerate(job["trajectory"]["steps"], 1):
            for event in step["events"]:
                a = event["action"]
                writer.writerow([safe(v) for v in [job["patient_id"], index, step["timestamp"], a["action_id"], a["kind"],
                                                   a["name"], a["concept"], event["value"], a["unit"], json.dumps(a["parameters"], ensure_ascii=False),
                                                   a["parameters"].get("actor", ""), a["parameters"].get("indication", ""),
                                                   "performed_in_simulation" if step.get("physician_record") else "",
                                                   json.dumps(a["parameters"].get("guideline_refs", []), ensure_ascii=False),
                                                   step.get("physician_record", {}).get("observation_cutoff")]])
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)

    # A production Vue build is served by FastAPI, so one local process is enough.
    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def index():
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "后端已就绪，请运行 frontend 的 npm run dev 或先执行 npm run build。", "docs": "/docs"})

    return app


app = create_app()
