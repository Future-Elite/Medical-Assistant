"""Agent-facing retrieval tools with retrieval-scoped citations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .audit import GroundedAnswerAuditor
from .models import EvidencePackage, RetrievalRequest
from .pipeline import EvidenceRetrievalPipeline


@dataclass
class EvidenceSessionStore:
    _packages: dict[str, EvidencePackage] = field(default_factory=dict)

    def put(self, package: EvidencePackage) -> None:
        self._packages[package.retrieval_id] = package

    def get(self, retrieval_id: str) -> EvidencePackage:
        try:
            return self._packages[retrieval_id]
        except KeyError:
            raise ValueError("unknown retrieval_id; retrieve evidence before opening a citation") from None


class EvidenceRAG:
    """Read-only tools; this class does not generate clinical decisions."""
    def __init__(self, pipeline: EvidenceRetrievalPipeline, sessions: EvidenceSessionStore | None = None, auditor: GroundedAnswerAuditor | None = None):
        self.pipeline = pipeline
        self.sessions = sessions or EvidenceSessionStore()
        # 默认只做引用结构审计；语义主张校验必须由调用方显式注入 verifier。
        self.auditor = auditor or GroundedAnswerAuditor()

    def retrieve_evidence(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        package = self.pipeline.retrieve(RetrievalRequest.from_dict(arguments))
        self.sessions.put(package)
        return package.to_dict()

    def open_citation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        package = self.sessions.get(str(arguments.get("retrieval_id") or ""))
        citation_id = str(arguments.get("citation_id") or "")
        citation = next((item for item in package.citations if item.citation_id == citation_id), None)
        if citation is None:
            raise ValueError("citation_id does not belong to this retrieval")
        return {"retrieval_id": package.retrieval_id, "citation": {
            "citation_id": citation.citation_id, "title": citation.title, "quote": citation.quote,
            "key_evidence_sentences": list(citation.key_evidence_sentences), "locator": citation.locator,
            "url": citation.url, "pmid": citation.pmid, "doi": citation.doi, "version_id": citation.version_id,
            "recommendation_id": citation.recommendation_id, "population": citation.population,
            "evidence_status": citation.evidence_status.value, "actions": list(citation.actions),
        }}

    def audit_grounded_answer(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        package = self.sessions.get(str(arguments.get("retrieval_id") or ""))
        return self.auditor.audit(
            package=package, answer=str(arguments.get("answer") or ""),
            claim_records=arguments.get("claim_records"),
        )

    @staticmethod
    def tool_specifications() -> list[dict[str, Any]]:
        return [
            {
                "name": "retrieve_evidence",
                "description": (
                    "新生儿专科只读检索：先由 Agent 从只读 HIS 获取患者快照，再检索患者事实、PubMed 与新生儿版本化指南；不生成诊疗结论。"
                    "年龄与状态约定：ga_days/ga_text（出生胎龄）、pna_hours（生后小时）、PMA 与矫正年龄必须分开记录且由调用方派生，RAG 不推导；"
                    "数据状态用 observed_positive/observed_negative/not_recorded/pending/unavailable/hypothesis，母体事实用 subject_role=mother 标注；pending 与 not_recorded 不得当作阴性。"
                    "引用关联：ranked_documents[].citation_id 与 citations[].citation_id 是同一稳定外键（evidence_groups 中同值），用外键关联引用，不要按下标配对；审计回答用 [<citation_id>] 标注。"
                    "中英文规则：question 为中文原始问题，原样保留不翻译；问题中直接出现的英文词由系统自动提取进 question_english_terms 并参与英文检索（调用方不要手工填该字段）；"
                    "term_mapping 是人工审核的映射表（中文概念→english/mesh/aliases）：英文与 MeSH 由同一条映射行派生，仅在概念或别名出现在问题/患者术语/facets 时展开；"
                    "展开记录在 query_plan.mapping_trace 与 expansion_trace（source=reviewed_term_mapping），未命中概念也会列出；"
                    "english_terms/mesh_terms/user_terms 保留为兼容字段（旧调用方仍可用），新调用方应优先使用 term_mapping；"
                    "中文片段以 CJK 二元组参与匹配，可直接命中中文来源。返回 EvidencePackage，其中 query_plan 可审计实际使用的全部术语；applicability 给出指南结构化条件与患者事实的逐条核对结果。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["question"],
                    "properties": {
                        "request_id": {"type": "string", "description": "调用方关联 ID，仅用于日志与结果关联，不参与检索。"},
                        "question": {"type": "string", "description": "中文临床原始问题（可含英文药名/缩写，如 metformin）；原样保留、不翻译；英文词会被自动提取为 question_english_terms 并加入英文检索。"},
                        "task": {"type": "string", "description": "任务类型标记（预留），当前只随请求记录，不改变检索行为。"},
                        "as_of": {"type": "string", "description": "ISO 日期；证据与患者事实的可见截止边界，未来记录、撤回文献与被替代指南在此边界外被排除。"},
                        "patient_terms": {"type": "array", "items": {"type": "string"}, "description": "人工审核的最小化患者相关术语（中英皆可，新生儿场景可用“新生儿呼吸窘迫综合征”等），作为检索概念参与匹配；不得放原始病历、患者标识或自由文本病史。"},
                        "patient_snapshot": {"type": "object", "description": "Agent 从只读 HIS 获取的结构化、按 as_of 截止的患者快照（patient_ref/as_of/facts/reviewed_external_terms）；不得包含真实标识或未授权自由文本。"},
                        "source_kinds": {"type": "array", "items": {"type": "string", "enum": ["pubmed", "guideline", "drug", "clinical_trial", "local_knowledge"]}, "description": "来源种类过滤；不填表示按默认顺序检索已注册来源。"},
                        "term_mapping": {"type": ["array", "object"], "description": "人工审核的术语映射表。推荐数组行：[{\"concept\":\"新生儿呼吸窘迫综合征\",\"aliases\":[\"NRDS\"],\"english\":[\"respiratory distress syndrome\"],\"mesh\":[\"Respiratory Distress Syndrome, Newborn\"]}]；也接受对象形式 {\"概念\": {\"english\":[...],\"mesh\":[...]}} 或旧式 {\"概念\": [\"english\"]}。英文与 MeSH 必须与概念绑定；仅当概念/别名出现在问题、患者术语或 facets 时展开。展开与未命中记录在 query_plan.mapping_trace。"},
                        "user_terms": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}, "description": "兼容字段（旧调用方）：人工审核的中文概念→英文术语映射。新调用方请用 term_mapping，以便同时绑定 MeSH；仅当键出现在问题/患者术语文本中才展开，展开记录在 query_plan.expansion_trace（source=reviewed_user_terms），不自动翻译。"},
                        "english_terms": {"type": "array", "items": {"type": "string"}, "description": "兼容字段（旧调用方）：人工审核的英文医学术语/缩写，直接参与英文检索与实时 PubMed 查询。新调用方请把英文放进 term_mapping 的对应概念行，以保留概念—术语绑定。"},
                        "mesh_terms": {"type": "array", "items": {"type": "string"}, "description": "兼容字段（旧调用方）：人工审核的 MeSH 主题词，参与英文检索与实时 PubMed 查询。新调用方请把 MeSH 放进 term_mapping 的对应概念行。"},
                        "facets": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}, "description": "结构化临床问题槽位（population/outcome 等），取值作为检索概念参与匹配。"},
                        "filters": {"type": "object", "description": "结构化过滤项，如 {\"guideline_version\": \"2024\"} 限定指南版本。"},
                        "clinical_intent": {"type": "string", "description": "可选：guideline_recommendation / evidence_update / drug_safety / clinical_question；缺省时由问题文本推断。"},
                        "source_mode": {"type": "string", "enum": ["live", "custom"], "description": "来源模式，仅两种：live=实时 PubMed + 官方指南元数据（默认）；custom=调用方注册的自有知识库（经 /api/sources/custom 传入，schema 见 contracts/README.md）。服务不内置本地语料；实时失败不自动回退。"},
                        "retrieval_method": {"type": "string", "description": "底层检索方法：bm25（默认）/ql（Dirichlet 查询似然）/rm3（伪相关反馈）/dense（MedCPT 双塔，英文域）/xlingual（multilingual-e5 双塔，中文问题直接编码、跨语言检索英文语料）/m3（BGE-M3 多语言双塔，CLS 池化，跨语言）/translate（zh→en 机器译文作为额外查询变体，原问题保留、数字不一致会告警）/splade/colbert/query2doc；多选用逗号或数组（如 \"bm25,xlingual\"）做多路 RRF 融合；零分路线不参与 RRF。神经方法需本地模型，不可用时该方法标 not_configured（trace/warnings），不静默降级。"},
                        "rerank": {"type": "boolean", "description": "true 时在召回后用 MedCPT cross-encoder 重排；未配置时 rerank_trace.status=not_configured。"},
                        "fidelity_gate": {"type": "string", "enum": ["off", "audit", "warn", "downweight", "reject"], "description": "查询保真门控（研究特性，默认 off=不审计）。审计对象是中文问题→英文检索计划的转换产物（审核术语/机器译文/多语言编码输入）：实体覆盖、数值保留、否定/时间表达、词表实体串换。audit=记录不改信号；warn=警示；downweight/reject=按判定级别对受影响路线降权或退出检索融合（审核映射为质量上界分支，只警示不降权）。全过程写入 trace.query_fidelity；查询计划不被静默改写。"},
                    },
                },
            },
            {"name": "open_citation", "description": "打开本次检索（同一 retrieval_id）中的版本化引文和原文定位；citation_id 必须属于该 retrieval_id。", "parameters": {"type": "object", "required": ["retrieval_id", "citation_id"], "properties": {"retrieval_id": {"type": "string", "description": "检索作用域 ID，来自 retrieve_evidence 结果。"}, "citation_id": {"type": "string", "description": "引用 ID（<retrieval_id>:cit-N）；跨检索使用会被拒绝。"}}}},
            {"name": "audit_grounded_answer", "description": "以本次检索证据审计回答主张与引用（claim 级）；不生成诊疗结论，默认只做引用结构检查，语义支持标记 not_checked。", "parameters": {"type": "object", "required": ["retrieval_id", "answer"], "properties": {"retrieval_id": {"type": "string"}, "answer": {"type": "string", "description": "待审计回答；用 [citation_id] 标注引用，只能引用同一 retrieval_id 的 citation_id。"}}}},
        ]
