import type { ApiLogEntry } from "../types";

let logSink: ((entry: Omit<ApiLogEntry, "id">) => void) | null = null;
let counter = 0;

export function setApiLogger(sink: ((entry: Omit<ApiLogEntry, "id">) => void) | null) {
  logSink = sink;
}

function record(method: string, url: string, status: number | string, ms: number) {
  counter += 1;
  logSink?.({
    method,
    url,
    status,
    ms,
    at: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
  });
}

export class ApiError extends Error {
  status: number;
  exceptionType?: string;
  errorId?: string;

  constructor(message: string, status: number, exceptionType?: string, errorId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.exceptionType = exceptionType;
    this.errorId = errorId;
  }
}

/** 真实 HTTP 封装：所有业务动作都落到后端，不做前端假数据。 */
export async function requestJson<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const started = performance.now();
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    record(method, url, "网络错误", Math.round(performance.now() - started));
    throw new ApiError(`网络请求失败：${(error as Error).message}`, 0);
  }
  const raw = await response.text();
  let data: any = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    data = { error: raw || `请求失败 · ${response.status}` };
  }
  record(method, url, response.status, Math.round(performance.now() - started));
  if (!response.ok) {
    const parts = [data.error || `请求失败 · ${response.status}`];
    if (data.exception_type) parts.push(`异常: ${data.exception_type}`);
    if (data.error_id) parts.push(`错误 ID: ${data.error_id}`);
    throw new ApiError(parts.join(" | "), response.status, data.exception_type, data.error_id);
  }
  return data as T;
}

export function postJson<T = any>(url: string, payload: unknown): Promise<T> {
  return requestJson<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
