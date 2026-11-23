import type { Finding, ReviewReport } from "../api";
import { formatTime, ModeBadge } from "./Status";
const severityLabels = {
  critical: "严重",
  high: "高风险",
  medium: "中风险",
  low: "低风险",
};
function FindingCard({ finding, index }: { finding: Finding; index: number }) {
  return (
    <article className="finding" aria-labelledby={`finding-${index}`}>
      <div className="finding-header">
        <span className={`badge severity-${finding.severity}`}>
          {severityLabels[finding.severity]}
        </span>
        <span className="muted">
          {finding.source === "static" ? "静态规则" : "AI 候选意见"}
        </span>
        {finding.rule_id && <code className="rule-id">{finding.rule_id}</code>}
      </div>
      <h3 id={`finding-${index}`}>{finding.category}</h3>
      <p className="location">
        <code>{finding.location.path}</code>
        <span>
          {finding.location.start_line}–{finding.location.end_line} 行 ·{" "}
          {finding.location.side === "old" ? "修改前 · base" : "修改后 · head"}
        </span>
      </p>
      <dl className="finding-copy">
        <dt>原因</dt>
        <dd>{finding.reason}</dd>
      </dl>
      <div className="evidence">
        <div className="evidence-label">
          代码证据<span>纯文本</span>
        </div>
        <pre>
          <code>{finding.evidence}</code>
        </pre>
      </div>
      <dl className="finding-copy">
        <dt>建议</dt>
        <dd>{finding.suggestion}</dd>
      </dl>
    </article>
  );
}
export function Report({ report }: { report: ReviewReport }) {
  return (
    <div className="report-content">
      <section className="panel report-summary" aria-labelledby="summary-title">
        <div className="section-heading">
          <h2 id="summary-title">审查摘要</h2>
          <ModeBadge mock={report.is_mock} />
        </div>
        <p>{report.summary}</p>
        <p className="report-timestamp">
          生成于 {formatTime(report.generated_at)}（北京时间）
        </p>
      </section>
      {report.status === "failed" && (
        <section className="notice danger" role="alert">
          <h2>审查未完成</h2>
          <p>{report.error?.message ?? "本次审查失败，请核对任务错误信息。"}</p>
          {report.error && <code>{report.error.code}</code>}
        </section>
      )}
      {report.diagnostics.length > 0 && (
        <section className="notice warning" aria-labelledby="limits-title">
          <h2 id="limits-title">分析限制与处理说明</h2>
          <p>请结合以下说明理解结果；部分内容可能未完成分析。</p>
          <ul>
            {report.diagnostics.map((diagnostic, i) => (
              <li key={i}>
                <code>{diagnostic.code}</code>
                {diagnostic.path && (
                  <span className="diagnostic-path">{diagnostic.path}</span>
                )}
                <p>{diagnostic.message}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section aria-labelledby="findings-title">
        <div className="section-heading findings-heading">
          <h2 id="findings-title">
            候选问题 <span className="count">{report.findings.length}</span>
          </h2>
          <span className="muted">逐条核对代码与触发条件</span>
        </div>
        {report.findings.length === 0 ? (
          <div className="panel no-findings">
            <h3>无发现条目</h3>
            <p>
              这不代表代码正确或安全。请结合审查状态、分析限制和人工检查判断。
            </p>
          </div>
        ) : (
          report.findings.map((finding, i) => (
            <FindingCard key={i} finding={finding} index={i} />
          ))
        )}
      </section>
    </div>
  );
}
