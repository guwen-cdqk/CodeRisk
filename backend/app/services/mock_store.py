"""Read-only demo fixture; NOT a database, task runner, or real review result."""

from datetime import UTC, datetime
from uuid import UUID

from app.schemas.models import ReviewReport, ReviewTask, TaskList

DEMO_ID = UUID("00000000-0000-4000-8000-000000000001")


class MockReadStore:
    def __init__(self) -> None:
        self.task = ReviewTask(
            id=DEMO_ID,
            repository="demo/coderisk",
            pr_number=1,
            head_sha="a" * 40,
            status="completed",
            is_mock=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.report = ReviewReport(
            task_id=DEMO_ID,
            head_sha=self.task.head_sha,
            status="completed",
            summary="MOCK：仅用于接口联调，未执行代码分析或 AI 审查；空发现不代表代码安全。",
            is_mock=True,
            generated_at=self.task.created_at,
        )

    def list_tasks(self, limit: int, offset: int) -> TaskList:
        return TaskList(
            items=[self.task][offset : offset + limit], total=1, limit=limit, offset=offset
        )
