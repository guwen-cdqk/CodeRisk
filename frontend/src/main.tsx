import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { get, type ReviewReport, type TaskList } from "./api";
import "./style.css";

function App() {
  const [page, setPage] = useState<"tasks" | "detail" | "history">("tasks");
  const [tasks, setTasks] = useState<TaskList | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    get<TaskList>("/tasks").then(setTasks).catch((e: Error) => setError(e.message));
  }, []);
  async function open(id: string) {
    setPage("detail"); setReport(null); setError(""); setLoading(true);
    try { setReport(await get<ReviewReport>(`/tasks/${id}/report`)); }
    catch (e) { setError(e instanceof Error ? e.message : "加载失败"); }
    finally { setLoading(false); }
  }
  return <main>
    <h1>CodeRisk</h1>
    <p role="note">Mock 联调模式 · 当前未连接 GitHub、分析引擎或 LLM。示例数据不代表真实审查。</p>
    <nav><button onClick={() => setPage("tasks")}>审查任务</button><button onClick={() => setPage("history")}>统计与历史</button></nav>
    {error && <p role="alert">{error}</p>}
    {page === "tasks" && <section><h2>审查任务列表</h2>
      {!tasks && !error && <p>加载中…</p>}
      {tasks?.items.length === 0 && <p>暂无任务</p>}
      {tasks?.items.map(t => <article key={t.id}>
        <button onClick={() => void open(t.id)}>{t.repository} #{t.pr_number}</button>
        <p>{t.status} · {t.is_mock ? "MOCK" : "真实任务"} · {t.head_sha.slice(0, 7)}</p>
      </article>)}
    </section>}
    {page === "detail" && <section><h2>PR 审查详情</h2>
      {loading && <p>加载中…</p>}
      {report && <><p>{report.summary}</p>{report.findings.length === 0 && <p>无发现条目；这不代表代码正确或安全。</p>}
        {report.findings.map((f, i) => <article key={i}><h3>{f.severity} · {f.category}</h3>
          <p>{f.location.path}:{f.location.start_line}–{f.location.end_line} ({f.location.side})</p>
          <p>{f.reason}</p><pre>{f.evidence}</pre><p>{f.suggestion}</p></article>)}</>}
    </section>}
    {page === "history" && <section><h2>基础统计与历史记录</h2>
      <p>当前示例任务总数：{tasks?.total ?? "加载中"}。真实统计 API 与历史筛选由 D 后续实现。</p>
      {tasks?.items.map(t => <p key={t.id}>{t.created_at} · {t.repository} #{t.pr_number} · {t.status}</p>)}
    </section>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
