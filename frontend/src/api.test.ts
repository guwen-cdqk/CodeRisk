import { expect, it, vi } from "vitest";
import { ApiError, errorMessage, get } from "./api";
it.each([
  [404, "未找到该任务或报告"],
  [422, "请求内容不符合要求"],
  [501, "本次未创建任务"],
  [503, "服务暂不可用"],
])("maps HTTP %i to actionable wording", (status, text) => {
  expect(
    errorMessage(new ApiError(status, "TEST", "Service message")),
  ).toContain(text);
});
it("handles a non-JSON server error without losing HTTP status", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("bad gateway", { status: 502 })),
  );
  await expect(get("/tasks")).rejects.toMatchObject({
    status: 502,
    code: "502",
    message: "请求失败",
  });
});
it("preserves the server request ID", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "TASK_NOT_FOUND", message: "Not found" },
            request_id: "trace-123",
          }),
          { status: 404 },
        ),
      ),
  );
  await expect(get("/tasks/missing")).rejects.toMatchObject({
    requestId: "trace-123",
    code: "TASK_NOT_FOUND",
  });
});
