import json
import re
from dataclasses import dataclass

from app.core.errors import ModuleError

REVIEW_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review", "edited"})
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class WebhookSnapshot:
    """保存 Webhook 声明的完整快照标识，供受理适配器核对。"""

    repository: str
    pr_number: int
    base_sha: str
    head_sha: str


def _invalid_payload() -> ModuleError:
    return ModuleError("INVALID_WEBHOOK_PAYLOAD", "Invalid webhook payload", status_code=422)


def _reject_constant(value: str) -> None:
    raise ValueError("JSON 不支持此数值常量")


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("JSON 对象包含重复字段")
        fields[key] = value
    return fields


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise _invalid_payload()
    return value


def parse_webhook_payload(body: bytes) -> dict:
    try:
        payload = json.loads(
            body.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_fields
        )
    except (ValueError, RecursionError):
        raise _invalid_payload() from None
    return _object(payload)


def should_review_event(event_type: str, payload: dict) -> bool:
    if event_type != "pull_request":
        return False
    action = payload.get("action")
    if not isinstance(action, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", action):
        raise _invalid_payload()
    if action not in REVIEW_ACTIONS:
        return False
    if action == "edited":
        changes = _object(payload.get("changes"))
        if "base" not in changes:
            return False
        previous_ref = _object(_object(changes["base"]).get("ref")).get("from")
        pr = _object(payload.get("pull_request"))
        current_ref = _object(pr.get("base")).get("ref")
        if not all(
            isinstance(ref, str) and 0 < len(ref) <= 1024 for ref in (previous_ref, current_ref)
        ):
            raise _invalid_payload()
        if previous_ref == current_ref:
            return False

    pr = _object(payload.get("pull_request"))
    state = pr.get("state")
    draft = pr.get("draft")
    if state not in ("open", "closed") or not isinstance(draft, bool):
        raise _invalid_payload()
    return state == "open" and not draft and pr.get("merged") is not True


def read_snapshot(payload: dict) -> WebhookSnapshot:
    repository = _object(payload.get("repository")).get("full_name")
    if (
        not isinstance(repository, str)
        or not REPOSITORY.fullmatch(repository)
        or any(part in (".", "..") for part in repository.split("/"))
    ):
        raise _invalid_payload()
    pr = _object(payload.get("pull_request"))
    number = payload.get("number")
    if type(number) is not int or not 0 < number <= 2**63 - 1:
        raise _invalid_payload()
    nested_number = pr.get("number", number)
    if type(nested_number) is not int or nested_number != number:
        raise _invalid_payload()
    base_sha = _object(pr.get("base")).get("sha")
    head_sha = _object(pr.get("head")).get("sha")
    if not all(isinstance(sha, str) and SHA.fullmatch(sha) for sha in (base_sha, head_sha)):
        raise _invalid_payload()
    return WebhookSnapshot(repository.lower(), number, base_sha, head_sha)
