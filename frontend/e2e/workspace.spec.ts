import { test, expect, type Page } from "@playwright/test";
import shared from "../../backend/tests/fixtures/review_example.json" with { type: "json" };

async function mockApi(page: Page) {
  await page.route("**/health", (route) =>
    route.fulfill({
      json: { mode: "mock", status: "ok", service: "CodeRisk" },
    }),
  );
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/tasks")
      return route.fulfill({
        json: { items: [shared.task], total: 1, offset: 0, limit: 20 },
      });
    if (path.endsWith("/report")) return route.fulfill({ json: shared.report });
    if (path === `/api/v1/tasks/${shared.task.id}`)
      return route.fulfill({ json: shared.task });
    return route.fulfill({
      status: 501,
      json: {
        error: {
          code: "NOT_IMPLEMENTED",
          message: "Review submission is not implemented",
        },
        request_id: "browser-test",
      },
    });
  });
}
test("list, evidence, history and responsive layout", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mockApi(page);
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  ).toBeVisible();
  await expect(page.getByRole("note")).toContainText("未执行真实审查");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("tasks.png"), fullPage: true });
  await page
    .getByRole("button", { name: "查看 demo/coderisk #1 报告" })
    .click();
  await expect(
    page.getByText("return eval(text)", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "分析限制与处理说明" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("report.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "返回任务列表" }).click();
  await page.getByRole("button", { name: /02.*历史记录/ }).click();
  await expect(page.getByRole("table")).toContainText("demo/coderisk");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("history.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("validates inputs and displays the real 501 contract without fake success", async ({
  page,
}, info) => {
  await mockApi(page);
  let submissions = 0;
  page.on("request", (req) => {
    if (req.method() === "POST") submissions++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "开始审查" }).click();
  await expect(page.getByLabel("GitHub 仓库")).toBeFocused();
  expect(submissions).toBe(0);
  await page.getByLabel("GitHub 仓库").fill("team/project");
  await page.getByLabel("Pull Request 编号").fill("12");
  await page.getByRole("button", { name: "开始审查" }).click();
  await expect(page.getByRole("alert")).toContainText("本次未创建任务");
  await expect(page.getByRole("alert")).toBeFocused();
  expect(submissions).toBe(1);
  await expect(
    page.getByRole("heading", { name: "把每一次修改，看清楚。" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("submission.png"),
    fullPage: true,
  });
});
test("keyboard can reach and open the report", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "查看 demo/coderisk #1 报告" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查看 demo/coderisk #1 报告" })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "PR 审查详情" }),
  ).toBeVisible();
  await expect(
    page.getByText("return eval(text)", { exact: true }),
  ).toBeVisible();
});
