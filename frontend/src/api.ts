// Current backend contract. Proposed states are intentionally not enabled here.
export interface ErrorDetail {
  code: string;
  message: string;
  retryable: boolean;
}
export interface Diagnostic {
  code: string;
  message: string;
  path: string | null;
}
export interface ReviewTask {
  id: string;
  repository: string;
  pr_number: number;
  head_sha: string;
  status: "queued" | "running" | "completed" | "failed";
  publication_status: "not_requested" | "pending" | "published" | "failed";
  error: ErrorDetail | null;
  created_at: string;
  is_mock: boolean;
}
export interface Finding {
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  location: {
    path: string;
    side: "old" | "new";
    start_line: number;
    end_line: number;
  };
  reason: string;
  evidence: string;
  suggestion: string;
  source: "static" | "ai";
  rule_id?: string | null;
}
export interface ReviewReport {
  task_id: string;
  head_sha: string;
  status: "completed" | "failed";
  summary: string;
  findings: Finding[];
  diagnostics: Diagnostic[];
  error: ErrorDetail | null;
  generated_at: string;
  is_mock: boolean;
}
export interface TaskList {
  items: ReviewTask[];
  total: number;
  limit: number;
  offset: number;
}
export interface CreateReviewRequest {
  repository: string;
  pr_number: number;
}
export interface Health {
  mode: "mock" | "unconfigured";
  status: "ok";
  service: string;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
}
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      body?.error?.code ?? String(response.status),
      body?.error?.message ?? "请求失败",
      body?.request_id,
    );
  }
  return response.json() as Promise<T>;
}
export function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(`/api/v1${path}`, { signal });
}
export function getHealth(signal?: AbortSignal): Promise<Health> {
  return request<Health>("/health", { signal });
}
export function createReview(
  body: CreateReviewRequest,
  signal?: AbortSignal,
): Promise<ReviewTask> {
  return request<ReviewTask>("/api/v1/reviews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 501)
      return "审查流程尚未接通。本次未创建任务，请等待服务接入后再提交。";
    if (error.status === 503) return "服务暂不可用，请稍后重试。";
    if (error.status === 404) return "未找到该任务或报告，请返回任务列表。";
    if (error.status === 422)
      return "请求内容不符合要求，请检查仓库名称和 PR 编号。";
    return `${error.message}（${error.code}）`;
  }
  return "连接失败，请检查网络或后端服务后重试。";
}
