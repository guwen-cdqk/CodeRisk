import type { TaskList } from "../api";
import { EmptyState, ErrorState, Loading } from "../components/Feedback";
import { formatTime, ModeBadge, TaskStatus } from "../components/Status";
export function HistoryPage({
  tasks,
  error,
  reload,
  open,
}: {
  tasks: TaskList | null;
  error: unknown;
  reload: () => void;
  open: (id: string) => void;
}) {
  return (
    <>
      <header className="page-heading">
        <div>
          <div className="section-kicker">REVIEW ARCHIVE</div>
          <h1>历史记录</h1>
          <p>回看每次审查，核对对应的代码版本。</p>
        </div>
        <span className="page-index" aria-hidden="true">
          02 / HISTORY
        </span>
      </header>
      <section className="panel history-panel" aria-labelledby="history-title">
        <div className="panel-heading">
          <div>
            <h2 id="history-title">任务记录</h2>
            <p>
              {tasks
                ? `当前展示 ${tasks.items.length} 条，服务端共返回 ${tasks.total} 条记录。`
                : "读取当前任务查询结果"}
            </p>
          </div>
          <button className="text-button" onClick={reload}>
            刷新记录
          </button>
        </div>
        {error ? (
          <ErrorState error={error} retry={reload} />
        ) : !tasks ? (
          <Loading>正在加载记录…</Loading>
        ) : tasks.items.length === 0 ? (
          <EmptyState title="暂无历史记录">
            任务创建后，可在这里查看对应的版本与报告。
          </EmptyState>
        ) : (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="审查历史，可左右滚动查看完整记录"
          >
            <p className="history-scroll-hint">左右滑动表格，查看时间和报告入口。</p>
            <table>
              <caption className="sr-only">当前查询范围内的审查历史</caption>
              <thead>
                <tr>
                  <th scope="col">仓库 / PR</th>
                  <th scope="col">提交版本</th>
                  <th scope="col">审查状态</th>
                  <th scope="col">创建时间（北京）</th>
                  <th scope="col">报告</th>
                </tr>
              </thead>
              <tbody>
                {tasks.items.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <strong>{task.repository}</strong>
                      <div className="history-pr">
                        #{task.pr_number} <ModeBadge mock={task.is_mock} />
                      </div>
                    </td>
                    <td>
                      <code title={task.head_sha}>
                        {task.head_sha.slice(0, 7)}
                      </code>
                    </td>
                    <td>
                      <TaskStatus status={task.status} />
                    </td>
                    <td>
                      <time dateTime={task.created_at}>
                        {formatTime(task.created_at)}
                      </time>
                    </td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => open(task.id)}
                        aria-label={`打开 ${task.repository} #${task.pr_number} 报告`}
                      >
                        打开 <span aria-hidden="true">↗</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="panel-footnote">
          本页展示当前查询范围，不作为全库审查效果统计。
        </div>
      </section>
    </>
  );
}
