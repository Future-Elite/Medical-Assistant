from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from ehrworld.llm import APIConfig, ContextLimitError, JSONClient, ModelResponseError
from ehrworld.simulator import APIPredictor, PatientSimulator
from .models import GenerationRequest
from .scenarios import DemoPredictor, action_plan, patient_context
from .store import JobStore, now
from .guidelines import GuidelineSourceError, guidance_for, manifest
from .physician import plan_step
from ehrworld.model_policy import thinking_policy


def default_predictor(request):
    if request.backend == "demo":
        return DemoPredictor(request), None
    client = JSONClient(replace(APIConfig.from_env(), model=request.model))
    if request.scenario_id.startswith("neonatal_"):
        conditioning = {"type": "source_initial_state_with_simulation_assumptions" if request.source_seed else "user_supplied_known_initial_state",
                        **request.initial_state.model_dump()}
        if request.source_seed:
            conditioning.update(observed_fields=request.source_seed['observed_fields'], simulation_assumptions=request.source_seed['assumptions'])
        return APIPredictor(client, clinical_guidance=guidance_for(request.scenario_id), initial_conditioning=conditioning), client
    return APIPredictor(client), client


def public_error(exc):
    if isinstance(exc, GuidelineSourceError):
        return {"code": "guideline_source_changed", "message": "本地指南缺失或内容已变化，需要复核并更新适配规则后再生成。", "retryable": False}
    if isinstance(exc, ModelResponseError):
        if "HTTP 403" in str(exc):
            return {"code": "upstream_forbidden", "message": "模型网关返回 403，当前网络环境或访问权限不受支持。请检查后重试，或选择离线演示。", "retryable": True}
        return {"code": "upstream_error", "message": "模型请求失败或输出格式不符合要求。已保留完成的步骤，可重新生成。", "retryable": True}
    if isinstance(exc, ContextLimitError):
        return {"code": "context_limit", "message": "患者历史超出模型输入上限，请缩短患者资料后重试。", "retryable": False}
    if isinstance(exc, ValueError):
        return {"code": "configuration_error", "message": "模型配置或生成数据无效，请检查服务端 .env 与输入资料。", "retryable": False}
    return {"code": "generation_error", "message": "生成任务未能完成，请检查服务日志或重新生成。", "retryable": True}


class GenerationService:
    def __init__(self, store: JobStore, predictor_factory=default_predictor):
        self.store = store
        self.predictor_factory = predictor_factory
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ehrworld")
        self.capacity = threading.BoundedSemaphore(6)
        self.batch_capacity = threading.BoundedSemaphore(1)

    def new_job(self, request, **metadata):
        identifier = str(uuid.uuid4())
        context = patient_context(request)
        job = {"id": identifier, "status": "queued", "created_at": now(), "updated_at": now(),
               "request": request.model_dump(mode="json"), "patient_id": f"SYN-{identifier[:8].upper()}",
               "total_steps": request.steps, "completed_steps": 0, "error": None, "usage": {},
               "requested_model": request.model, "thinking_policy": thinking_policy(request.model), **metadata,
               "trajectory": {"context": context.model_dump(mode="json"), "steps": []}}
        if request.scenario_id.startswith("neonatal_"):
            job["trajectory"]["guidelines"] = manifest()
            job["trajectory"]["initial_state"] = request.initial_state.model_dump()
            job["trajectory"]["record_schema"] = "neonatal-ehr-v2"
        if request.source_seed:
            job['trajectory']['source_seed'] = request.source_seed
        return job

    def submit(self, request: GenerationRequest, previous=None):
        if not self.capacity.acquire(blocking=False):
            return None
        metadata = {k: previous[k] for k in ('batch_id','batch_index','length_group') if k in previous} if previous else {}
        job = self.new_job(request, **metadata)
        try:
            if metadata:
                self.store.replace_batch_job(metadata['batch_id'], previous['id'], job)
            else:
                self.store.save(job)
            self.pool.submit(self._run, job['id'], request)
        except Exception:
            self.capacity.release()
            raise
        return job

    def submit_batch(self, body, catalog):
        source, children = catalog.plan(body)
        if not self.batch_capacity.acquire(blocking=False):
            return None
        batch_id = str(uuid.uuid4())
        jobs = [self.new_job(c['request'], batch_id=batch_id, batch_index=c['index'], length_group=c['length_group']) for c in children]
        batch = {'id': batch_id, 'created_at': now(), 'request': body.model_dump(mode='json'),
                 'source': source, 'distribution': {'short':3,'medium':5,'long':2}, 'job_ids':[j['id'] for j in jobs]}
        try:
            self.store.create_batch(batch, jobs)
        except Exception:
            self.batch_capacity.release()
            raise
        lock = threading.Lock()
        remaining = [10]
        def finished(_):
            with lock:
                remaining[0] -= 1
                if remaining[0] == 0: self.batch_capacity.release()
        for job, child in zip(jobs, children):
            self.pool.submit(self._run, job['id'], child['request'], False).add_done_callback(finished)
        return self.batch_summary(batch)

    def batch_summary(self, batch):
        jobs = self.store.batch_jobs(batch)
        completed = sum(j['status']=='completed' for j in jobs)
        failed = sum(j['status']=='failed' for j in jobs)
        status = ('running' if any(j['status']=='running' for j in jobs) else 'queued') if any(j['status'] in ('queued','running') for j in jobs) else 'completed' if completed==10 else 'failed' if failed==10 else 'completed_with_errors'
        return {**batch, 'status':status,'total_patients':10,'completed_patients':completed,'failed_patients':failed,
                'total_steps':sum(j['total_steps'] for j in jobs),'completed_steps':sum(j['completed_steps'] for j in jobs),
                'patients':[{k:v for k,v in j.items() if k not in ('trajectory','usage')} for j in jobs]}

    def _run(self, identifier, request, capacity_reserved=True):
        client = None
        job = self.store.get(identifier)
        try:
            job["status"] = "running"
            self.store.save(job)
            if request.scenario_id.startswith("neonatal_") and not all(s["available"] and s["reviewed"] for s in job["trajectory"]["guidelines"]["sources"]):
                raise GuidelineSourceError("Required reviewed local guidelines unavailable or changed")
            predictor, client = self.predictor_factory(request)
            job["model"] = predictor.name
            simulator = PatientSimulator(patient_context(request), request.start_time, predictor)
            for index in range(request.steps):
                physician_plan = plan_step(request, simulator.state, index) if request.scenario_id.startswith("neonatal_") else None
                timestamp, actions = (physician_plan.timestamp, physician_plan.actions) if physician_plan else action_plan(request)[index]
                step = simulator.step(timestamp, actions)
                record = step.model_dump(mode="json")
                if physician_plan:
                    record["physician_record"] = physician_plan.record
                job["trajectory"]["steps"].append(record)
                job["completed_steps"] = len(job["trajectory"]["steps"])
                if client:
                    job["usage"] = client.statistics()
                self.store.save(job)
            job["status"] = "completed"
        except Exception as exc:
            # Do not persist exception bodies or prompts: gateways can echo credentials.
            job["status"] = "failed"
            job["error"] = public_error(exc)
        finally:
            try:
                if client:
                    job["usage"] = client.statistics()
                    client.close()
            finally:
                try:
                    self.store.save(job)
                finally:
                    if capacity_reserved: self.capacity.release()

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=False)
