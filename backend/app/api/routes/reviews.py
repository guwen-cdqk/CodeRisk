from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.errors import ModuleError
from app.schemas.models import CreateReviewRequest, ReviewReport, ReviewTask, TaskList

router = APIRouter(prefix="/api/v1", tags=["reviews"])


def store_for(request: Request):
    if not request.app.state.settings.mock_enabled:
        raise ModuleError("NOT_CONFIGURED", "Real services are not implemented", status_code=503)
    return request.app.state.mock_store


@router.get("/tasks", response_model=TaskList)
def list_tasks(
    request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
) -> TaskList:
    return store_for(request).list_tasks(limit, offset)


@router.get("/tasks/{task_id}", response_model=ReviewTask)
def get_task(task_id: UUID, request: Request) -> ReviewTask:
    store = store_for(request)
    if task_id != store.task.id:
        raise ModuleError("TASK_NOT_FOUND", "Task not found", status_code=404)
    return store.task


@router.get("/tasks/{task_id}/report", response_model=ReviewReport)
def get_report(task_id: UUID, request: Request) -> ReviewReport:
    store = store_for(request)
    if task_id != store.task.id:
        raise ModuleError("TASK_NOT_FOUND", "Task not found", status_code=404)
    return store.report


@router.post("/reviews", response_model=ReviewTask, status_code=202)
def create_review(body: CreateReviewRequest) -> ReviewTask:
    raise ModuleError("NOT_IMPLEMENTED", "Review submission is not implemented", status_code=501)
