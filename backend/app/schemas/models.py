from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RepositoryName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
CommitSHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SourceLocation(Schema):
    path: str = Field(min_length=1, max_length=1024)
    side: Literal["old", "new"] = "new"
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def relative_posix_path(cls, value: str) -> str:
        if (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
        ):
            raise ValueError("path must be repository-relative POSIX path")
        return value

    @model_validator(mode="after")
    def ordered_lines(self) -> "SourceLocation":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class ChangedFile(Schema):
    path: str
    previous_path: str | None = None
    status: Literal["added", "modified", "removed", "renamed"]
    patch: str | None = None
    patch_truncated: bool = False
    # The head/base file text is data for AST parsing, never executable input.
    old_content: str | None = None
    new_content: str | None = None

    @field_validator("path", "previous_path")
    @classmethod
    def valid_path(cls, value: str | None) -> str | None:
        return SourceLocation.relative_posix_path(value) if value is not None else None


class PullRequestData(Schema):
    repository: RepositoryName
    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1024)
    base_ref: str = Field(min_length=1)
    head_ref: str = Field(min_length=1)
    base_sha: CommitSHA
    head_sha: CommitSHA
    files: list[ChangedFile] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Finding(Schema):
    category: str = Field(min_length=1)
    severity: Severity
    location: SourceLocation
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    source: Literal["static", "ai"]
    rule_id: str | None = None


class ChangedFunction(Schema):
    qualified_name: str = Field(min_length=1)
    location: SourceLocation
    context: str


class Diagnostic(Schema):
    code: str
    message: str
    path: str | None = None


class AnalysisResult(Schema):
    head_sha: CommitSHA
    status: Literal["complete", "partial", "failed"]
    functions: list[ChangedFunction] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


class ErrorDetail(Schema):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(Schema):
    error: ErrorDetail
    request_id: str


class ReviewReport(Schema):
    task_id: UUID
    head_sha: CommitSHA
    status: Literal["completed", "failed"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    error: ErrorDetail | None = None
    is_mock: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def consistent_error(self) -> "ReviewReport":
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("failed report requires error; completed report forbids error")
        return self


class ReviewTask(Schema):
    id: UUID
    repository: RepositoryName
    pr_number: int = Field(gt=0)
    head_sha: CommitSHA
    status: TaskStatus
    is_mock: bool = False
    publication_status: Literal["not_requested", "pending", "published", "failed"] = "not_requested"
    error: ErrorDetail | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskList(Schema):
    items: list[ReviewTask]
    total: int = Field(ge=0)
    limit: int
    offset: int


class CreateReviewRequest(Schema):
    repository: RepositoryName
    pr_number: int = Field(gt=0)


class HealthResponse(Schema):
    status: Literal["ok"] = "ok"
    service: str
    mode: Literal["mock", "unconfigured"]
