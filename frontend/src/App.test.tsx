import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { task, report, taskList, json } from "./test/fixtures";

let handle: (
  url: string,
  options?: RequestInit,
) => Response | Promise<Response>;
beforeEach(() => {
  handle = (url) => {
    if (url === "/health")
      return json({ status: "ok", service: "CodeRisk", mode: "mock" });
    if (url === "/api/v1/tasks") return json(taskList);
    if (url === `/api/v1/tasks/${task.id}`) return json(task);
    if (url.endsWith("/report")) return json(report);
    return json(
      {
        error: {
          code: "NOT_IMPLEMENTED",
          message: "Review submission is not implemented",
        },
        request_id: "request-123",
      },
      501,
    );
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options?: RequestInit) =>
      Promise.resolve(handle(url, options)),
    ),
  );
});

it("navigates list → evidence report → back → history with explicit mock labels", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByText("Mock 联调环境")).toBeVisible();
  expect(screen.getByRole("note")).toHaveTextContent("未执行真实审查");
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByText("return eval(text)")).toBeVisible();
  expect(screen.getByText("src/parser.py")).toBeVisible();
  expect(screen.getByText("2–2 行 · 修改后 · head")).toBeVisible();
  expect(screen.getByText("Untrusted input may execute code.")).toBeVisible();
  expect(
    screen.getByText("Use a parser for the expected input format."),
  ).toBeVisible();
  expect(
    screen.getByText("No LLM was called; upstream findings only."),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "返回任务列表" }));
  expect(
    screen.getByRole("heading", { name: "把每一次修改，看清楚。" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: /02.*历史记录/ }));
  expect(screen.getByRole("table")).toHaveTextContent("demo/coderisk");
  expect(
    screen.getByText("当前展示 1 条，服务端共返回 1 条记录。"),
  ).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "打开 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByText("return eval(text)")).toBeVisible();
});

it("shows loading followed by an empty list and empty history", async () => {
  let resolve!: (value: Response) => void;
  const base = handle;
  handle = (url) =>
    url === "/api/v1/tasks"
      ? new Promise((r) => {
          resolve = r;
        })
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByRole("status")).toHaveTextContent("正在加载任务");
  await act(async () => resolve(json({ ...taskList, items: [], total: 0 })));
  expect(await screen.findByText("还没有审查任务")).toBeVisible();
  await user.click(screen.getByRole("button", { name: /02.*历史记录/ }));
  expect(screen.getByText("暂无历史记录")).toBeVisible();
});

it("shows a network error then reloads successfully", async () => {
  const base = handle;
  let broken = true;
  handle = (url) => {
    if (url === "/api/v1/tasks" && broken)
      return Promise.reject(new TypeError("offline"));
    return base(url);
  };
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("连接失败");
  broken = false;
  await user.click(screen.getByRole("button", { name: "重新加载" }));
  expect(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  ).toBeVisible();
});

it("rejects invalid form inputs locally and focuses the first invalid field", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "开始审查" }));
  expect(screen.getByLabelText("GitHub 仓库")).toHaveFocus();
  expect(screen.getByText(/请输入 owner\/repo 格式/)).toBeVisible();
  expect(screen.getByText("请输入大于 0 的整数 PR 编号。")).toBeVisible();
  await user.type(screen.getByLabelText("GitHub 仓库"), "team/project");
  await user.type(screen.getByLabelText("Pull Request 编号"), "1.5");
  await user.click(screen.getByRole("button", { name: "开始审查" }));
  expect(screen.getByLabelText("Pull Request 编号")).toHaveFocus();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([, options]) => options?.method === "POST"),
  ).toHaveLength(0);
});

it("disables repeat submits and handles the current 501 without creating a task", async () => {
  let resolve!: (value: Response) => void;
  const base = handle;
  handle = (url, options) =>
    options?.method === "POST"
      ? new Promise((r) => {
          resolve = r;
        })
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.type(screen.getByLabelText("GitHub 仓库"), " team/project ");
  await user.type(screen.getByLabelText("Pull Request 编号"), "12");
  await user.dblClick(screen.getByRole("button", { name: "开始审查" }));
  expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
  expect(screen.getByLabelText("GitHub 仓库")).toBeDisabled();
  const posts = vi
    .mocked(fetch)
    .mock.calls.filter(([, options]) => options?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string)).toEqual({
    repository: "team/project",
    pr_number: 12,
  });
  await act(async () =>
    resolve(
      json(
        {
          error: { code: "NOT_IMPLEMENTED", message: "Not implemented" },
          request_id: "request-123",
        },
        501,
      ),
    ),
  );
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("本次未创建任务");
  expect(alert).toHaveTextContent("request-123");
  expect(alert).toHaveFocus();
  expect(
    screen.getByRole("heading", { name: "把每一次修改，看清楚。" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "开始审查" })).toBeEnabled();
});

it("opens the server-issued queued task on a fake 202, without fabricating a report", async () => {
  const queued = { ...task, status: "queued" };
  const base = handle;
  handle = (url, options) =>
    options?.method === "POST"
      ? json(queued, 202)
      : url === `/api/v1/tasks/${task.id}`
        ? json(queued)
        : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.type(screen.getByLabelText("GitHub 仓库"), task.repository);
  await user.type(screen.getByLabelText("Pull Request 编号"), "1");
  await user.click(screen.getByRole("button", { name: "开始审查" }));
  expect(await screen.findByText("任务正在排队")).toBeVisible();
  expect(screen.getByText(/提交已被服务受理/)).toBeVisible();
  expect(screen.queryByText("无发现条目")).not.toBeInTheDocument();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/report")),
  ).toHaveLength(0);
});

it.each(["queued", "running"] as const)(
  "shows %s without pretending a report exists",
  async (status) => {
    const base = handle;
    handle = (url) =>
      url === `/api/v1/tasks/${task.id}`
        ? json({ ...task, status })
        : base(url);
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
    );
    expect(
      await screen.findByText(
        status === "queued" ? "任务正在排队" : "审查正在进行",
      ),
    ).toBeVisible();
    expect(screen.queryByText("无发现条目")).not.toBeInTheDocument();
  },
);

it("keeps publication failure separate from a saved completed report", async () => {
  const base = handle;
  handle = (url) =>
    url === `/api/v1/tasks/${task.id}`
      ? json({ ...task, publication_status: "failed" })
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByText("发布失败")).toBeVisible();
  expect(await screen.findByText("return eval(text)")).toBeVisible();
  expect(
    within(screen.getByRole("region", { name: "任务与版本信息" })).getByText(
      "已完成",
    ),
  ).toBeVisible();
});

it("shows task failure and report failure details", async () => {
  const failure = {
    code: "ANALYSIS_FAILED",
    message: "分析未能完成",
    retryable: false,
  };
  const base = handle;
  handle = (url) =>
    url === `/api/v1/tasks/${task.id}`
      ? json({ ...task, status: "failed", error: failure })
      : url.endsWith("/report")
        ? json({ ...report, status: "failed", error: failure, findings: [] })
        : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(
    await screen.findByRole("heading", { name: "审查未完成" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "任务失败原因" })).toBeVisible();
  expect(screen.getAllByText("ANALYSIS_FAILED")).toHaveLength(2);
});

it("shows report-not-ready separately from empty findings", async () => {
  const base = handle;
  handle = (url) =>
    url.endsWith("/report")
      ? json({ error: { code: "REPORT_NOT_READY", message: "Not ready" } }, 409)
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByText("报告尚未就绪")).toBeVisible();
  expect(screen.queryByText("无发现条目")).not.toBeInTheDocument();
});

it("does not show an old response after leaving a detail page", async () => {
  let resolve!: (value: Response) => void;
  const base = handle;
  handle = (url) =>
    url.endsWith("/report")
      ? new Promise((r) => {
          resolve = r;
        })
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  await waitFor(() => expect(resolve).toBeDefined());
  await user.click(screen.getByRole("button", { name: "返回任务列表" }));
  await act(async () => resolve(json(report)));
  expect(screen.queryByText("return eval(text)")).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "把每一次修改，看清楚。" }),
  ).toBeVisible();
});

it("does not label an unconfigured service as mock", async () => {
  const base = handle;
  handle = (url) =>
    url === "/health" ? json({ mode: "unconfigured" }) : base(url);
  render(<App />);
  expect(await screen.findByText("服务未配置")).toBeVisible();
  expect(screen.queryByText("Mock 联调环境")).not.toBeInTheDocument();
});

it("renders a completed empty report with an explicit safety limitation", async () => {
  const base = handle;
  handle = (url) =>
    url.endsWith("/report")
      ? json({ ...report, findings: [], diagnostics: [] })
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByText("无发现条目")).toBeVisible();
  expect(screen.getByText(/这不代表代码正确或安全/)).toBeVisible();
});

it("handles a missing task without showing an empty successful report", async () => {
  const base = handle;
  handle = (url) =>
    url === `/api/v1/tasks/${task.id}`
      ? json(
          { error: { code: "TASK_NOT_FOUND", message: "Task not found" } },
          404,
        )
      : base(url);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "未找到该任务或报告",
  );
  expect(screen.queryByText("无发现条目")).not.toBeInTheDocument();
});
