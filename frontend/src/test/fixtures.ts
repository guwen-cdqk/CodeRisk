import shared from "../../../backend/tests/fixtures/review_example.json";
import type { ReviewTask, ReviewReport, TaskList } from "../api";
// Reuse the team's manually annotated fixture; no model or analyzer is run.
export const task: ReviewTask = {
  ...shared.task,
  status: "completed",
  publication_status: "not_requested",
  error: null,
};
export const report: ReviewReport = {
  ...shared.report,
  status: "completed",
  findings: shared.report.findings as ReviewReport["findings"],
  error: null,
  diagnostics: shared.report.diagnostics.map((d) => ({ ...d, path: null })),
};
export const taskList: TaskList = {
  items: [task],
  total: 1,
  limit: 20,
  offset: 0,
};
export function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
