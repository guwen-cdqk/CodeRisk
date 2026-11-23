import type { ReviewTask, TaskList } from "../api";
import { EmptyState, ErrorState, Loading } from "../components/Feedback";
import { ReviewForm } from "../components/ReviewForm";
import { formatTime, ModeBadge, TaskStatus } from "../components/Status";
export function TasksPage({
  tasks,
  error,
  reload,
  open,
  accepted,
}: {
  tasks: TaskList | null;
  error: unknown;
  reload: () => void;
  open: (id: string) => void;
  accepted: (task: ReviewTask) => void;
}) {
  return (
    <>
      <header className="page-heading">
        <div>
          <div className="section-kicker">REVIEW WORKSPACE</div>
          <h1>把每一次修改，看清楚。</h1>
          <p>从任务到代码证据，让审查有据可循。</p>
        </div>
        <span className="page-index" aria-hidden="true">
          01 / REVIEWS
        </span>
      </header>
      <div className="workspace-grid">
        <section className="panel task-panel" aria-labelledby="tasks-title">
          <div className="panel-heading">
            <div>
              <h2 id="tasks-title">
                审查任务{" "}
                <span className="count">{tasks?.items.length ?? "—"}</span>
              </h2>
              <p>当前返回的任务 · 按服务端顺序展示</p>
            </div>
            <button className="text-button" onClick={reload}>
              刷新列表
            </button>
          </div>
          {error ? (
            <ErrorState error={error} retry={reload} />
          ) : !tasks ? (
            <Loading>正在加载任务…</Loading>
          ) : tasks.items.length === 0 ? (
            <EmptyState title="还没有审查任务">
              填写下方或右侧表单，发起第一份 PR 审查。
            </EmptyState>
          ) : (
            <div className="task-list">
              {tasks.items.map((task) => (
                <article className="task-row" key={task.id}>
                  <div className="task-row-main">
                    <div className="task-identity">
                      <span className="pr-symbol" aria-hidden="true">
                        ⑂
                      </span>
                      <div>
                        <button
                          className="task-link"
                          onClick={() => open(task.id)}
                        >
                          {task.repository} <span>#{task.pr_number}</span>
                        </button>
                        <div className="task-meta">
                          <code>{task.head_sha.slice(0, 7)}</code>
                          <span>·</span>
                          <time dateTime={task.created_at}>
                            {formatTime(task.created_at)}
                          </time>
                        </div>
                      </div>
                    </div>
                    <TaskStatus status={task.status} />
                  </div>
                  <div className="task-row-footer">
                    <ModeBadge mock={task.is_mock} />
                    <button
                      className="text-button"
                      aria-label={`查看 ${task.repository} #${task.pr_number} 报告`}
                      onClick={() => open(task.id)}
                    >
                      查看报告 <span aria-hidden="true">↗</span>
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
          <div className="panel-footnote">
            所有时间均为北京时间。空发现不代表代码正确或安全。
          </div>
        </section>
        <ReviewForm onAccepted={accepted} />
      </div>
    </>
  );
}
