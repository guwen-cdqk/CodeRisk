from uuid import UUID

from app.core.errors import ModuleError
from app.schemas.models import AnalysisResult, Diagnostic, PullRequestData, ReviewReport


class FakeReviewer:
    """Offline AIReviewer substitute, not an analyzer or evidence validator.

    completed means only that this fake call returned, never that AI ran or
    coverage was complete. Partial inputs retain explicit mock diagnostics;
    real degraded status handling belongs to C-03 after the shared contract changes.
    """

    async def review(
        self, task_id: UUID, pr: PullRequestData, analysis: AnalysisResult
    ) -> ReviewReport:
        if analysis.head_sha != pr.head_sha:
            raise ModuleError(
                "ANALYSIS_SHA_MISMATCH", "Analysis does not match the PR snapshot", status_code=422
            )
        if analysis.status == "failed":
            raise ModuleError("ANALYSIS_FAILED", "Cannot review a failed analysis", status_code=422)

        copied = analysis.model_copy(deep=True)
        copied.diagnostics.append(
            Diagnostic(code="MOCK_REVIEWER", message="No LLM was called; upstream findings only.")
        )
        summary = "MOCK：未执行 AI 审查，仅保留上游发现；空发现不代表代码安全。"
        if analysis.status == "partial":
            copied.diagnostics.append(
                Diagnostic(code="MOCK_PARTIAL_ANALYSIS", message="Input analysis is incomplete.")
            )
            summary += "输入分析不完整；completed 仅表示 Fake 调用完成。"

        return ReviewReport(
            task_id=task_id,
            head_sha=pr.head_sha,
            status="completed",
            summary=summary,
            findings=copied.findings,
            diagnostics=copied.diagnostics,
            is_mock=True,
        )
