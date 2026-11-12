export function esc(value: unknown): string {
  return String(value ?? "");
}

export function caseDisplayLabel(label: unknown): string {
  return String(label ?? "")
    .replace(/[()（）]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function score(value: unknown, digits = 3): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

export function rankDelta(preRank?: number | null, finalRank?: number | null): string {
  if (!preRank || !finalRank) return "—";
  const d = preRank - finalRank;
  if (d > 0) return `↑${d}`;
  if (d < 0) return `↓${-d}`;
  return "—";
}

export function pct(value: unknown, digits = 1): string {
  if (value === null || value === undefined) return "未测量";
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "未测量";
}

export function truncate(text: unknown, max = 120): string {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export const collectionLabels: Record<string, string> = {
  birth: "出生/成熟度",
  maternal: "母体",
  diagnoses: "诊断",
  postnatal: "生后时点",
  respiratory_support: "呼吸支持",
  vitals: "生命体征",
  blood_gas: "血气",
  labs: "检验",
  imaging: "影像",
  medications: "用药",
  therapies: "治疗",
  feeding: "喂养",
  risk_factors: "风险因素",
  reports: "记录",
};

export const kindLabels: Record<string, string> = {
  guideline: "指南",
  pubmed: "PubMed",
  drug: "药品",
  clinical_trial: "临床试验",
  local_knowledge: "本地知识",
};

export const sourceLabels: Record<string, string> = {
  "neonatal-guideline-metadata": "指南元数据",
  "neonatal-guideline-synthetic": "合成指南",
  "neonatal-literature-synthetic": "合成文献",
};

export const collectionColors: Record<string, string> = {
  birth: "#2563a8",
  maternal: "#7c4dad",
  diagnoses: "#b04545",
  postnatal: "#0d7f72",
  respiratory_support: "#116579",
  vitals: "#b06a0a",
  blood_gas: "#1a7f5a",
  labs: "#b06a0a",
  imaging: "#2563a8",
  medications: "#1a7f5a",
  therapies: "#7c4dad",
  feeding: "#0d7f72",
  risk_factors: "#a23535",
  reports: "#5b6b78",
};

export function parseJsonObject(raw: string, label: string): Record<string, unknown> {
  const text = raw.trim();
  if (!text) return {};
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new Error(`${label} JSON 无效：${(error as Error).message}`);
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  return value as Record<string, unknown>;
}

export function parseTermMapping(raw: string): Record<string, any>[] {
  const text = raw.trim();
  if (!text) return [];
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new Error(`term_mapping JSON 无效：${(error as Error).message}`);
  }
  if (value === null) return [];
  if (Array.isArray(value)) {
    value.forEach((row, index) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) {
        throw new Error(`term_mapping[${index}] 必须是对象`);
      }
    });
    return value as Record<string, any>[];
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, any>).map(([concept, payload]) => ({
      concept,
      ...(Array.isArray(payload) ? { english: payload } : payload),
    }));
  }
  throw new Error("term_mapping 必须是数组或对象");
}
