import type { ReviewTask } from "../api";
const taskLabels = {
  queued: "排队中",
  running: "审查中",
  completed: "已完成",
  failed: "审查失败",
};
const publicationLabels = {
  not_requested: "未请求发布",
  pending: "等待发布",
  published: "已发布",
  failed: "发布失败",
};
export function TaskStatus({ status }: { status: ReviewTask["status"] }) {
  return (
    <span className={`badge status-${status}`}>
      <span className="status-dot" />
      {taskLabels[status]}
    </span>
  );
}
export function PublicationStatus({
  status,
}: {
  status: ReviewTask["publication_status"];
}) {
  return (
    <span
      className={`badge ${status === "failed" ? "status-failed" : "badge-neutral"}`}
    >
      {publicationLabels[status]}
    </span>
  );
}
export function ModeBadge({ mock }: { mock: boolean }) {
  return (
    <span className={`badge ${mock ? "badge-mock" : "badge-neutral"}`}>
      {mock ? "MOCK 示例" : "真实任务"}
    </span>
  );
}
export function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Shanghai",
        hour12: false,
      }).format(date);
}
