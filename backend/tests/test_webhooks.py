import asyncio
import hashlib
import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from multiprocessing import get_context
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from anyio import sleep_forever, to_thread
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.routes.webhooks import MAX_WEBHOOK_BODY_BYTES, get_webhook_submitter
from app.core.config import Settings
from app.core.errors import ModuleError
from app.core.webhook_security import verify_signature
from app.main import create_app
from app.schemas.models import ReviewTask, TaskStatus
from app.services import webhook_deliveries
from app.services.webhook_events import WebhookSnapshot, parse_webhook_payload, should_review_event

SECRET = "local-test-secret"
URL = "/api/v1/webhooks/github"
BASE = "a" * 40
HEAD = "b" * 40


class FakeCoordinator:
    """用真实 SQLite 提交模拟持久化受理，并记录调用以供断言。"""

    def __init__(self, path):
        self.path = path
        self.calls = []

    async def submit(self, snapshot):
        self.calls.append(snapshot)
        return await to_thread.run_sync(self._save, snapshot)

    def _save(self, snapshot):
        key = (snapshot.repository, snapshot.pr_number, snapshot.base_sha, snapshot.head_sha)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    repository TEXT, pr_number INTEGER, base_sha TEXT, head_sha TEXT,
                    task_json TEXT, PRIMARY KEY (repository, pr_number, base_sha, head_sha)
                )
                """
            )
            task = ReviewTask(
                id=uuid4(),
                repository=snapshot.repository,
                pr_number=snapshot.pr_number,
                head_sha=snapshot.head_sha,
                status=TaskStatus.QUEUED,
                is_mock=True,
            )
            db.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (*key, task.model_dump_json()),
            )
            existing = db.execute(
                """
                SELECT task_json FROM tasks
                WHERE repository = ? AND pr_number = ? AND base_sha = ? AND head_sha = ?
                """,
                key,
            ).fetchone()
        return ReviewTask.model_validate_json(existing[0])

    def saved_tasks(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT task_json FROM tasks").fetchall()
        return [ReviewTask.model_validate_json(row[0]) for row in rows]


@pytest.fixture
def webhook(tmp_path):
    settings = Settings(
        _env_file=None,
        github_webhook_secret=SECRET,
        github_allowed_repositories={"Demo/Repo"},
        github_webhook_delivery_db=tmp_path / "deliveries.sqlite3",
    )
    fake = FakeCoordinator(tmp_path / "tasks.sqlite3")
    return settings, fake


def make_payload(action="opened", *, state="open", draft=False, base_sha=BASE):
    return {
        "action": action,
        "number": 12,
        "repository": {"full_name": "Demo/Repo"},
        "pull_request": {
            "number": 12,
            "state": state,
            "draft": draft,
            "base": {"sha": base_sha, "ref": "main"},
            "head": {"sha": HEAD, "ref": "topic"},
        },
    }


def signed_headers(body, *, event="pull_request", delivery="delivery-1"):
    return {
        "X-Hub-Signature-256": "sha256="
        + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
    }


def send(client, payload=None, *, event="pull_request", delivery="delivery-1"):
    body = json.dumps(make_payload() if payload is None else payload).encode()
    return client.post(
        URL, content=body, headers=signed_headers(body, event=event, delivery=delivery)
    )


def assert_error(response, status, code, *, retryable=False):
    assert response.status_code == status
    error = response.json()
    assert error["error"]["code"] == code
    assert error["error"]["retryable"] is retryable
    assert error["request_id"] == response.headers["x-request-id"]


def test_valid_signature():
    secret = b"It's a Secret to Everybody"
    body = b"Hello, World!"
    signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

    assert verify_signature(body, signature, secret) is True


def test_empty_secret():
    body = b"Hello, World!"
    signature = "sha256=" + "0" * 64

    assert verify_signature(body, signature, b"") is False


def test_invalid_signature_format():
    body = b"Hello, World!"
    secret = b"test-secret"

    assert verify_signature(body, None, secret) is False
    assert verify_signature(body, "sha1=abc", secret) is False
    assert verify_signature(body, "sha256=你好", secret) is False
    assert verify_signature(body, "sha256=abc", secret) is False


def test_tampered_body():
    secret = b"It's a Secret to Everybody"
    signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

    # 使用正确签名，但修改原始请求体
    tampered_body = b"Hello, World?"

    assert verify_signature(tampered_body, signature, secret) is False


def test_wrong_signature():
    secret = b"It's a Secret to Everybody"
    body = b"Hello, World!"

    # 签名格式合法，但摘要内容错误
    wrong_signature = "sha256=" + "0" * 64

    assert verify_signature(body, wrong_signature, secret) is False


def test_webhook_without_secret():
    # 创建没有配置 Webhook 密钥的应用
    settings = Settings(
        _env_file=None,
        github_webhook_secret=None,
    )
    app = create_app(settings)

    # 模拟客户端发送 Webhook 请求
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            content=b'{"action":"opened"}',
        )

    # 验证服务器拒绝处理未配置密钥的请求
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WEBHOOK_SECRET_NOT_CONFIGURED"

    # 验证错误响应符合 CodeRisk 的统一格式
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_webhook_with_valid_signature(webhook):
    settings, fake = webhook
    app = create_app(settings)
    app.dependency_overrides[get_webhook_submitter] = lambda: fake.submit
    with TestClient(app) as client:
        response = send(client)
    assert response.status_code == 202
    assert fake.calls == [WebhookSnapshot("demo/repo", 12, BASE, HEAD)]
    assert len(fake.saved_tasks()) == 1


def test_sync_app_initialization_defers_async_limiter(webhook, monkeypatch):
    settings, fake = webhook
    original = webhook_deliveries.CapacityLimiter

    def require_event_loop(tokens):
        asyncio.get_running_loop()
        return original(tokens)

    monkeypatch.setattr(webhook_deliveries, "CapacityLimiter", require_event_loop)
    app = create_app(settings, webhook_submitter=fake.submit)
    with TestClient(app) as client:
        assert send(client).status_code == 202


def test_webhook_with_wrong_signature():
    secret = "local-test-secret"
    body = b'{"action":"opened"}'

    # 构造格式正确、但内容错误的签名
    wrong_signature = "sha256=" + "0" * 64

    settings = Settings(
        _env_file=None,
        github_webhook_secret=secret,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": wrong_signature},
        )

    # 错误签名必须被拒绝
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SIGNATURE_VERIFY_FAILED"


def test_webhook_without_signature():
    secret = "local-test-secret"
    body = b'{"action":"opened"}'

    settings = Settings(
        _env_file=None,
        github_webhook_secret=secret,
    )

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            content=body,
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SIGNATURE_VERIFY_FAILED"


def test_webhook_with_tampered_body(webhook):
    settings, fake = webhook
    original_body = json.dumps(make_payload()).encode()
    tampered_body = original_body.replace(b'"opened"', b'"closed"')
    headers = signed_headers(original_body)
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        valid_response = client.post(URL, content=original_body, headers=headers)
        response = client.post(URL, content=tampered_body, headers=headers)
    assert valid_response.status_code == 202
    assert_error(response, 401, "SIGNATURE_VERIFY_FAILED")
    assert len(fake.calls) == 1


def test_webhook_with_empty_secret():
    body = b'{"action":"opened"}'
    settings = Settings(
        _env_file=None,
        github_webhook_secret="",
    )

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": ""},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WEBHOOK_SECRET_NOT_CONFIGURED"


def test_parse_valid_webhook_payload():
    body = b'{"action":"opened","number":12}'

    payload = parse_webhook_payload(body)

    assert payload["action"] == "opened"
    assert payload["number"] == 12
    assert isinstance(payload, dict)


def test_parse_invalid_json():
    body = b'{"action":'

    with pytest.raises(ModuleError):
        parse_webhook_payload(body)


def test_parse_illegal_utf8_bytes():
    body = b"\xff"
    with pytest.raises(ModuleError):
        parse_webhook_payload(body)


def test_parse_not_dict_json():
    body = b'["opened"]'
    with pytest.raises(ModuleError):
        parse_webhook_payload(body)


def test_whether_daft_trigger_review():
    event_type = "pull_request"
    payload = {
        "action": "opened",
        "pull_request": {
            "state": "open",
            "draft": False,
        },
    }
    assert should_review_event(event_type, payload)
    payload = {
        "action": "opened",
        "pull_request": {
            "state": "open",
            "draft": True,
        },
    }
    assert not should_review_event(event_type, payload)


def test_missing_pull_request():
    payload = {"action": "opened"}

    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_missing_draft():
    payload = {
        "action": "opened",
        "pull_request": {
            "state": "open",
        },
    }
    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_missing_action():
    payload = {
        "pull_request": {
            "state": "open",
            "draft": True,
        }
    }
    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_missing_state():
    payload = {
        "action": "opened",
        "pull_request": {
            "draft": True,
        },
    }
    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_closed_state():
    payload = {
        "action": "opened",
        "pull_request": {
            "state": "closed",
            "draft": True,
        },
    }
    assert not should_review_event("pull_request", payload)


def test_unknown_state():
    payload = {
        "action": "opened",
        "pull_request": {
            "state": "unknown",
            "draft": True,
        },
    }
    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_edit_base_ref():
    payload = {
        "action": "edited",
        "changes": {
            "base": {
                "ref": {
                    "from": "main",
                }
            }
        },
        "pull_request": {
            "state": "open",
            "draft": False,
            "base": {"ref": "release"},
        },
    }
    assert should_review_event("pull_request", payload)


def test_edit_title():
    payload = {
        "action": "edited",
        "changes": {
            "title": {
                "from": "旧标题",
            }
        },
        "pull_request": {
            "state": "open",
            "draft": False,
        },
    }
    assert not should_review_event("pull_request", payload)


def test_missing_from_ref():
    payload = {
        "action": "edited",
        "changes": {"base": {"ref": {}}},
        "pull_request": {
            "state": "open",
            "draft": False,
        },
    }
    with pytest.raises(ModuleError):
        should_review_event("pull_request", payload)


def test_ping_event_returns_204(webhook):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        response = send(client, {}, event="ping")
    assert response.status_code == 204
    assert response.content == b""
    assert fake.calls == []


@pytest.mark.parametrize(
    "action", ["opened", "reopened", "synchronize", "ready_for_review", "edited"]
)
def test_review_events(webhook, action):
    settings, fake = webhook
    payload = make_payload(action)
    if action == "edited":
        payload["changes"] = {"base": {"ref": {"from": "release"}}}
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client, payload).status_code == 202
    assert fake.calls == [WebhookSnapshot("demo/repo", 12, BASE, HEAD)]


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("pull_request", make_payload(draft=True)),
        ("pull_request", make_payload("ready_for_review", draft=True)),
        ("pull_request", make_payload("synchronize", state="closed")),
        ("pull_request", make_payload("closed", state="closed")),
        ("pull_request", {"action": "closed", "pull_request": {"state": "closed", "merged": True}}),
        (
            "pull_request",
            {
                **make_payload("synchronize"),
                "pull_request": {**make_payload()["pull_request"], "merged": True},
            },
        ),
        ("pull_request", {"action": "edited", "changes": {"title": {"from": "旧标题"}}}),
        ("pull_request", {"action": "edited", "changes": {"body": {"from": "旧正文"}}}),
        (
            "pull_request",
            {**make_payload("edited"), "changes": {"base": {"ref": {"from": "main"}}}},
        ),
        ("pull_request", {"action": "labeled"}),
        ("push", {}),
    ],
)
def test_ignored_events(webhook, event, payload):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        response = send(client, payload, event=event)
    assert response.status_code == 204
    assert response.content == b""
    assert fake.calls == []
    assert not settings.github_webhook_delivery_db.exists()


@pytest.mark.parametrize(
    "signature", [None, "sha256=" + "0" * 64, "sha1=123", "sha256=" + "A" * 64]
)
def test_invalid_signature_never_submits(webhook, signature):
    settings, fake = webhook
    body = json.dumps(make_payload()).encode()
    headers = signed_headers(body)
    if signature is None:
        headers.pop("X-Hub-Signature-256")
    else:
        headers["X-Hub-Signature-256"] = signature
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert_error(
            client.post(URL, content=body, headers=headers), 401, "SIGNATURE_VERIFY_FAILED"
        )
    assert fake.calls == []


@pytest.mark.parametrize(
    "body",
    [b"{", b'{"x":"\xff"}', b"[]", b'{"x": NaN}', b'{"x":1,"x":2}', b"[" * 2000 + b"]" * 2000],
)
def test_signed_invalid_json(webhook, body):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        response = client.post(URL, content=body, headers=signed_headers(body))
    assert_error(response, 422, "INVALID_WEBHOOK_PAYLOAD")
    assert fake.calls == []


@pytest.mark.parametrize(
    "patch",
    [
        {"number": True},
        {"number": "12"},
        {"number": 2**63},
        {"repository": {}},
        {"repository": {"full_name": "../repo"}},
        {"repository": {"full_name": "a" * 101 + "/repo"}},
        {"action": 1},
        {"action": "a" * 65},
        {"pull_request": {**make_payload()["pull_request"], "state": []}},
        {"pull_request": {**make_payload()["pull_request"], "draft": "false"}},
        {"pull_request": {**make_payload()["pull_request"], "number": 7}},
        {"pull_request": {**make_payload()["pull_request"], "base": {}}},
        {"pull_request": {**make_payload()["pull_request"], "head": {"sha": 123}}},
        {"action": "edited", "changes": {"base": None}},
    ],
)
def test_invalid_fields(webhook, patch):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        response = send(client, {**make_payload(), **patch})
    assert_error(response, 422, "INVALID_WEBHOOK_PAYLOAD")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("name", "value", "status", "code"),
    [
        ("X-GitHub-Event", None, 422, "EVENT_NOT_CONFIGURED"),
        ("X-GitHub-Event", "e" * 65, 422, "EVENT_NOT_CONFIGURED"),
        ("X-GitHub-Delivery", None, 422, "INVALID_WEBHOOK_DELIVERY"),
        ("X-GitHub-Delivery", "id with spaces", 422, "INVALID_WEBHOOK_DELIVERY"),
        ("X-GitHub-Delivery", "d" * 129, 422, "INVALID_WEBHOOK_DELIVERY"),
        ("X-GitHub-Delivery", "duplicate", 422, "INVALID_WEBHOOK_DELIVERY"),
        ("X-Hub-Signature-256", "duplicate", 401, "SIGNATURE_VERIFY_FAILED"),
    ],
)
def test_required_headers(webhook, name, value, status, code):
    settings, fake = webhook
    body = json.dumps(make_payload()).encode()
    headers = signed_headers(body)
    if value is None:
        headers.pop(name)
    elif value != "duplicate":
        headers[name] = value
    pairs = list(headers.items())
    if value == "duplicate":
        pairs.append((name, headers[name]))
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        response = client.post(URL, content=body, headers=pairs)
    assert_error(response, status, code)
    assert fake.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_body_size_limit_counts_streamed_bytes(webhook):
    settings, fake = webhook
    body = b"{}" + b" " * (MAX_WEBHOOK_BODY_BYTES - 2)

    async def chunks():
        yield body[:512]
        yield body[512:]
        yield b" "

    app = create_app(settings, webhook_submitter=fake.submit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        valid_headers = signed_headers(body, event="ping")
        valid = await client.post(URL, content=body, headers=valid_headers)
        assert valid.status_code == 204
        response = await client.post(
            URL,
            content=chunks(),
            headers=signed_headers(body + b" ", event="ping"),
        )
    assert_error(response, 413, "WEBHOOK_BODY_TOO_LARGE")
    assert fake.calls == []


def test_unallowed_repository(webhook):
    settings, fake = webhook
    settings.github_allowed_repositories = frozenset()
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert_error(send(client), 403, "REPOSITORY_NOT_ALLOWED")
    assert fake.calls == []


def test_default_app_fails_closed_without_coordinator(webhook):
    settings, fake = webhook
    with TestClient(create_app(settings)) as client:
        assert_error(send(client), 503, "WEBHOOK_COORDINATOR_NOT_CONFIGURED", retryable=True)
    assert fake.calls == []
    assert not settings.github_webhook_delivery_db.exists()


def test_delivery_dedup_after_app_restart(webhook):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
        assert send(client).status_code == 202
    assert len(fake.calls) == 1
    restarted = FakeCoordinator(fake.path)
    with TestClient(create_app(settings, webhook_submitter=restarted.submit)) as client:
        assert send(client).status_code == 202
        assert_error(send(client, make_payload("reopened")), 409, "WEBHOOK_DELIVERY_CONFLICT")
    assert restarted.calls == []
    assert restarted.saved_tasks() == fake.saved_tasks()


def test_snapshot_key_includes_base_sha(webhook):
    settings, fake = webhook
    changed = make_payload("edited", base_sha="c" * 40)
    changed["changes"] = {"base": {"ref": {"from": "main"}}}
    changed["pull_request"]["base"]["ref"] = "release"
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
        assert send(client, delivery="delivery-2").status_code == 202
        assert len(fake.saved_tasks()) == 1
        assert send(client, changed, delivery="delivery-3").status_code == 202
    assert [snapshot.base_sha for snapshot in fake.calls] == [BASE, BASE, "c" * 40]
    tasks = fake.saved_tasks()
    assert len({task.id for task in tasks}) == 2
    assert {task.head_sha for task in tasks} == {HEAD}


@pytest.mark.parametrize("second_delivery", ["delivery-1", "delivery-2"])
def test_concurrent_requests(webhook, monkeypatch, second_delivery):
    settings, fake = webhook
    entered = Event()
    second_entered = Event()
    release = Event()

    async def first_submit(snapshot):
        entered.set()
        assert await to_thread.run_sync(release.wait, 5)
        return await fake.submit(snapshot)

    first_app = create_app(settings, webhook_submitter=first_submit)
    second_app = create_app(settings, webhook_submitter=fake.submit)
    second_store = second_app.state.webhook_deliveries
    accept = second_store._accept

    def observed_accept(*args):
        second_entered.set()
        accept(*args)

    monkeypatch.setattr(second_store, "_accept", observed_accept)
    with (
        TestClient(first_app) as first,
        TestClient(second_app) as second,
        ThreadPoolExecutor(2) as pool,
    ):
        first_response = pool.submit(send, first)
        try:
            assert entered.wait(5)
            second_response = pool.submit(send, second, delivery=second_delivery)
            assert second_entered.wait(5)
            assert fake.calls == []
        finally:
            release.set()
        assert first_response.result(5).status_code == 202
        assert second_response.result(5).status_code == 202
    assert len(fake.calls) == (1 if second_delivery == "delivery-1" else 2)
    assert len(fake.saved_tasks()) == 1


def test_submit_failure_can_retry(webhook):
    settings, fake = webhook
    attempts = []

    async def submit(snapshot):
        attempts.append(snapshot)
        if len(attempts) == 1:
            raise RuntimeError("模拟受理故障")
        return await fake.submit(snapshot)

    with TestClient(create_app(settings, webhook_submitter=submit)) as client:
        assert_error(send(client), 503, "WEBHOOK_SUBMIT_FAILED", retryable=True)
        assert not fake.path.exists()
        assert send(client).status_code == 202
    assert len(attempts) == 2
    assert len(fake.calls) == len(fake.saved_tasks()) == 1


def test_submit_timeout_releases_delivery_transaction(webhook, monkeypatch):
    settings, fake = webhook
    attempts = []

    async def submit(snapshot):
        attempts.append(snapshot)
        await sleep_forever()

    with monkeypatch.context() as patch:
        patch.setattr(webhook_deliveries, "SUBMIT_TIMEOUT_SECONDS", 0.05)
        with TestClient(create_app(settings, webhook_submitter=submit)) as client:
            assert_error(send(client), 503, "WEBHOOK_SUBMIT_FAILED", retryable=True)
    assert len(attempts) == 1
    assert not fake.path.exists()
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
    assert len(fake.calls) == len(fake.saved_tasks()) == 1


def test_coordinator_module_error_is_preserved(webhook):
    settings, fake = webhook
    attempts = []

    async def submit(snapshot):
        attempts.append(snapshot)
        raise ModuleError("SNAPSHOT_CHANGED", "PR snapshot has changed", status_code=409)

    with TestClient(create_app(settings, webhook_submitter=submit)) as client:
        assert_error(send(client), 409, "SNAPSHOT_CHANGED")
    assert len(attempts) == 1
    assert not fake.path.exists()


def test_wrong_task_result_does_not_mark_delivery(webhook):
    settings, fake = webhook

    async def submit(snapshot):
        task = await fake.submit(snapshot)
        return task.model_copy(update={"head_sha": "d" * 40})

    with TestClient(create_app(settings, webhook_submitter=submit)) as client:
        assert_error(send(client), 503, "WEBHOOK_SUBMIT_MISMATCH", retryable=True)
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
    assert len(fake.calls) == 2
    assert len(fake.saved_tasks()) == 1


def test_database_open_failure_does_not_submit(webhook):
    settings, fake = webhook
    settings.github_webhook_delivery_db = settings.github_webhook_delivery_db.parent
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert_error(send(client), 503, "WEBHOOK_DELIVERY_STORE_UNAVAILABLE", retryable=True)
    assert fake.calls == []


@pytest.mark.parametrize(
    "db_name", [":memory:", "file::memory:?cache=shared", "file:delivery?mode=memory"]
)
def test_volatile_delivery_database_does_not_submit(webhook, db_name, tmp_path, monkeypatch):
    settings, fake = webhook
    monkeypatch.chdir(tmp_path)
    settings.github_webhook_delivery_db = Path(db_name)
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert_error(send(client), 503, "WEBHOOK_DELIVERY_DB_NOT_PERSISTENT")
    assert fake.calls == []
    assert not fake.path.exists()


def test_database_lock_timeout_can_retry(webhook):
    settings, fake = webhook
    with (
        closing(sqlite3.connect(settings.github_webhook_delivery_db)) as db,
        TestClient(create_app(settings, webhook_submitter=fake.submit)) as client,
    ):
        db.execute("BEGIN IMMEDIATE")
        assert_error(send(client), 503, "WEBHOOK_DELIVERY_STORE_UNAVAILABLE", retryable=True)
        assert fake.calls == []
        db.rollback()
        assert send(client).status_code == 202
    assert len(fake.calls) == 1


def test_saved_task_is_reused_after_delivery_mark_fails(webhook):
    settings, fake = webhook
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client, delivery="initial").status_code == 202
        with closing(sqlite3.connect(settings.github_webhook_delivery_db)) as db:
            db.execute(
                """
                CREATE TRIGGER fail_delivery BEFORE INSERT ON webhook_deliveries
                BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END
                """
            )
        payload = make_payload(base_sha="c" * 40)
        assert_error(
            send(client, payload), 503, "WEBHOOK_DELIVERY_STORE_UNAVAILABLE", retryable=True
        )
        tasks = fake.saved_tasks()
        assert len(tasks) == 2
        with closing(sqlite3.connect(settings.github_webhook_delivery_db)) as db:
            db.execute("DROP TRIGGER fail_delivery")
        assert send(client, payload).status_code == 202
        assert send(client, payload).status_code == 202
    assert len(fake.calls) == 3
    assert fake.saved_tasks() == tasks


def test_saved_task_is_reused_after_response_send_fails(webhook):
    settings, fake = webhook
    app = create_app(settings, webhook_submitter=fake.submit)

    async def broken_connection(scope, receive, send):
        async def drop_response(message):
            assert message["type"] == "http.response.start"
            assert message["status"] == 202
            raise OSError("模拟 HTTP 响应发送失败")

        await app(scope, receive, drop_response)

    with pytest.raises(OSError, match="响应发送失败"):
        send(TestClient(broken_connection))
    tasks = fake.saved_tasks()
    assert len(tasks) == len(fake.calls) == 1
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
    assert len(fake.calls) == 1
    assert fake.saved_tasks() == tasks


def _receive_then_pause(delivery_db, task_db, entered, save_before_pause):
    settings = Settings(
        _env_file=None,
        github_webhook_secret=SECRET,
        github_allowed_repositories={"demo/repo"},
        github_webhook_delivery_db=delivery_db,
    )
    fake = FakeCoordinator(task_db)

    async def submit(snapshot):
        if save_before_pause:
            await fake.submit(snapshot)
        entered.set()
        await sleep_forever()

    with TestClient(create_app(settings, webhook_submitter=submit)) as client:
        send(client)


@pytest.mark.parametrize("save_before_pause", [False, True])
def test_recover_delivery_after_process_termination(webhook, save_before_pause):
    settings, fake = webhook
    context = get_context("spawn")
    entered = context.Event()
    process = context.Process(
        target=_receive_then_pause,
        args=(settings.github_webhook_delivery_db, fake.path, entered, save_before_pause),
    )
    process.start()
    try:
        assert entered.wait(10)
        assert fake.path.exists() is save_before_pause
        original = fake.saved_tasks() if save_before_pause else []
    finally:
        process.terminate()
        process.join(5)
    assert not process.is_alive()
    assert process.exitcode != 0
    with TestClient(create_app(settings, webhook_submitter=fake.submit)) as client:
        assert send(client).status_code == 202
        assert send(client).status_code == 202
    assert len(fake.calls) == len(fake.saved_tasks()) == 1
    if original:
        assert fake.saved_tasks() == original
