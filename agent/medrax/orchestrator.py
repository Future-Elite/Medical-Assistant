"""Bounded Medical Agent loop shared by the UI and non-UI callers."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from threading import RLock, Lock

from rag.audit import parse_citation_ids
from medrax.answer_audit import AnswerAuditModule
from medrax.medical_session import ACTIVE_TURN, BudgetExceeded, MedicalSession, TurnContext


class MedicalAgentOrchestrator:
    def __init__(self, workflow, rag_tool, *, max_revisions=2, max_rounds=4):
        self.workflow = workflow
        self.rag_tool = rag_tool
        self.sessions = {}
        self.audit_path = Path("logs/evidence_audit.jsonl")
        self.max_revisions = max_revisions
        self.max_rounds = max_rounds
        self._lock = RLock()
        self._log_lock = Lock()

    def reset(self, thread_id):
        with self._lock:
            self.sessions.pop(thread_id, None)

    def _generate(self, prompt, thread_id, turn):
        draft = None
        for event in self.workflow.stream(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}}, stream_mode="updates",
        ):
            model = event.get("model") or event.get("process")
            if not model:
                continue
            message = model["messages"][-1]
            if not getattr(message, "tool_calls", None) and message.content:
                draft = str(message.content)
        if not draft:
            raise RuntimeError("Agent produced no answer draft")
        return AnswerAuditModule.normalize_citations(draft)

    def _audit_scoped(self, session, retrieval_id, answer, claim_records=None):
        records = claim_records or []
        grouped = {}
        for index, record in enumerate(records):
            for citation_id in record["citation_ids"]:
                owner = citation_id.rsplit(":", 1)[0]
                if owner not in session.retrievals or citation_id not in session.citation_map:
                    raise ValueError("Citation is outside the active patient's conversation scope")
                per_package = grouped.setdefault(owner, {})
                local = per_package.setdefault(index, {**record, "citation_ids": []})
                local["citation_ids"].append(citation_id)
        rows = [{**record, "claim": record["claim_en"], "verifier_claim": record["claim_en"], "valid_citation_ids": [],
                 "invalid_citation_ids": [], "citation_checks": [], "ragtruth_label": "not_checked"}
                for record in records]
        available = True
        warnings = []
        for owner, local in grouped.items():
            result = self.rag_tool.audit_grounded_answer(owner, answer, claim_records=list(local.values()))
            if len(result.get("claims", [])) != len(local):
                raise ValueError("Scoped auditor returned an incomplete result set")
            available = available and bool(result.get("verifier", {}).get("available"))
            warnings.extend(result.get("warnings", []))
            for index, checked in zip(local, result["claims"]):
                rows[index]["valid_citation_ids"].extend(checked.get("valid_citation_ids", []))
                rows[index]["invalid_citation_ids"].extend(checked.get("invalid_citation_ids", []))
                rows[index]["citation_checks"].extend(checked.get("citation_checks", []))
                if checked.get("ragtruth_label") == "supported":
                    rows[index]["ragtruth_label"] = "supported"
                elif rows[index]["ragtruth_label"] != "supported":
                    rows[index]["ragtruth_label"] = checked.get("ragtruth_label", "not_checked")
        return {"claims": rows, "verifier": {"name": "minicheck", "available": available},
                "retrieval_ids": list(grouped), "warnings": warnings}

    def run_turn(self, question, thread_id, *, patient_ref="", as_of=None, emit=None):
        # Serialize turns in this prototype: one cached RAG/MiniCheck instance serves
        # all calls; no two questions may mutate the same session concurrently.
        with self._lock:
            return self._run_turn(question, thread_id, patient_ref, as_of, emit)

    def _run_turn(self, question, thread_id, patient_ref, as_of, listener):
        cutoff = str(as_of or date.today().isoformat())
        date.fromisoformat(cutoff)
        selected = str(patient_ref or "").strip()
        if selected and not selected.isdigit():
            raise ValueError("Select a synthetic HIS patient using its integer ID")
        session = self.sessions.get(thread_id)
        if session is None or (session.patient_ref, session.as_of) != (selected, cutoff):
            session = MedicalSession(patient_ref=selected, as_of=cutoff)
            self.sessions[thread_id] = session
        def send(event):
            row = {"timestamp": datetime.now(timezone.utc).isoformat(), "thread_id": thread_id,
                   "turn_id": turn.turn_id, "patient_scope": session.generation, **event}
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock:
                with self.audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if listener:
                listener(row)
        turn = TurnContext(session, question, send)
        token = ACTIVE_TURN.set(turn)
        auditor = AnswerAuditModule(
            lambda *args, **kwargs: self._audit_scoped(session, *args, **kwargs),
            self.rag_tool.translate_claims, max_revisions=self.max_revisions,
        )
        graph_thread = f"{thread_id}:{session.generation}"
        revisions = 0
        prompt = (
            f"Original user question: {question}\n"
            f"Caller-selected synthetic patient: {selected or 'none'}; as_of: {cutoff}.\n"
            "Read HIS if patient facts are needed. Ask the user when a required patient or "
            "clinical detail is missing. Inspect RAG gaps and warnings; you may retrieve again "
            "or open citations before drafting. Preserve the user's answer language."
        )
        try:
            for round_index in range(self.max_rounds):
                send({"event": "agent_round", "round": round_index, "tool_call_counts": dict(turn.counts)})
                before = set(session.retrievals)
                errors_before = len(turn.retrieval_errors)
                draft = self._generate(prompt, graph_thread, turn)
                send({"event": "answer_draft", "attempt": revisions, "draft": draft})
                new_packages = [session.retrievals[key] for key in set(session.retrievals) - before]
                usable = [item for package in new_packages for item in package.get("evidence", [])
                          if item.get("metadata", {}).get("content_mode") != "metadata_only"]
                if (new_packages and not usable) or len(turn.retrieval_errors) > errors_before:
                    if not parse_citation_ids(draft) and ("?" in draft or "？" in draft):
                        return self._finish(draft, turn, send, "clarification")
                    if turn.counts.get("retrieve_medical_evidence", 0) < 3 and round_index + 1 < self.max_rounds:
                        prompt = "The latest retrieval returned no usable substantive external evidence (empty or metadata-only). Reassess the gap: adjust configured retrieval/source parameters and retrieve again, or ask the user for reviewed English terms. Do not invent citations."
                        send({"event": "evidence_gap", "reason": "empty_or_metadata_only"})
                        continue
                    return self._finish("信息不足：检索未获得可引用的外部证据，请补充问题或审核后的英文医学术语。", turn, send, "insufficient_evidence")
                ids = parse_citation_ids(draft)
                if selected and session.snapshot is None and not ("?" in draft or "？" in draft):
                    return self._finish("信息不足：尚未获得所选患者的 HIS 快照，请检查 HIS 连接或补充患者信息。", turn, send, "missing_patient_facts")
                unknown = set(ids) - set(session.citation_map)
                if unknown:
                    result = {"passed": False, "status": "failed", "failure_reasons": ["Unknown or cross-patient citation IDs: " + ", ".join(sorted(unknown))]}
                else:
                    retrieval_id = ids[0].rsplit(":", 1)[0] if ids else (next(iter(session.retrievals), None))
                    # A clarification with no cited claims may be returned without a
                    # grounding audit; this is not a citation-completeness validator.
                    if not ids and ("?" in draft or "？" in draft):
                        retrieval_id = None
                    result = auditor.audit(draft, retrieval_id, lambda stage: send({"event": "audit_stage", "stage": stage}))
                send({"event": "answer_audit", "attempt": revisions, **result})
                if result.get("passed"):
                    output = auditor.render_public_citations(draft, session.citation_map)
                    from interface import ChatInterface
                    mentioned = ChatInterface._extract_citation_ids(output)
                    allowed = ChatInterface._extract_citation_ids(" ".join(session.citation_map.values()))
                    if mentioned - allowed:
                        return self._finish("⚠️ 回答包含未检索验证的公开引用，已拦截。", turn, send, "invalid_citation")
                    return self._finish(output, turn, send, "passed")
                if result.get("status") == "error":
                    return self._finish("⚠️ 翻译或审计执行异常，已拦截，请检查日志后重试。", turn, send, "audit_error")
                if revisions >= self.max_revisions:
                    return self._finish("⚠️ 回答草稿经过两次修改后仍未通过证据支持性审计，已拦截，请转人工复核。", turn, send, "blocked")
                revisions += 1
                prompt = auditor.revision_prompt(result, revisions)
                if turn.counts.get("retrieve_medical_evidence", 0) < 3:
                    prompt = prompt.replace("Do not call tools.", "If support is missing, you may retrieve new evidence or open a citation within the remaining budget; otherwise revise from existing evidence.")
                prompt += " Keep the original user's answer language."
            return self._finish("⚠️ 已达到本轮编排上限，仍无法形成可验证回答，请补充信息或转人工复核。", turn, send, "budget_exhausted")
        except BudgetExceeded as exc:
            send({"event": "budget_exhausted", "reason": str(exc)})
            return self._finish("⚠️ 本轮工具调用预算已耗尽，请补充信息后重试。", turn, send, "budget_exhausted")
        except Exception as exc:
            send({"event": "agent_error", "reason": f"{type(exc).__name__}: {exc}"})
            return self._finish("⚠️ Agent 执行异常，未输出未经审计的草稿，请检查日志。", turn, send, "error")
        finally:
            ACTIVE_TURN.reset(token)

    @staticmethod
    def _finish(answer, turn, send, status):
        send({"event": "final_response", "status": status, "tool_call_counts": dict(turn.counts)})
        return {"answer": answer, "status": status, "turn_id": turn.turn_id}
