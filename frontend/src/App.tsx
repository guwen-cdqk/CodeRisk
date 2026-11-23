import { useEffect, useRef, useState } from "react";
import {
  get,
  getHealth,
  type Health,
  type ReviewTask,
  type TaskList,
} from "./api";
import { DetailPage } from "./pages/DetailPage";
import { HistoryPage } from "./pages/HistoryPage";
import { TasksPage } from "./pages/TasksPage";
type Page =
  | { name: "tasks" | "history" }
  | { name: "detail"; id: string; accepted?: boolean };
export function App() {
  const [page, setPage] = useState<Page>({ name: "tasks" });
  const [tasks, setTasks] = useState<TaskList | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);
  const [mode, setMode] = useState<Health["mode"] | "unknown">("unknown");
  const content = useRef<HTMLElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setTasks(null);
    setError(null);
    get<TaskList>("/tasks", controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setTasks(value);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err);
      });
    return () => controller.abort();
  }, [version]);
  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => {
        if (!controller.signal.aborted) setMode(health.mode);
      })
      .catch(() => {
        if (!controller.signal.aborted) setMode("unknown");
      });
    return () => controller.abort();
  }, []);
  function navigate(next: Page) {
    setPage(next);
    content.current?.focus();
  }
  function open(id: string) {
    navigate({ name: "detail", id });
  }
  function accepted(task: ReviewTask) {
    navigate({ name: "detail", id: task.id, accepted: true });
    setVersion((v) => v + 1);
  }
  const reload = () => setVersion((v) => v + 1);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            cr<span>.</span>
          </span>
          <span>CodeRisk</span>
        </div>
        <div className="sidebar-section">审查工作台</div>
        <nav aria-label="主导航">
          <button
            className={`nav-item ${page.name !== "history" ? "active" : ""}`}
            onClick={() => navigate({ name: "tasks" })}
            aria-current={page.name === "tasks" ? "page" : undefined}
          >
            <span className="nav-number">01</span>审查任务
            <span aria-hidden="true">↗</span>
          </button>
          <button
            className={`nav-item ${page.name === "history" ? "active" : ""}`}
            onClick={() => navigate({ name: "history" })}
            aria-current={page.name === "history" ? "page" : undefined}
          >
            <span className="nav-number">02</span>历史记录
            <span aria-hidden="true">↗</span>
          </button>
        </nav>
        <div className="sidebar-bottom">
          <span className="sidebar-status">
            <span />
            团队内部工作台
          </span>
          <p>
            让风险有位置，
            <br />
            让判断有依据。
          </p>
          <span className="sidebar-caption">PYTHON PR REVIEW</span>
        </div>
      </aside>
      <div className="main-shell">
        <div className="topbar">
          <span>
            工作台 <span className="breadcrumb-divider">/</span>{" "}
            {page.name === "detail"
              ? "审查详情"
              : page.name === "history"
                ? "历史记录"
                : "审查任务"}
          </span>
          <span className="environment-tag">
            {mode === "mock"
              ? "Mock 联调环境"
              : mode === "unconfigured"
                ? "服务未配置"
                : "连接状态待确认"}
          </span>
        </div>
        <main id="main-content" ref={content} tabIndex={-1}>
          <div className="environment-note" role="note">
            <span className="note-label">
              {mode === "mock" ? "MOCK" : "提示"}
            </span>
            <p>
              {mode === "mock"
                ? "当前展示联调数据，未执行真实审查。示例结果不代表代码正确或安全。"
                : mode === "unconfigured"
                  ? "真实审查服务尚未配置，请等待服务接入。"
                  : "尚未确认服务运行模式，请以任务与报告中的数据来源标识为准。"}
            </p>
          </div>
          {page.name === "tasks" && (
            <TasksPage
              tasks={tasks}
              error={error}
              reload={reload}
              open={open}
              accepted={accepted}
            />
          )}
          {page.name === "history" && (
            <HistoryPage
              tasks={tasks}
              error={error}
              reload={reload}
              open={open}
            />
          )}
          {page.name === "detail" && (
            <DetailPage
              key={page.id}
              id={page.id}
              accepted={Boolean(page.accepted)}
              back={() => navigate({ name: "tasks" })}
            />
          )}
          <footer className="workspace-footer">
            <span>CodeRisk · 人工审查的辅助工具</span>
            <span>审查意见需人工核对</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
