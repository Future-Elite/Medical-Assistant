import { useEffect, useMemo, useState } from "react";
import { useWorkbench } from "../state/workbench";
import { Badge, Empty, Field, Notice, PageHeader, StatusRow } from "../components/ui";
import { Icon } from "../components/Icon";
import { SnapshotView } from "../components/PatientView";
import { caseDisplayLabel, parseTermMapping } from "../lib/format";

function StepKicker({ index }: { index: number }) {
  return <span>步骤 {String(index).padStart(2, "0")} / 10</span>;
}

function NextBar({
  prev,
  next,
  nextLabel,
  onNext,
}: {
  prev?: string;
  next?: string;
  nextLabel?: string;
  onNext?: () => void;
}) {
  const { goStep, stepStatus } = useWorkbench();
  return (
    <div className="page-nav">
      {prev ? (
        <button type="button" className="btn" onClick={() => goStep(prev as any)}>
          <Icon name="chevronLeft" size={14} />
          上一步
        </button>
      ) : (
        <span />
      )}
      {next ? (
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            onNext?.();
            goStep(next as any);
          }}
        >
          {nextLabel || "下一步"}
          <Icon name="chevronRight" size={14} />
          {stepStatus[next as keyof typeof stepStatus] ? null : null}
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

export function CasePanel() {
  const {
    cases,
    caseId,
    applyCase,
    form,
    setForm,
    snapshotJson,
    setSnapshotJson,
    manualSnapshot,
    setManualSnapshot,
    snapshotState,
    loadSnapshot,
    snapshot,
  } = useWorkbench();

  return (
    <>
      <PageHeader
        kicker="准备 · 病例与快照"
        title="病例与快照"
        actions={<Badge tone={snapshot ? "done" : ""}>{snapshotState}</Badge>}
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>病例选择</h3>
            <span className="hint">{cases.length} 例合成病例</span>
          </div>
          <div className="card-body stack">
            <div className="form-grid c3">
              <Field label="病例">
                <select
                  className="select"
                  value={caseId}
                  onChange={(event) => applyCase(event.target.value)}
                >
                  <option value="">— 手动输入 —</option>
                  {cases.filter((item) => item.case_id.startsWith("neo-chain-")).length ? (
                    <optgroup label="链路演示（合成）">
                      {cases.filter((item) => item.case_id.startsWith("neo-chain-")).map((item) => (
                        <option key={item.case_id} value={item.case_id}>
                          {caseDisplayLabel(item.label)}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  <optgroup label="EHR 演示（合成）">
                    {cases.filter((item) => !item.case_id.startsWith("neo-chain-")).map((item) => (
                      <option key={item.case_id} value={item.case_id}>
                        {caseDisplayLabel(item.label)}
                      </option>
                    ))}
                  </optgroup>
                </select>
              </Field>
              <Field label="patient_ref">
                <input className="input" readOnly value={snapshot?.patient_ref || ""} />
              </Field>
              <Field label="as_of">
                <input
                  className="input"
                  type="date"
                  value={form.asOf}
                  onChange={(event) => setForm("asOf", event.target.value)}
                />
              </Field>
            </div>
            <div className="notice info">
              <Icon name="shield" size={15} />
              <div>合成演示数据 · 只读检索 · 不写回 HIS · 输出不是诊断或治疗建议</div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>快照载入</h3>
            <div className="row">
              <label className="check">
                <input
                  type="checkbox"
                  checked={manualSnapshot}
                  onChange={(event) => setManualSnapshot(event.target.checked)}
                />
                手动快照
              </label>
              <button type="button" className="btn btn-primary btn-sm" onClick={loadSnapshot}>
                <Icon name="database" size={14} />
                载入快照
              </button>
            </div>
          </div>
          <div className="card-body">
            <div className="split-preview">
              <Field label="快照 JSON" hint="POST /api/his/snapshot">
                <textarea
                  className="textarea code"
                  style={{ minHeight: 220 }}
                  spellCheck={false}
                  value={snapshotJson}
                  onChange={(event) => setSnapshotJson(event.target.value)}
                />
              </Field>
              <div>
                <div className="section-title">最近一次快照回显</div>
                <SnapshotView snapshot={snapshot} />
              </div>
            </div>
          </div>
        </div>
      </div>
      <NextBar next="question" nextLabel="问题与意图" />
    </>
  );
}

export function QuestionPanel() {
  const { form, setForm } = useWorkbench();
  return (
    <>
      <PageHeader
        kicker="准备 · 问题与意图"
        title="问题与意图"
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>检索问题</h3>
            <Badge tone={form.question.trim() ? "ready" : ""}>
              {form.question.trim() ? "已填写" : "待填写"}
            </Badge>
          </div>
          <div className="card-body stack">
            <Field label="question" hint="中文临床问题会按 query_language 与术语映射进入检索。">
              <textarea
                className="textarea"
                style={{ minHeight: 110 }}
                value={form.question}
                onChange={(event) => setForm("question", event.target.value)}
                placeholder="例如：该患儿新生儿呼吸窘迫综合征的指南推荐是什么？"
              />
            </Field>
            <div className="form-grid c3">
              <Field label="task">
                <select
                  className="select"
                  value={form.task}
                  onChange={(event) => setForm("task", event.target.value)}
                >
                  <option value="clinical_question">clinical_question</option>
                  <option value="evidence_update">evidence_update</option>
                </select>
              </Field>
              <Field label="query_language">
                <select
                  className="select"
                  value={form.queryLanguage}
                  onChange={(event) => setForm("queryLanguage", event.target.value)}
                >
                  <option value="zh">zh</option>
                  <option value="en">en</option>
                </select>
              </Field>
              <Field label="clinical_intent">
                <select
                  className="select"
                  value={form.clinicalIntent}
                  onChange={(event) => setForm("clinicalIntent", event.target.value)}
                >
                  <option value="">自动推断</option>
                  <option value="guideline_recommendation">guideline_recommendation</option>
                  <option value="evidence_update">evidence_update</option>
                  <option value="drug_safety">drug_safety</option>
                  <option value="clinical_question">clinical_question</option>
                </select>
              </Field>
            </div>
          </div>
        </div>
      </div>
      <NextBar prev="case" next="terms" nextLabel="术语与映射" />
    </>
  );
}

export function TermsPanel() {
  const { form, setForm, health } = useWorkbench();
  const mapping = useMemo(() => {
    try {
      return { rows: parseTermMapping(form.termMapping), error: null as string | null };
    } catch (error) {
      return { rows: [] as Record<string, any>[], error: (error as Error).message };
    }
  }, [form.termMapping]);
  const library = health?.term_mapping_library || {};
  const mesh = library.mesh_status || {};

  return (
    <>
      <PageHeader
        kicker="准备 · 术语与映射"
        title="术语与映射"
        actions={<Badge tone={mapping.error ? "error" : "ready"}>{mapping.error ? "JSON 错误" : `${mapping.rows.length} 行`}</Badge>}
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>术语输入</h3>
          </div>
          <div className="card-body stack">
            <div className="form-grid c2">
              <Field label="patient_terms" hint="逗号分隔">
                <input
                  className="input"
                  value={form.patientTerms}
                  onChange={(event) => setForm("patientTerms", event.target.value)}
                  placeholder="新生儿呼吸窘迫综合征, 早产"
                />
              </Field>
              <Field label="facets JSON">
                <textarea
                  className="textarea code short"
                  spellCheck={false}
                  value={form.facets}
                  onChange={(event) => setForm("facets", event.target.value)}
                />
              </Field>
            </div>
            <Field label="term_mapping JSON" hint="数组或 {概念: 载荷} 对象；兼容 english / mesh / aliases。">
              <textarea
                className="textarea code"
                style={{ minHeight: 140 }}
                spellCheck={false}
                value={form.termMapping}
                onChange={(event) => setForm("termMapping", event.target.value)}
              />
            </Field>
            {mapping.error ? <Notice tone="error">{mapping.error}</Notice> : null}
          </div>
        </div>

        <div className="grid grid-2">
          <div className="card">
            <div className="card-head">
              <h3>映射表预览</h3>
              <Badge>{mapping.rows.length} 行</Badge>
            </div>
            <div className="card-body">
              <div className="mapping-table">
                {mapping.rows.length ? (
                  <table>
                    <thead>
                      <tr>
                        <th>中文概念</th>
                        <th>English</th>
                        <th>MeSH</th>
                        <th>别名</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mapping.rows.map((row, index) => (
                        <tr key={index}>
                          <td>{row.concept || ""}</td>
                          <td>{(row.english || []).join(" · ")}</td>
                          <td>{(row.mesh || []).join(" · ")}</td>
                          <td>{(row.aliases || []).join(" · ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="hint">暂无映射</div>
                )}
              </div>
            </div>
          </div>
          <div className="card">
            <div className="card-head">
              <h3>术语映射库</h3>
              <Badge tone={library.configured ? "done" : ""}>
                {library.configured ? `${library.rows || 0} 概念` : "未配置"}
              </Badge>
            </div>
            <div className="card-body">
              {library.configured ? (
                <div className="stack">
                  <div className="chips">
                    {(library.concepts || []).slice(0, 24).map((concept: string) => (
                      <span className="chip" key={concept}>
                        {concept}
                      </span>
                    ))}
                  </div>
                  <div className="status-list">
                    <StatusRow label="来源" value={library.source || "映射库"} />
                    <StatusRow label="MeSH 精确锚定" value={mesh.mesh_grounded ?? 0} />
                    <StatusRow label="MeSH 最佳匹配" value={mesh.mesh_top_match ?? 0} />
                    <StatusRow label="未找到" value={mesh.not_found ?? 0} />
                  </div>
                </div>
              ) : (
                <Empty title="未配置映射库" />
              )}
            </div>
          </div>
        </div>
      </div>
      <NextBar prev="question" next="scope" nextLabel="范围与过滤" />
    </>
  );
}

export function ScopePanel() {
  const { form, setForm } = useWorkbench();
  const toggleKind = (kind: string, checked: boolean) => {
    const next = checked
      ? [...new Set([...form.sourceKinds, kind])]
      : form.sourceKinds.filter((item) => item !== kind);
    setForm("sourceKinds", next);
  };
  return (
    <>
      <PageHeader
        kicker="准备 · 范围与过滤"
        title="范围与过滤"
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>检索范围</h3>
            <Badge tone="ready">已配置</Badge>
          </div>
          <div className="card-body stack">
            <div className="form-grid c3">
              <Field label="source_mode">
                <select
                  className="select"
                  value={form.sourceMode}
                  onChange={(event) =>
                    setForm("sourceMode", event.target.value as "live" | "custom")
                  }
                >
                  <option value="live">live · 实时来源</option>
                  <option value="custom">custom · 来源库</option>
                </select>
              </Field>
              <Field label="top_k">
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={50}
                  value={form.topK}
                  onChange={(event) => setForm("topK", Number(event.target.value) || 50)}
                />
              </Field>
              <Field label="source_kinds">
                <div className="check-grid">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={form.sourceKinds.includes("guideline")}
                      onChange={(event) => toggleKind("guideline", event.target.checked)}
                    />
                    guideline
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={form.sourceKinds.includes("pubmed")}
                      onChange={(event) => toggleKind("pubmed", event.target.checked)}
                    />
                    pubmed
                  </label>
                </div>
              </Field>
            </div>
            <Field label="filters JSON" hint="按来源、时间或版本过滤的附加条件。">
              <textarea
                className="textarea code short"
                spellCheck={false}
                value={form.filters}
                onChange={(event) => setForm("filters", event.target.value)}
              />
            </Field>
          </div>
        </div>
      </div>
      <NextBar prev="terms" next="method" nextLabel="检索方法与门控" />
    </>
  );
}

export function MethodPanel() {
  const {
    form,
    setForm,
    health,
    capabilities,
    rerankerInfo,
    industrialInfo,
    applyReranker,
    applyIndustrial,
  } = useWorkbench();
  const [rerankerModel, setRerankerModel] = useState("medcpt");
  const [rerankPool, setRerankPool] = useState(0);
  const [entityMode, setEntityMode] = useState("active");
  const [lifecycleMode, setLifecycleMode] = useState("active");
  const [claimModel, setClaimModel] = useState("flan-t5-large");
  const [semantic, setSemantic] = useState(true);

  useEffect(() => {
    if (rerankerInfo) {
      setRerankerModel(rerankerInfo.model || "medcpt");
      setRerankPool(Number(rerankerInfo.rerank_pool || 0));
    }
  }, [rerankerInfo]);

  useEffect(() => {
    if (!industrialInfo) return;
    setEntityMode(industrialInfo.entity_linking?.mode || "active");
    setLifecycleMode(industrialInfo.guideline_lifecycle?.mode || "active");
    const verifier = industrialInfo.claim_verifier || {};
    setClaimModel(verifier.configured ? verifier.model || "flan-t5-large" : "off");
    setSemantic(Boolean(industrialInfo.entity_linking?.semantic_requested));
  }, [industrialInfo]);

  const semState = health?.entity_linking?.semantic || {};

  return (
    <>
      <PageHeader
        kicker="准备 · 检索方法与门控"
        title="检索方法与门控"
        actions={<Badge tone={health ? "ready" : ""}>{health ? "已读取" : "待读取"}</Badge>}
      />
      <div className="page-body">
        <div className="card">
          <div className="card-head">
            <h3>检索与门控</h3>
          </div>
          <div className="card-body stack">
            <div className="form-grid c2">
              <Field label="retrieval_method">
                <select
                  className="select"
                  value={form.retrievalMethod}
                  onChange={(event) => setForm("retrievalMethod", event.target.value)}
                >
                  <option value="">默认 bm25</option>
                  <option value="bm25">bm25</option>
                  <option value="ql">ql</option>
                  <option value="rm3">rm3</option>
                  <option value="dense">dense · MedCPT</option>
                  <option value="xlingual">xlingual · multilingual-e5</option>
                  <option value="m3">m3 · BGE-M3</option>
                  <option value="translate">translate · zh→en</option>
                  <option value="splade">splade</option>
                  <option value="colbert">colbert</option>
                  <option value="query2doc">query2doc</option>
                  <option value="bm25,ql">bm25 + ql · RRF</option>
                  <option value="bm25,xlingual">bm25 + xlingual · RRF</option>
                  <option value="bm25,m3">bm25 + m3 · RRF</option>
                  <option value="bm25,dense">bm25 + dense · RRF</option>
                  <option value="bm25,translate">bm25 + translate</option>
                </select>
              </Field>
              <Field label="fidelity_gate">
                <select
                  className="select"
                  value={form.fidelityGate}
                  onChange={(event) => setForm("fidelityGate", event.target.value)}
                >
                  <option value="off">off</option>
                  <option value="audit">audit</option>
                  <option value="warn">warn</option>
                  <option value="downweight">downweight</option>
                  <option value="reject">reject</option>
                </select>
              </Field>
            </div>

            <div className="subcard">
              <div className="subcard-title">
                <span>重排</span>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.rerank}
                    onChange={(event) => setForm("rerank", event.target.checked)}
                  />
                  启用重排
                </label>
              </div>
              <div className="form-grid c2">
                <Field label="重排器模型">
                  <select
                    className="select"
                    value={rerankerModel}
                    onChange={(event) => setRerankerModel(event.target.value)}
                  >
                    <option value="medcpt">MedCPT cross-encoder</option>
                    <option value="qwen3">Qwen3-Reranker-0.6B</option>
                  </select>
                </Field>
                <Field label="重排候选池 rerank_pool">
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step={10}
                    value={rerankPool}
                    onChange={(event) => setRerankPool(Number(event.target.value) || 0)}
                  />
                </Field>
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => applyReranker(rerankerModel, rerankPool)}
                >
                  应用重排配置
                </button>
                <span className="hint">
                  当前：{rerankerInfo?.model || "medcpt"} · 候选池 {rerankerInfo?.rerank_pool ?? 0}
                </span>
              </div>
            </div>

            {capabilities.industrial ? (
            <div className="subcard">
              <div className="subcard-title">
                <span>校验与治理</span>
              </div>
              <div className="form-grid c3">
                <Field label="实体链接">
                  <select
                    className="select"
                    value={entityMode}
                    onChange={(event) => setEntityMode(event.target.value)}
                  >
                    <option value="active">active · 核验并扩展</option>
                    <option value="shadow">shadow · 只记录</option>
                    <option value="off">off · 关闭</option>
                  </select>
                </Field>
                <Field label="指南版本治理">
                  <select
                    className="select"
                    value={lifecycleMode}
                    onChange={(event) => setLifecycleMode(event.target.value)}
                  >
                    <option value="active">active · 撤回/更新生效</option>
                    <option value="shadow">shadow · 只记录</option>
                    <option value="off">off · 关闭</option>
                  </select>
                </Field>
                <Field label="主张校验">
                  <select
                    className="select"
                    value={claimModel}
                    onChange={(event) => setClaimModel(event.target.value)}
                  >
                    <option value="flan-t5-large">flan-t5-large</option>
                    <option value="roberta-large">roberta-large</option>
                    <option value="deberta-v3-large">deberta-v3-large</option>
                    <option value="off">off · 仅结构审计</option>
                  </select>
                </Field>
              </div>
              <label className="check" style={{ marginTop: 10 }}>
                <input
                  type="checkbox"
                  checked={semantic}
                  disabled={entityMode === "off"}
                  onChange={(event) => setSemantic(event.target.checked)}
                />
                实体链接语义候选 BGE-M3 · 首次加载较慢
              </label>
              <div className="row" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() =>
                    applyIndustrial({
                      entity_linking_mode: entityMode,
                      guideline_lifecycle_mode: lifecycleMode,
                      claim_verifier: claimModel,
                      entity_linking_semantic: semantic,
                    })
                  }
                >
                  应用校验配置
                </button>
                <span className="hint">
                  词表 {health?.entity_linking?.lexicon?.descriptors ?? "—"} 描述符 · 语义{" "}
                  {semState.configured
                    ? semState.loaded
                      ? "已加载"
                      : semState.reason
                        ? `不可用：${semState.reason}`
                        : "待首次加载"
                    : "off"}
                </span>
              </div>
            </div>
            ) : null}
          </div>
        </div>
      </div>
      <NextBar prev="scope" next="request" nextLabel="请求组装" />
    </>
  );
}
