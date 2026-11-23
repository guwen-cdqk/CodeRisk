import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { Report } from "./Report";
import { report } from "../test/fixtures";
it("keeps empty findings distinct from analysis limitations", () => {
  render(
    <Report
      report={{
        ...report,
        findings: [],
        diagnostics: [
          {
            code: "SOURCE_MISSING",
            path: "src/broken.py",
            message: "缺少全文，部分文件未分析。",
          },
        ],
      }}
    />,
  );
  expect(screen.getByText("无发现条目")).toBeVisible();
  expect(screen.getByText(/这不代表代码正确或安全/)).toBeVisible();
  expect(screen.getByText("SOURCE_MISSING")).toBeVisible();
  expect(screen.getByText("src/broken.py")).toBeVisible();
  expect(screen.getByText("缺少全文，部分文件未分析。")).toBeVisible();
});
it("renders HTML-like evidence as text and preserves old-side locations", () => {
  const evidence =
    '<img src=x onerror="window.__injected=true"><script>alert(1)</script>';
  const { container } = render(
    <Report
      report={{
        ...report,
        findings: [
          {
            ...report.findings[0],
            evidence,
            source: "ai",
            location: {
              path: "old/name.py",
              side: "old",
              start_line: 2,
              end_line: 4,
            },
          },
        ],
      }}
    />,
  );
  expect(screen.getByText(evidence)).toBeVisible();
  expect(container.querySelector("img, script")).toBeNull();
  expect(screen.getByText("2–4 行 · 修改前 · base")).toBeVisible();
  expect(screen.getByText("AI 候选意见")).toBeVisible();
});
it.each([
  ["critical", "严重"],
  ["high", "高风险"],
  ["medium", "中风险"],
  ["low", "低风险"],
] as const)("labels severity %s", (severity, label) => {
  render(
    <Report
      report={{ ...report, findings: [{ ...report.findings[0], severity }] }}
    />,
  );
  expect(screen.getByText(label)).toBeVisible();
});
