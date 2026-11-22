import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from uuid import UUID

from anyio import CapacityLimiter, fail_after, from_thread, to_thread

from app.core.errors import ModuleError
from app.schemas.models import ReviewTask
from app.services.webhook_events import WebhookSnapshot

# 仅用于 Webhook 装配，待 A-03 接入能够核对完整快照的受理适配器。
WebhookSubmitter = Callable[[WebhookSnapshot], Awaitable[ReviewTask]]
SUBMIT_TIMEOUT_SECONDS = 70


class WebhookDeliveryStore:
    """在独立 SQLite 文件中记录已受理的投递。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._workers: CapacityLimiter | None = None

    async def accept(
        self,
        delivery_id: str,
        body_sha256: str,
        snapshot: WebhookSnapshot,
        submit: WebhookSubmitter,
    ) -> None:
        filename = str(self.path)
        if filename == ":memory:" or filename.startswith("file:"):
            raise ModuleError(
                "WEBHOOK_DELIVERY_DB_NOT_PERSISTENT",
                "Webhook delivery store requires a persistent file path",
                status_code=503,
            )
        if self._workers is None:
            # 在异步上下文创建独立限流器，协调器仍可使用默认线程池。
            self._workers = CapacityLimiter(4)
        try:
            await to_thread.run_sync(
                self._accept, delivery_id, body_sha256, snapshot, submit, limiter=self._workers
            )
        except sqlite3.Error:
            raise ModuleError(
                "WEBHOOK_DELIVERY_STORE_UNAVAILABLE",
                "Webhook delivery store is unavailable",
                status_code=503,
                retryable=True,
            ) from None

    def _accept(
        self,
        delivery_id: str,
        body_sha256: str,
        snapshot: WebhookSnapshot,
        submit: WebhookSubmitter,
    ) -> None:
        with closing(sqlite3.connect(self.path, timeout=1, isolation_level=None)) as db:
            db.execute("PRAGMA synchronous = FULL")
            # 数据库写锁覆盖查询、协调器受理和提交，连接关闭时自动回滚未提交事务。
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    delivery_id TEXT NOT NULL PRIMARY KEY,
                    body_sha256 TEXT NOT NULL,
                    task_id TEXT NOT NULL
                )
                """
            )
            existing = db.execute(
                "SELECT body_sha256 FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != body_sha256:
                    raise ModuleError(
                        "WEBHOOK_DELIVERY_CONFLICT",
                        "Webhook delivery ID was used for another body",
                        status_code=409,
                    )
                return

            task_id = from_thread.run(self._submit, snapshot, submit)
            db.execute(
                """
                INSERT INTO webhook_deliveries (delivery_id, body_sha256, task_id)
                VALUES (?, ?, ?)
                """,
                (delivery_id, body_sha256, str(task_id)),
            )
            db.commit()

    @staticmethod
    async def _submit(snapshot: WebhookSnapshot, submit: WebhookSubmitter) -> UUID:
        try:
            with fail_after(SUBMIT_TIMEOUT_SECONDS):
                task = await submit(snapshot)
        except ModuleError:
            raise
        except Exception:
            raise ModuleError(
                "WEBHOOK_SUBMIT_FAILED",
                "Review submission failed",
                status_code=503,
                retryable=True,
            ) from None
        if (
            not isinstance(task, ReviewTask)
            or task.repository.lower() != snapshot.repository
            or task.pr_number != snapshot.pr_number
            or task.head_sha != snapshot.head_sha
        ):
            raise ModuleError(
                "WEBHOOK_SUBMIT_MISMATCH",
                "Review submission did not confirm the expected snapshot",
                status_code=503,
                retryable=True,
            )
        return task.id
