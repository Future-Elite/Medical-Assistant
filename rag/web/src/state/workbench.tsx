import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, postJson, requestJson, setApiLogger } from "../api/client";
import {
  parseJsonObject,
  parseTermMapping,
  caseDisplayLabel,
} from "../lib/format";
import type {
  ApiLogEntry,
  BadgeTone,
  ControllerPolicy,
  DemoCase,
  Dict,
  HealthPayload,
  PatientSnapshot,
  RetrievalRequest,
  StepKey,
} from "../types";

export type ThemeChoice = "light" | "dark" | "warm" | "indigo" | "auto";

export interface FormState {
  question: string;
  task: string;
  queryLanguage: string;
  clinicalIntent: string;
  asOf: string;
  patientTerms: string;
  termMapping: string;
  facets: string;
  filters: string;
  sourceMode: "live" | "custom";
  topK: number;
  sourceKinds: string[];
  retrievalMethod: string;
  rerank: boolean;
  fidelityGate: string;
}

export interface ParseOptions {
  chunkStrategy: string;
  tableStrategy: string;
  figureStrategy: string;
  mineruTier: string;
  chunkSize: number;
  chunkOverlap: number;
  forceReparse: boolean;
}

export interface Toast {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
}

interface Workbench {
  form: FormState;
  setForm: <K extends keyof FormState>(key: K, value: FormState[K]) => void;

  cases: DemoCase[];
  caseId: string;
  currentCase: DemoCase | null;
  applyCase: (caseId: string) => void;

  snapshot: PatientSnapshot | null;
  snapshotJson: string;
  setSnapshotJson: (value: string) => void;
  manualSnapshot: boolean;
  setManualSnapshot: (value: boolean) => void;
  snapshotState: string;
  loadSnapshot: () => Promise<void>;

  requestPreview: { json: string; error: string | null; request: RetrievalRequest | null };
  resetForm: () => void;

  running: boolean;
  runError: string | null;
  pkg: any;
  selectedIndex: number | null;
  selectResult: (index: number) => void;
  sourceFilter: string;
  setSourceFilter: (value: string) => void;
  resultTab: string;
  setResultTab: (value: string) => void;
  patientTab: string;
  setPatientTab: (value: string) => void;
  runRetrieval: () => Promise<void>;

  controllerPolicy: ControllerPolicy;
  setControllerPolicy: (patch: Partial<ControllerPolicy>) => void;
  controllerResult: any;
  controllerRunning: boolean;
  runController: () => Promise<void>;

  health: HealthPayload | null;
  capabilities: { industrial: boolean; guidelines: boolean };
  tools: Dict | null;
  sources: Dict | null;
  backendError: string | null;
  backendCheck: () => Promise<HealthPayload | null>;
  registeredCount: number;

  demoGuidelines: Dict[];
  loadDemoSources: () => Promise<void>;
  clearSources: () => Promise<void>;

  rerankerInfo: Dict | null;
  applyReranker: (model: string, pool: number) => Promise<void>;
  industrialInfo: Dict | null;
  applyIndustrial: (payload: Dict) => Promise<void>;

  guidelines: Dict[];
  guidelineOptions: Dict | null;
  guidelineConfigured: boolean;
  guidelineDataDir: string;
  guidelineStatus: string;
  guidelineError: string | null;
  selectedGuidelineId: string;
  selectGuideline: (documentId: string) => Promise<void>;
  guidelineChunks: Dict[];
  selectedChunkId: string;
  selectChunk: (chunkId: string) => void;
  loadGuidelines: () => Promise<void>;

  parseOptions: ParseOptions;
  setParseOption: <K extends keyof ParseOptions>(key: K, value: ParseOptions[K]) => void;
  parseStatus: string;
  parseHint: string;
  parseGuidelines: (all: boolean) => Promise<void>;

  pdfPages: number;
  pdfPage: number;
  showPdfPage: (page: number) => void;
  pdfMeta: string;
  pdfHint: string;

  applicabilityResult: Dict | null;
  applicabilityError: string | null;
  checkApplicability: () => Promise<void>;
  applicabilityWorkflowResult: Dict | null;
  applicabilityWorkflowError: string | null;
  runApplicabilityWorkflow: (executeExternal?: boolean, includeIndustrialGraph?: boolean) => Promise<void>;
  resumeApplicabilityWorkflow: (facts: Dict[]) => Promise<void>;

  saveLiveGuideline: (doc: Dict) => Promise<void>;
  openGuidelineChunk: (sourceId: string, chunkId: string) => Promise<void>;

  auditAnswer: string;
  setAuditAnswer: (value: string) => void;
  auditResult: Dict | null;
  auditRunning: boolean;
  auditHint: string;
  runAudit: () => Promise<void>;
  openCitation: (citationId: string) => Promise<void>;
  citationMessage: string;

  step: StepKey;
  goStep: (step: StepKey) => void;
  stepStatus: Record<StepKey, { text: string; tone: BadgeTone }>;
  maxVisitedStep: number;

  apiLog: ApiLogEntry[];
  clearApiLog: () => void;

  theme: ThemeChoice;
  setTheme: (theme: ThemeChoice) => void;

  toasts: Toast[];
  notify: (message: string, tone?: Toast["tone"]) => void;
  dismissToast: (id: number) => void;

  cmdOpen: boolean;
  setCmdOpen: (open: boolean) => void;
}

const WorkbenchContext = createContext<Workbench | null>(null);

export const THEME_KEY = "neo-workbench-theme";
const THEME_VALUES: ThemeChoice[] = ["light", "dark", "warm", "indigo", "auto"];

const STEP_ORDER: StepKey[] = [
  "case",
  "question",
  "terms",
  "scope",
  "method",
  "request",
  "run",
  "results",
  "guidelines",
  "audit",
];

// 模块级序号：即使 Provider 因故重建，日志 key 也保持全局唯一，避免重复行。
let apiSequence = 0;

function defaultForm(): FormState {
  return {
    question: "",
    task: "clinical_question",
    queryLanguage: "zh",
    clinicalIntent: "",
    asOf: "",
    patientTerms: "",
    termMapping: "[]",
    facets: "{}",
    filters: "{}",
    sourceMode: "live",
    topK: 50,
    sourceKinds: ["guideline", "pubmed"],
    retrievalMethod: "",
    rerank: false,
    fidelityGate: "off",
  };
}

function defaultParseOptions(): ParseOptions {
  return {
    chunkStrategy: "recursive",
    tableStrategy: "markdown",
    figureStrategy: "reference",
    mineruTier: "advanced",
    chunkSize: 800,
    chunkOverlap: 120,
    forceReparse: false,
  };
}

function systemDark(): boolean {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const [form, setFormState] = useState<FormState>(defaultForm);
  const [cases, setCases] = useState<DemoCase[]>([]);
  const [caseId, setCaseId] = useState("");
  const [snapshot, setSnapshot] = useState<PatientSnapshot | null>(null);
  const [snapshotJson, setSnapshotJson] = useState(
    '{\n  "patient_ref": "",\n  "as_of": "",\n  "facts": []\n}',
  );
  const [manualSnapshot, setManualSnapshot] = useState(false);
  const [snapshotState, setSnapshotState] = useState("未载入");

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [pkg, setPkg] = useState<any>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [resultTab, setResultTab] = useState("evidence");
  const [patientTab, setPatientTab] = useState("hits");

  const [controllerPolicy, setControllerPolicyState] = useState<ControllerPolicy>({
    max_rounds: 4,
    max_cost: 3,
    min_evidence: 3,
    min_coverage: 0.5,
  });
  const [controllerResult, setControllerResult] = useState<any>(null);
  const [controllerRunning, setControllerRunning] = useState(false);

  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [tools, setTools] = useState<Dict | null>(null);
  const [sources, setSources] = useState<Dict | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);

  const [demoGuidelines, setDemoGuidelines] = useState<Dict[]>([]);

  const [rerankerInfo, setRerankerInfo] = useState<Dict | null>(null);
  const [industrialInfo, setIndustrialInfo] = useState<Dict | null>(null);

  const [guidelines, setGuidelines] = useState<Dict[]>([]);
  const guidelinesRef = useRef<Dict[]>([]);
  const [guidelineOptions, setGuidelineOptions] = useState<Dict | null>(null);
  const [guidelineConfigured, setGuidelineConfigured] = useState(false);
  const [guidelineDataDir, setGuidelineDataDir] = useState("未配置");
  const [guidelineStatus, setGuidelineStatus] = useState("检查中");
  const [guidelineError, setGuidelineError] = useState<string | null>(null);
  const [selectedGuidelineId, setSelectedGuidelineId] = useState("");
  const [guidelineChunks, setGuidelineChunks] = useState<Dict[]>([]);
  const [selectedChunkId, setSelectedChunkId] = useState("");
  const [parseOptions, setParseOptionsState] = useState<ParseOptions>(defaultParseOptions);
  const [parseStatus, setParseStatus] = useState("尚未解析");
  const [parseHint, setParseHint] = useState(
    "解析产物写入 <data>/<document_id>_chunks.jsonl；custom 检索会自动重新加载。",
  );
  const [pdfPages, setPdfPages] = useState(0);
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfMeta, setPdfMeta] = useState("未选择指南");
  const [pdfHint, setPdfHint] = useState("页面由服务端实时渲染");

  const [applicabilityResult, setApplicabilityResult] = useState<Dict | null>(null);
  const [applicabilityError, setApplicabilityError] = useState<string | null>(null);
  const [applicabilityWorkflowResult, setApplicabilityWorkflowResult] = useState<Dict | null>(null);
  const [applicabilityWorkflowError, setApplicabilityWorkflowError] = useState<string | null>(null);

  const [auditAnswer, setAuditAnswer] = useState("");
  const [auditResult, setAuditResult] = useState<Dict | null>(null);
  const [auditRunning, setAuditRunning] = useState(false);
  const [auditHint, setAuditHint] = useState("待检索");
  const [citationMessage, setCitationMessage] = useState("");

  const [step, setStep] = useState<StepKey>("case");
  const [maxVisited, setMaxVisited] = useState(0);

  const [apiLog, setApiLog] = useState<ApiLogEntry[]>([]);

  const [theme, setThemeState] = useState<ThemeChoice>("light");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);
  const [cmdOpen, setCmdOpen] = useState(false);

  const notify = useCallback((message: string, tone: Toast["tone"] = "info") => {
    toastId.current += 1;
    const id = toastId.current;
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((item) => item.id !== id)), 3000);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  useEffect(() => {
    setApiLogger((entry) => {
      apiSequence += 1;
      const id = apiSequence;
      setApiLog((prev) => [{ ...entry, id }, ...prev].slice(0, 60));
    });
    return () => setApiLogger(null);
  }, []);

  /* ---------- 主题 ---------- */
  useEffect(() => {
    let saved: string = "light";
    try {
      saved =
        new URLSearchParams(window.location.search).get("theme") ||
        localStorage.getItem(THEME_KEY) ||
        "light";
    } catch {
      saved = "light";
    }
    if (!THEME_VALUES.includes(saved as ThemeChoice)) saved = "light";
    setThemeState(saved as ThemeChoice);
  }, []);

  const applyTheme = useCallback((choice: ThemeChoice) => {
    const resolved = choice === "auto" ? (systemDark() ? "dark" : "light") : choice;
    document.documentElement.setAttribute("data-theme", resolved);
  }, []);

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* localStorage 不可用时仅本次生效 */
    }
  }, [theme, applyTheme]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      if (theme === "auto") applyTheme("auto");
    };
    query.addEventListener?.("change", handler);
    return () => query.removeEventListener?.("change", handler);
  }, [theme, applyTheme]);

  const setTheme = useCallback((choice: ThemeChoice) => setThemeState(choice), []);

  /* ---------- 表单 ---------- */
  const setForm = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setFormState((prev) => ({ ...prev, [key]: value }));
  }, []);

  const setParseOption = useCallback(
    <K extends keyof ParseOptions>(key: K, value: ParseOptions[K]) => {
      setParseOptionsState((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const setControllerPolicy = useCallback((patch: Partial<ControllerPolicy>) => {
    setControllerPolicyState((prev) => ({ ...prev, ...patch }));
  }, []);

  const buildRequest = useCallback((): RetrievalRequest => {
    const request: RetrievalRequest = {
      question: form.question.trim(),
      task: form.task,
      query_language: form.queryLanguage,
      clinical_intent: form.clinicalIntent || null,
      as_of: form.asOf || null,
      patient_terms: form.patientTerms
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      term_mapping: parseTermMapping(form.termMapping),
      facets: parseJsonObject(form.facets, "facets"),
      filters: parseJsonObject(form.filters, "filters"),
      source_kinds: form.sourceKinds,
      source_mode: form.sourceMode,
      retrieval_method: form.retrievalMethod || null,
      rerank: form.rerank,
      fidelity_gate: form.fidelityGate,
      top_k: Number(form.topK) || 50,
    };
    if (snapshot) request.patient_snapshot = snapshot;
    return request;
  }, [form, snapshot]);

  const requestPreview = useMemo(() => {
    try {
      const request = buildRequest();
      return { json: JSON.stringify(request, null, 2), error: null, request };
    } catch (error) {
      return { json: "", error: (error as Error).message, request: null };
    }
  }, [buildRequest]);

  const currentCase = useMemo(
    () => cases.find((item) => item.case_id === caseId) || null,
    [cases, caseId],
  );

  const applyCaseRecord = useCallback((item: DemoCase) => {
    const preset = item.preset || {};
    setCaseId(item.case_id);
    setFormState((prev) => ({
      ...prev,
      question: preset.question || "",
      task: preset.task || "clinical_question",
      queryLanguage: preset.query_language || "zh",
      clinicalIntent: preset.clinical_intent || "",
      asOf: item.snapshot.as_of || "",
      patientTerms: (preset.patient_terms || []).join(", "),
      termMapping: JSON.stringify(preset.term_mapping || [], null, 2),
      facets: JSON.stringify(preset.facets || {}, null, 2),
      filters: JSON.stringify(preset.filters || {}, null, 2),
      sourceMode: preset.source_mode || "live",
      topK: Number(preset.top_k || 50),
      sourceKinds: preset.source_kinds || ["guideline", "pubmed"],
      retrievalMethod: preset.retrieval_method || "",
      rerank: Boolean(preset.rerank),
      fidelityGate: preset.fidelity_gate || "off",
    }));
    setSnapshotJson(JSON.stringify(item.snapshot, null, 2));
    setManualSnapshot(false);
    setSnapshot(null);
    setSnapshotState("未载入");
    setApplicabilityResult(null);
    setApplicabilityError(null);
    setApplicabilityWorkflowResult(null);
    setApplicabilityWorkflowError(null);
  }, []);

  const applyCase = useCallback(
    (nextCaseId: string) => {
      const item = cases.find((row) => row.case_id === nextCaseId);
      if (item) applyCaseRecord(item);
    },
    [cases, applyCaseRecord],
  );

  const loadCases = useCallback(async () => {
    try {
      const [data, chain] = await Promise.all([
        requestJson("/api/demo/cases"),
        requestJson("/api/demo/applicability-cases").catch(() => ({ cases: [] })),
      ]);
      const rows: DemoCase[] = [...(data.cases || []), ...(chain.cases || [])];
      setCases(rows);
      if (rows.length) applyCaseRecord(rows[0]);
    } catch (error) {
      setBackendError(
        `无法读取病例数据：${(error as Error).message}。请确认后端已启动（PYTHONPATH=. python3 -m rag.server）。`,
      );
    }
  }, [applyCaseRecord]);

  const resetForm = useCallback(() => {
    setFormState(defaultForm());
    setCaseId("");
    setSnapshot(null);
    setSnapshotState("未载入");
    setSnapshotJson('{\n  "patient_ref": "",\n  "as_of": "",\n  "facts": []\n}');
    setManualSnapshot(true);
    setPkg(null);
    setSelectedIndex(null);
    setSourceFilter("all");
    setRunError(null);
    setControllerResult(null);
    setAuditResult(null);
    setAuditHint("待检索");
    setAuditAnswer("");
    setCitationMessage("");
    setApplicabilityResult(null);
    setApplicabilityError(null);
  }, []);

  const loadSnapshot = useCallback(async () => {
    try {
      const parsed = JSON.parse(snapshotJson || "{}");
      if (!manualSnapshot) parsed.as_of = form.asOf || parsed.as_of;
      const response = await postJson("/api/his/snapshot", { patient_snapshot: parsed });
      const data = response.data;
      setSnapshot(data);
      if (data.as_of) setFormState((prev) => ({ ...prev, asOf: data.as_of }));
      setSnapshotState(`${(data.facts || []).length} 条可见事实`);
      setApplicabilityResult(null);
      notify(`快照已载入：${(data.facts || []).length} 条可见事实`, "success");
    } catch (error) {
      setSnapshot(null);
      setSnapshotState("载入失败");
      notify((error as Error).message, "error");
      throw error;
    }
  }, [snapshotJson, manualSnapshot, form.asOf, notify]);

  /* ---------- 后端状态 ---------- */
  const backendCheck = useCallback(async (): Promise<HealthPayload | null> => {
    try {
      const [healthData, toolsData, sourcesData] = await Promise.all([
        requestJson<HealthPayload>("/api/health"),
        requestJson("/api/tools"),
        requestJson("/api/sources"),
      ]);
      setHealth(healthData);
      setTools(toolsData);
      setSources(sourcesData);
      setBackendError(null);
      setGuidelineConfigured(Boolean(healthData.guideline_parsing?.configured));
      setRerankerInfo(healthData.reranker || null);
      setIndustrialInfo({
        entity_linking: healthData.entity_linking,
        guideline_lifecycle: healthData.guideline_lifecycle,
        claim_verifier: healthData.claim_verifier,
        pubmed: healthData.pubmed,
        term_mapping_library: healthData.term_mapping_library,
      });
      return healthData;
    } catch (error) {
      setBackendError((error as Error).message);
      return null;
    }
  }, []);

  const registeredCount = useMemo(() => {
    return ((sources?.custom?.registered as Dict[]) || []).length;
  }, [sources]);

  const capabilities = useMemo(
    () => ({
      industrial: Boolean(
        health?.entity_linking || health?.guideline_lifecycle || health?.claim_verifier,
      ),
      guidelines: Boolean(health?.guideline_parsing),
    }),
    [health],
  );

  const refreshSources = useCallback(async () => {
    try {
      const data = await requestJson("/api/sources");
      setSources(data);
      return data;
    } catch {
      return null;
    }
  }, []);

  const refreshDemoGuidelines = useCallback(async () => {
    try {
      const data = await requestJson("/api/demo/guidelines");
      setDemoGuidelines(data.guidelines || []);
    } catch (error) {
      notify(`演示来源读取失败：${(error as Error).message}`, "error");
    }
  }, [notify]);

  const ensureCustomSources = useCallback(async () => {
    if (form.sourceMode !== "custom" || registeredCount > 0 || guidelineConfigured) return;
    await postJson("/api/demo/guidelines/load", {});
    await Promise.all([refreshDemoGuidelines(), refreshSources()]);
  }, [form.sourceMode, registeredCount, guidelineConfigured, refreshDemoGuidelines, refreshSources]);

  const loadDemoSources = useCallback(async () => {
    try {
      await postJson("/api/demo/guidelines/load", {});
      await Promise.all([refreshDemoGuidelines(), refreshSources(), backendCheck()]);
      notify("演示来源库已加载", "success");
    } catch (error) {
      notify(`加载失败：${(error as Error).message}`, "error");
    }
  }, [refreshDemoGuidelines, refreshSources, backendCheck, notify]);

  const clearSources = useCallback(async () => {
    try {
      await postJson("/api/sources/custom/clear", {});
      await Promise.all([refreshDemoGuidelines(), refreshSources()]);
      notify("自定义来源已清空", "success");
    } catch (error) {
      notify(`清空失败：${(error as Error).message}`, "error");
    }
  }, [refreshDemoGuidelines, refreshSources, notify]);

  const applyReranker = useCallback(
    async (model: string, pool: number) => {
      try {
        const result = await postJson("/api/settings/reranker", { model, rerank_pool: pool });
        setRerankerInfo(result.reranker || null);
        await backendCheck();
        notify(`重排配置已生效：${result.reranker?.model} · 候选池 ${result.reranker?.rerank_pool}`, "success");
      } catch (error) {
        notify(`应用失败：${(error as Error).message}`, "error");
      }
    },
    [backendCheck, notify],
  );

  const applyIndustrial = useCallback(
    async (payload: Dict) => {
      try {
        const result = await postJson("/api/settings/industrial", payload);
        const info = result.industrial || {};
        setIndustrialInfo({
          entity_linking: info.entity_linking,
          guideline_lifecycle: info.guideline_lifecycle,
          claim_verifier: info.claim_verifier,
          pubmed: health?.pubmed,
          term_mapping_library: health?.term_mapping_library,
        });
        await backendCheck();
        notify("校验配置已生效", "success");
      } catch (error) {
        notify(`应用失败：${(error as Error).message}`, "error");
      }
    },
    [backendCheck, health, notify],
  );

  /* ---------- 检索 ---------- */
  const runRetrieval = useCallback(async () => {
    const request = requestPreview.request;
    if (!request) {
      setRunError(requestPreview.error || "请求 JSON 无效");
      notify(requestPreview.error || "请求 JSON 无效", "error");
      return;
    }
    if (!request.question) {
      setRunError("question 不能为空");
      notify("question 不能为空", "error");
      return;
    }
    setRunning(true);
    setRunError(null);
    try {
      await ensureCustomSources();
      const result = await postJson("/api/retrieve", {
        ...request,
        request_id: `gui-${Date.now()}`,
      });
      setPkg(result);
      setSelectedIndex(result.citations?.length ? 0 : null);
      setSourceFilter("all");
      setResultTab("evidence");
      setStep("results");
      setMaxVisited((prev) => Math.max(prev, STEP_ORDER.indexOf("results")));
      setAuditResult(null);
      setAuditHint("待检索");
      notify(`检索完成：返回 ${(result.ranked_documents || []).length} 条证据`, "success");
      await refreshSources();
    } catch (error) {
      setRunError((error as Error).message);
      notify(`检索失败：${(error as Error).message}`, "error");
      setStep("results");
      setResultTab("evidence");
      await refreshSources();
    } finally {
      setRunning(false);
    }
  }, [requestPreview, ensureCustomSources, refreshSources, notify]);

  const runController = useCallback(async () => {
    const request = requestPreview.request;
    if (!request || !request.question) {
      notify("question 不能为空", "error");
      return;
    }
    setControllerRunning(true);
    try {
      await ensureCustomSources();
      const result = await postJson("/api/controller/retrieve", {
        ...request,
        controller_policy: controllerPolicy,
        request_id: `controller-${Date.now()}`,
      });
      setControllerResult(result);
      setResultTab("controller");
      setStep("results");
      notify(
        `控制器完成：${result.stop?.reason || "—"} · ${result.final?.rounds ?? 0} 轮`,
        "success",
      );
    } catch (error) {
      setControllerResult({ error: (error as Error).message });
      setResultTab("controller");
      notify(`控制器运行失败：${(error as Error).message}`, "error");
    } finally {
      setControllerRunning(false);
    }
  }, [requestPreview, controllerPolicy, ensureCustomSources, notify]);

  const selectResult = useCallback((index: number) => {
    setSelectedIndex(index);
  }, []);

  /* ---------- 指南解析 ---------- */
  const loadPdfMeta = useCallback(async (documentId: string) => {
    setPdfPages(0);
    setPdfPage(1);
    setPdfMeta("读取 PDF 页信息…");
    setPdfHint("页面由服务端实时渲染");
    try {
      const meta = await requestJson(
        `/api/guidelines/pdf/meta?document_id=${encodeURIComponent(documentId)}`,
      );
      setPdfPages(Number(meta.pages) || 0);
      setPdfMeta(`${meta.file || ""} · ${meta.pages || 0} 页`);
    } catch (error) {
      setPdfMeta("PDF 预览不可用");
      setPdfHint((error as Error).message);
    }
  }, []);

  const loadChunks = useCallback(
    async (documentId: string) => {
      setSelectedGuidelineId(documentId);
      setSelectedChunkId("");
      setGuidelineChunks([]);
      if (!documentId) {
        setPdfMeta("未选择指南");
        return;
      }
      try {
        const data = await requestJson(
          `/api/guidelines/chunks?document_id=${encodeURIComponent(documentId)}`,
        );
        setGuidelineChunks(data.chunks || []);
        const guide = guidelinesRef.current.find((item) => item.document_id === documentId) || {};
        setParseStatus(`已解析 ${data.count} 块 · 可检索 ${guide.retrievable_count ?? "—"} 块`);
        await loadPdfMeta(documentId);
        if ((data.chunks || []).length) setSelectedChunkId(data.chunks[0].chunk_id);
      } catch (error) {
        setParseStatus("尚未解析");
        setPdfMeta("尚未解析");
        setPdfHint((error as Error).message);
      }
    },
    [loadPdfMeta],
  );

  const loadGuidelines = useCallback(async () => {
    try {
      const data = await requestJson("/api/guidelines");
      const rows: Dict[] = data.guidelines || [];
      guidelinesRef.current = rows;
      setGuidelines(rows);
      setGuidelineConfigured(Boolean(data.configured));
      setGuidelineDataDir(data.data_dir || "未配置 RAG_GUIDELINE_DATA");
      setGuidelineError(data.error || null);
      if (!guidelineOptions && data.options) {
        const options = data.options;
        setGuidelineOptions(options);
        setParseOptionsState((prev) => ({
          ...prev,
          chunkStrategy: options.chunk_strategy || prev.chunkStrategy,
          tableStrategy: options.table_strategy || prev.tableStrategy,
          figureStrategy: options.figure_strategy || prev.figureStrategy,
          mineruTier: options.tier || prev.mineruTier,
          chunkSize: options.chunk_size ?? prev.chunkSize,
          chunkOverlap: options.overlap ?? prev.chunkOverlap,
        }));
      }
      if (!data.configured) {
        setGuidelineStatus("未配置");
        setSelectedGuidelineId("");
        return;
      }
      if (data.error) {
        setGuidelineStatus("manifest 错误");
        return;
      }
      if (!(data.guidelines || []).length) {
        setGuidelineStatus("无指南");
        return;
      }
      setGuidelineStatus(`${data.guidelines.length} 份指南`);
      const fallback = (data.guidelines.find((item: Dict) => item.chunk_count > 0) || data.guidelines[0])
        .document_id;
      const next =
        data.guidelines.some((item: Dict) => item.document_id === selectedGuidelineId) &&
        selectedGuidelineId
          ? selectedGuidelineId
          : fallback;
      await loadChunks(next);
    } catch (error) {
      setGuidelineStatus("读取失败");
      setGuidelineError((error as Error).message);
    }
  }, [guidelineOptions, selectedGuidelineId, loadChunks]);

  const selectGuideline = useCallback(
    async (documentId: string) => {
      await loadChunks(documentId);
    },
    [loadChunks],
  );

  const showPdfPage = useCallback(
    (page: number) => {
      if (!selectedGuidelineId || !pdfPages) return;
      const value = Math.min(Math.max(1, Number(page) || 1), pdfPages);
      setPdfPage(value);
      setPdfHint(`第 ${value} / ${pdfPages} 页`);
    },
    [selectedGuidelineId, pdfPages],
  );

  const selectChunk = useCallback(
    (chunkId: string) => {
      const chunk = guidelineChunks.find((item) => item.chunk_id === chunkId);
      if (!chunk) return;
      setSelectedChunkId(chunkId);
      if (chunk.page_start) showPdfPage(chunk.page_start);
    },
    [guidelineChunks, showPdfPage],
  );

  const parseGuidelines = useCallback(
    async (all: boolean) => {
      const body: Dict = {
        chunk_strategy: parseOptions.chunkStrategy,
        table_strategy: parseOptions.tableStrategy,
        figure_strategy: parseOptions.figureStrategy,
        chunk_size: parseOptions.chunkSize,
        overlap: parseOptions.chunkOverlap,
        tier: parseOptions.mineruTier,
        force: parseOptions.forceReparse,
      };
      if (!all) {
        if (!selectedGuidelineId) {
          notify("先选择指南", "error");
          return;
        }
        body.document_ids = [selectedGuidelineId];
      }
      setParseStatus(all ? "正在解析全部指南…" : "正在解析所选指南…");
      try {
        const data = await postJson("/api/guidelines/parse", body);
        const total = (data.results || []).reduce(
          (sum: number, row: Dict) => sum + (row.chunks || 0),
          0,
        );
        setParseStatus(`完成 ${(data.results || []).length} 份 · ${total} 块`);
        setParseHint(
          `本次选项：${data.options?.chunk_strategy}/${data.options?.table_strategy}/${data.options?.figure_strategy} · 块大小 ${data.options?.chunk_size} · 重叠 ${data.options?.overlap} · ${data.options?.tier}`,
        );
        await loadGuidelines();
        notify(`解析完成：${total} 块`, "success");
      } catch (error) {
        setParseStatus("解析失败");
        notify(`解析失败：${(error as Error).message}`, "error");
      }
    },
    [parseOptions, selectedGuidelineId, loadGuidelines, notify],
  );

  const openGuidelineChunk = useCallback(
    async (sourceId: string, chunkId: string) => {
      if (!guidelines.some((item) => item.document_id === sourceId)) {
        notify("该指南不在解析数据目录中，无法打开 PDF 分块", "error");
        return;
      }
      setStep("guidelines");
      setMaxVisited((prev) => Math.max(prev, STEP_ORDER.indexOf("guidelines")));
      await loadChunks(sourceId);
      setSelectedChunkId(chunkId);
    },
    [guidelines, loadChunks, notify],
  );

  const checkApplicability = useCallback(async () => {
    if (!selectedGuidelineId) {
      setApplicabilityError("先选择指南");
      return;
    }
    if (!currentCase) {
      setApplicabilityError("先在 01 步骤选择病例");
      return;
    }
    setApplicabilityError(null);
    try {
      const data = await postJson("/api/guidelines/applicability", {
        document_id: selectedGuidelineId,
        patient_snapshot: currentCase.snapshot,
      });
      setApplicabilityResult(data);
    } catch (error) {
      setApplicabilityError((error as Error).message);
      setApplicabilityResult(null);
    }
  }, [selectedGuidelineId, currentCase]);

  const runApplicabilityWorkflow = useCallback(async (executeExternal = false, includeIndustrialGraph = false) => {
    if (!selectedGuidelineId) {
      setApplicabilityWorkflowError("先选择目标指南");
      return;
    }
    if (!currentCase) {
      setApplicabilityWorkflowError("先在 01 步骤选择病例");
      return;
    }
    setApplicabilityWorkflowError(null);
    try {
      const data = await postJson("/api/applicability", {
        patient_snapshot: currentCase.snapshot,
        question: form.question.trim() || "该指南是否适用？",
        target_guideline_id: selectedGuidelineId,
        execute_external: executeExternal,
        include_industrial_graph: includeIndustrialGraph,
      });
      setApplicabilityWorkflowResult(data);
    } catch (error) {
      setApplicabilityWorkflowError((error as Error).message);
      setApplicabilityWorkflowResult(null);
    }
  }, [selectedGuidelineId, currentCase, form.question]);

  const resumeApplicabilityWorkflow = useCallback(async (facts: Dict[]) => {
    const resolutionId = applicabilityWorkflowResult?.resolution_id;
    if (!resolutionId) {
      setApplicabilityWorkflowError("没有可续跑的解析");
      return;
    }
    setApplicabilityWorkflowError(null);
    try {
      const data = await postJson("/api/applicability", {
        resolution_id: resolutionId,
        supplied_facts: facts,
      });
      setApplicabilityWorkflowResult(data);
    } catch (error) {
      setApplicabilityWorkflowError((error as Error).message);
    }
  }, [applicabilityWorkflowResult]);

  const saveLiveGuideline = useCallback(
    async (doc: Dict) => {
      const meta = doc.metadata || {};
      const contentMode = meta.content_mode || "metadata_only";
      const payload = {
        document_id: doc.document_id,
        title: doc.title,
        pmid: doc.pmid,
        pmcid: meta.pmcid,
        doi: doc.doi,
        published_at: doc.published_at,
        version_id: doc.version_id,
        license: meta.license,
        rights_basis: meta.rights_basis,
        content_mode: contentMode,
        abstract: contentMode === "abstract" ? doc.text : "",
        source_url: doc.url,
        evidence_type: meta.evidence_type || "clinical_practice_guideline",
      };
      try {
        const result = await postJson("/api/guidelines/save-live", payload);
        notify(`已保存：${result.document_id} · ${result.content_mode}`, "success");
        await loadGuidelines();
      } catch (error) {
        notify(`保存失败：${(error as Error).message}`, "error");
      }
    },
    [loadGuidelines, notify],
  );

  /* ---------- 审计 ---------- */
  const runAudit = useCallback(async () => {
    if (!pkg) {
      setAuditHint("待检索");
      return;
    }
    const answer = auditAnswer.trim();
    if (!answer) {
      setAuditHint("回答不能为空");
      return;
    }
    setAuditRunning(true);
    try {
      const result = await postJson("/api/audit", {
        retrieval_id: pkg.retrieval_id,
        answer,
      });
      setAuditResult(result);
      setAuditHint(
        [
          `校验器：${result.verifier?.name || "unknown"}`,
          result.verifier?.model,
          result.verifier?.translation?.used ? result.verifier.translation.processor : null,
        ]
          .filter(Boolean)
          .join(" · "),
      );
      notify("审计完成", "success");
    } catch (error) {
      setAuditHint((error as Error).message);
      notify(`审计失败：${(error as Error).message}`, "error");
    } finally {
      setAuditRunning(false);
    }
  }, [pkg, auditAnswer, notify]);

  const openCitation = useCallback(
    async (citationId: string) => {
      if (!pkg) return;
      try {
        const opened = await postJson("/api/citation", {
          retrieval_id: pkg.retrieval_id,
          citation_id: citationId,
        });
        setCitationMessage(
          `引用已锁定：${opened.citation?.title || ""} · ${opened.citation?.locator || "定位已返回"}`,
        );
        notify("引用已打开", "success");
      } catch (error) {
        setCitationMessage((error as Error).message);
        notify((error as Error).message, "error");
      }
    },
    [pkg, notify],
  );

  /* ---------- 导航 ---------- */
  const goStep = useCallback((next: StepKey) => {
    setStep(next);
    setMaxVisited((prev) => Math.max(prev, STEP_ORDER.indexOf(next)));
    document.querySelector(".main")?.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const stepStatus = useMemo<Record<StepKey, { text: string; tone: BadgeTone }>>(() => {
    const questionReady = Boolean(form.question.trim());
    const termsReady = Boolean(form.patientTerms.trim() || form.termMapping.trim() !== "[]");
    return {
      case: snapshot
        ? { text: "快照就绪", tone: "done" }
        : caseId
          ? { text: "待载入", tone: "" }
          : { text: "待选择", tone: "" },
      question: questionReady ? { text: "已填写", tone: "ready" } : { text: "待填写", tone: "" },
      terms: termsReady ? { text: "已填写", tone: "ready" } : { text: "待填写", tone: "" },
      scope: { text: "已配置", tone: "ready" },
      method: health ? { text: "已读取", tone: "ready" } : { text: "待读取", tone: "" },
      request: requestPreview.error
        ? { text: "JSON 错误", tone: "error" }
        : questionReady
          ? { text: "可执行", tone: "ready" }
          : { text: "待组装", tone: "" },
      run: running
        ? { text: "执行中", tone: "running" }
        : runError
          ? { text: "失败", tone: "error" }
          : pkg
            ? { text: "完成", tone: "done" }
            : { text: "待执行", tone: "" },
      results: pkg
        ? { text: `${(pkg.ranked_documents || []).length} 条`, tone: "done" }
        : { text: "未检索", tone: "" },
      guidelines: !capabilities.guidelines
        ? { text: "未提供", tone: "" }
        : guidelineConfigured
          ? { text: guidelineChunks.length ? `${guidelineChunks.length} 块` : "待解析", tone: guidelineChunks.length ? "done" : "" }
          : { text: "未配置", tone: "" },
      audit: auditResult ? { text: "已审计", tone: "done" } : { text: "待审计", tone: "" },
    };
  }, [
    snapshot,
    caseId,
    form.question,
    form.patientTerms,
    form.termMapping,
    requestPreview.error,
    health,
    running,
    runError,
    pkg,
    guidelineConfigured,
    guidelineChunks.length,
    capabilities.guidelines,
    auditResult,
  ]);

  /* ---------- 启动 ---------- */
  const bootstrapped = useRef(false);
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    void (async () => {
      const healthData = await backendCheck();
      void loadCases();
      void refreshDemoGuidelines();
      // 只在后端声明支持指南解析时请求 /api/guidelines，避免旧后端 404。
      if (healthData?.guideline_parsing) void loadGuidelines();
    })();
  }, [loadCases, backendCheck, loadGuidelines, refreshDemoGuidelines]);

  const value: Workbench = {
    form,
    setForm,
    cases,
    caseId,
    currentCase,
    applyCase,
    snapshot,
    snapshotJson,
    setSnapshotJson,
    manualSnapshot,
    setManualSnapshot,
    snapshotState,
    loadSnapshot,
    requestPreview,
    resetForm,
    running,
    runError,
    pkg,
    selectedIndex,
    selectResult,
    sourceFilter,
    setSourceFilter,
    resultTab,
    setResultTab,
    patientTab,
    setPatientTab,
    runRetrieval,
    controllerPolicy,
    setControllerPolicy,
    controllerResult,
    controllerRunning,
    runController,
    health,
    capabilities,
    tools,
    sources,
    backendError,
    backendCheck,
    registeredCount,
    demoGuidelines,
    loadDemoSources,
    clearSources,
    rerankerInfo,
    applyReranker,
    industrialInfo,
    applyIndustrial,
    guidelines,
    guidelineOptions,
    guidelineConfigured,
    guidelineDataDir,
    guidelineStatus,
    guidelineError,
    selectedGuidelineId,
    selectGuideline,
    guidelineChunks,
    selectedChunkId,
    selectChunk,
    loadGuidelines,
    parseOptions,
    setParseOption,
    parseStatus,
    parseHint,
    parseGuidelines,
    pdfPages,
    pdfPage,
    showPdfPage,
    pdfMeta,
    pdfHint,
    applicabilityResult,
    applicabilityError,
    checkApplicability,
    applicabilityWorkflowResult,
    applicabilityWorkflowError,
    runApplicabilityWorkflow,
    resumeApplicabilityWorkflow,
    saveLiveGuideline,
    openGuidelineChunk,
    auditAnswer,
    setAuditAnswer,
    auditResult,
    auditRunning,
    auditHint,
    runAudit,
    openCitation,
    citationMessage,
    step,
    goStep,
    stepStatus,
    maxVisitedStep: maxVisited,
    apiLog,
    clearApiLog: () => setApiLog([]),
    theme,
    setTheme,
    toasts,
    notify,
    dismissToast,
    cmdOpen,
    setCmdOpen,
  };

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench(): Workbench {
  const context = useContext(WorkbenchContext);
  if (!context) throw new Error("useWorkbench 必须在 WorkbenchProvider 内使用");
  return context;
}

export { STEP_ORDER, caseDisplayLabel };
