import { useEffect, useState } from "react";
import { ApiError, get, type ReviewTask, type ReviewReport } from "../api";
import { ErrorState, Loading } from "../components/Feedback";
import { Report } from "../components/Report";
import {
  formatTime,
  ModeBadge,
  PublicationStatus,
  TaskStatus,
} from "../components/Status";
export function DetailPage({
  id,
  back,
  accepted,
}: {
  id: string;
  back: () => void;
  accepted: boolean;
}) {
  const [task, setTask] = useState<ReviewTask | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reportError, setReportError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setReportError(null);
    setReport(null);
    setTask(null);
    async function load() {
      try {
        const next = await get<ReviewTask>(`/tasks/${id}`, controller.signal);
        if (controller.signal.aborted) return;
        setTask(next);
        if (next.status === "completed" || next.status === "failed") {
          try {
            const result = await get<ReviewReport>(
              `/tasks/${id}/report`,
              controller.signal,
            );
            if (!controller.signal.aborted) setReport(result);
          } catch (err) {
            if (!controller.signal.aborted) setReportError(err);
          }
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [id, version]);
  return (
    <>
      <button className="back-link" onClick={back}>
        <span aria-hidden="true">←</span> 返回任务列表
      </button>
      <header className="page-heading detail-heading">
        <div>
          <div className="section-kicker">REVIEW REPORT</div>
          <h1>PR 审查详情</h1>
          <p>
            {task
              ? `${task.repository} / Pull Request #${task.pr_number}`
              : "核对审查结果与代码依据"}
          </p>
        </div>
        <button
          className="button secondary"
          onClick={() => setVersion((v) => v + 1)}
        >
          刷新详情
        </button>
      </header>
      {accepted && (
        <p className="notice accepted" role="status">
          提交已被服务受理。任务详情以服务端返回结果为准。
        </p>
      )}
      {error ? (
        <ErrorState error={error} retry={() => setVersion((v) => v + 1)} />
      ) : (
        <>
          {task && (
            <section className="panel snapshot" aria-label="任务与版本信息">
              <div>
                <span className="meta-label">审查状态</span>
                <TaskStatus status={task.status} />
              </div>
              <div>
                <span className="meta-label">GitHub 评论</span>
                <PublicationStatus status={task.publication_status} />
              </div>
              <div>
                <span className="meta-label">数据来源</span>
                <ModeBadge mock={task.is_mock} />
              </div>
              <div>
                <span className="meta-label">Head 版本</span>
                <code className="sha" title={task.head_sha}>
                  {task.head_sha}
                </code>
              </div>
              <div>
                <span className="meta-label">创建时间（北京）</span>
                <span>{formatTime(task.created_at)}</span>
              </div>
            </section>
          )}
          {loading && <Loading>正在加载审查详情…</Loading>}
          {task?.error && (
            <div className="notice danger" role="alert">
              <h2>任务失败原因</h2>
              <p>{task.error.message}</p>
              <code>{task.error.code}</code>
            </div>
          )}
          {!loading &&
            task &&
            (task.status === "queued" || task.status === "running") && (
              <div className="panel pending-report" role="status">
                <h2>
                  {task.status === "queued" ? "任务正在排队" : "审查正在进行"}
                </h2>
                <p>报告尚未生成，请稍后刷新详情查看结果。</p>
              </div>
            )}
          {reportError instanceof ApiError && reportError.status === 409 ? (
            <div className="panel pending-report" role="status">
              <h2>报告尚未就绪</h2>
              <p>请稍后刷新详情，当前没有可展示的报告。</p>
            </div>
          ) : (
            reportError != null && (
              <ErrorState
                error={reportError}
                retry={() => setVersion((v) => v + 1)}
              />
            )
          )}
          {report && <Report report={report} />}
        </>
      )}
    </>
  );
}
