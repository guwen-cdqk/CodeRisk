"""Dependency direction: implementations -> these ports + schemas, never each other."""

from typing import Protocol
from uuid import UUID

from app.schemas.models import (
    AnalysisResult,
    CreateReviewRequest,
    PullRequestData,
    ReviewReport,
    ReviewTask,
    TaskList,
)


class GitHubGateway(Protocol):
    async def fetch_pull_request(self, repository: str, number: int) -> PullRequestData: ...

    async def publish_report(self, pr: PullRequestData, report: ReviewReport) -> str:
        """Upsert a summary comment using task/head marker; return GitHub comment URL."""
        ...


class CodeAnalyzer(Protocol):
    def analyze(self, pr: PullRequestData) -> AnalysisResult: ...


class AIReviewer(Protocol):
    async def review(
        self, task_id: UUID, pr: PullRequestData, analysis: AnalysisResult
    ) -> ReviewReport: ...


class ReviewRepository(Protocol):
    async def create_if_absent(self, task: ReviewTask) -> tuple[ReviewTask, bool]:
        """Atomic unique key: (repository, pr_number, head_sha); return task, created."""
        ...

    async def get_task(self, task_id: UUID) -> ReviewTask | None: ...

    async def list_tasks(self, limit: int, offset: int) -> TaskList: ...

    async def save_task(self, task: ReviewTask) -> None: ...

    async def save_report(self, report: ReviewReport) -> None: ...

    async def get_report(self, task_id: UUID) -> ReviewReport | None: ...


class ReviewCoordinator(Protocol):
    async def submit(self, request: CreateReviewRequest) -> ReviewTask: ...

    async def run(self, task_id: UUID) -> None: ...
