export type Dict = Record<string, any>;

export interface PatientSnapshot {
  patient_ref: string;
  as_of: string;
  facts?: Dict[];
  suppressed_future?: number;
  source?: string;
  [key: string]: unknown;
}

export interface DemoCase {
  case_id: string;
  label: string;
  synthetic?: boolean;
  snapshot: PatientSnapshot;
  preset: Dict;
}

export interface RetrievalRequest {
  question: string;
  task: string;
  query_language: string;
  clinical_intent: string | null;
  as_of: string | null;
  patient_terms: string[];
  term_mapping: Dict[];
  facets: Dict;
  filters: Dict;
  source_kinds: string[];
  source_mode: "live" | "custom";
  retrieval_method: string | null;
  rerank: boolean;
  fidelity_gate: string;
  top_k: number;
  patient_snapshot?: PatientSnapshot;
  [key: string]: unknown;
}

export interface ControllerPolicy {
  max_rounds: number;
  max_cost: number;
  min_evidence: number;
  min_coverage: number;
}

export interface ApiLogEntry {
  id: number;
  method: string;
  url: string;
  status: number | string;
  ms: number;
  at: string;
}

export interface HealthPayload {
  rag_version: string;
  active_mode: string;
  retrieval_methods: Record<string, { available: boolean }>;
  reranker: Dict;
  entity_linking: Dict;
  guideline_lifecycle: Dict;
  claim_verifier: Dict;
  pubmed: Dict;
  guideline_parsing: Dict;
  term_mapping_library: Dict;
  [key: string]: any;
}

export type StepKey =
  | "case"
  | "question"
  | "terms"
  | "scope"
  | "method"
  | "request"
  | "run"
  | "results"
  | "guidelines"
  | "audit";

export type BadgeTone = "" | "ready" | "done" | "running" | "error" | "warn";
