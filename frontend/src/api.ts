// MVP read DTOs; backend OpenAPI is the source of truth. D generates complete types in D-02.
export interface ReviewTask {
  id: string;
  repository: string;
  pr_number: number;
  head_sha: string;
  status: "queued" | "running" | "completed" | "failed";
  publication_status: "not_requested" | "pending" | "published" | "failed";
  created_at: string;
  is_mock: boolean;
}
export interface Finding {
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  location: { path: string; side: "old" | "new"; start_line: number; end_line: number };
  reason: string;
  evidence: string;
  suggestion: string;
  source: "static" | "ai";
}
export interface ReviewReport {
  task_id: string;
  status: "completed" | "failed";
  summary: string;
  findings: Finding[];
  is_mock: boolean;
}
export interface TaskList { items: ReviewTask[]; total: number; limit: number; offset: number }

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(`${body?.error?.code || response.status}: ${body?.error?.message || "请求失败"}`);
  }
  return response.json() as Promise<T>;
}
