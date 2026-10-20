from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.schemas.models import PullRequestData, ReviewReport, SourceLocation, TaskList


@pytest.fixture
def client():
    with TestClient(create_app(Settings(_env_file=None))) as test_client:
        yield test_client


def test_health_and_demo_contract(client):
    response = client.get("/health")
    assert response.json() == {"status": "ok", "service": "CodeRisk", "mode": "mock"}
    assert response.headers["x-request-id"]
    page = TaskList.model_validate(client.get("/api/v1/tasks").json())
    task = page.items[0]
    assert task.is_mock
    assert client.get(f"/api/v1/tasks/{task.id}").json()["id"] == str(task.id)
    report = ReviewReport.model_validate(client.get(f"/api/v1/tasks/{task.id}/report").json())
    assert report.task_id == task.id and report.is_mock
    assert report.head_sha == task.head_sha
    assert client.get("/api/v1/tasks?offset=1").json()["items"] == []
    spec = client.get("/openapi.json").json()
    error_schema = spec["paths"]["/api/v1/tasks"]["get"]["responses"]["422"]
    assert error_schema["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        ("/api/v1/tasks?limit=0", 422, "VALIDATION_ERROR"),
        ("/api/v1/tasks/not-a-uuid", 422, "VALIDATION_ERROR"),
        (f"/api/v1/tasks/{uuid4()}", 404, "TASK_NOT_FOUND"),
        ("/missing", 404, "HTTP_ERROR"),
    ],
)
def test_error_envelope(client, url, status, code):
    response = client.get(url)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_submission_is_explicitly_unimplemented(client):
    response = client.post("/api/v1/reviews", json={"repository": "a/b", "pr_number": 1})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
    assert (
        client.post("/api/v1/reviews", json={"repository": "bad", "pr_number": 0}).status_code
        == 422
    )


def test_mock_disabled_is_not_silent_success():
    with TestClient(create_app(Settings(_env_file=None, mock_enabled=False))) as client:
        assert client.get("/health").json()["mode"] == "unconfigured"
        assert client.get("/api/v1/tasks").status_code == 503


@pytest.mark.parametrize("path", ["../x.py", "/x.py", "C:/x.py", "a\\b.py", "a//b.py"])
def test_reject_unsafe_locations(path):
    with pytest.raises(ValidationError):
        SourceLocation(path=path, start_line=1, end_line=2)


def test_line_range_and_report_state():
    with pytest.raises(ValidationError):
        SourceLocation(path="a.py", start_line=3, end_line=2)
    with pytest.raises(ValidationError):
        ReviewReport(task_id=uuid4(), head_sha="a" * 40, status="failed", summary="failure")


def test_pr_roundtrip_contract():
    pr = PullRequestData(
        repository="org/repo",
        number=1,
        title="demo",
        base_ref="main",
        head_ref="feature/a",
        base_sha="b" * 40,
        head_sha="a" * 40,
        files=[{"path": "a.py", "status": "modified", "patch": None}],
    )
    assert PullRequestData.model_validate_json(pr.model_dump_json()) == pr
    with pytest.raises(ValidationError):
        PullRequestData.model_validate({**pr.model_dump(), "head_sha": "main"})


def test_unexpected_error_does_not_leak_secrets():
    application = create_app(Settings(_env_file=None))

    @application.get("/test-error")
    def explode():
        raise RuntimeError("secret-token")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/test-error")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret-token" not in response.text
