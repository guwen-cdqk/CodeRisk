import hashlib
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.requests import ClientDisconnect

from app.core.errors import ModuleError
from app.core.webhook_security import verify_signature
from app.schemas.models import ErrorResponse
from app.services.webhook_deliveries import WebhookSubmitter
from app.services.webhook_events import parse_webhook_payload, read_snapshot, should_review_event

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024
EVENT_HEADER = re.compile(r"[a-z0-9_]{1,64}")
DELIVERY_HEADER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def get_webhook_submitter(request: Request) -> WebhookSubmitter | None:
    return request.app.state.webhook_submitter


def _single_header(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else ""


async def _bounded_body(request: Request) -> bytes:
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
                raise ModuleError(
                    "WEBHOOK_BODY_TOO_LARGE", "Webhook body is too large", status_code=413
                )
            body.extend(chunk)
    except ClientDisconnect:
        raise ModuleError(
            "WEBHOOK_BODY_INCOMPLETE", "Webhook body is incomplete", status_code=400
        ) from None
    return bytes(body)


@router.post(
    "/github",
    status_code=202,
    response_class=Response,
    responses={
        204: {"description": "Event ignored"},
        **{code: {"model": ErrorResponse} for code in (400, 401, 403, 409, 413)},
    },
)
async def receive_webhook(
    request: Request, submit: Annotated[WebhookSubmitter | None, Depends(get_webhook_submitter)]
) -> Response:
    settings = request.app.state.settings
    secret = settings.github_webhook_secret
    if not secret or not secret.get_secret_value():
        raise ModuleError(
            "WEBHOOK_SECRET_NOT_CONFIGURED", "Webhook secret is not configured", status_code=503
        )

    body = await _bounded_body(request)
    signature = _single_header(request, "X-Hub-Signature-256")
    if not verify_signature(body, signature, secret.get_secret_value().encode("utf-8")):
        raise ModuleError(
            "SIGNATURE_VERIFY_FAILED", "Webhook signature verification failed", status_code=401
        )

    event = _single_header(request, "X-GitHub-Event")
    if not EVENT_HEADER.fullmatch(event):
        raise ModuleError("EVENT_NOT_CONFIGURED", "Invalid webhook event header", status_code=422)
    delivery_id = _single_header(request, "X-GitHub-Delivery")
    if not DELIVERY_HEADER.fullmatch(delivery_id):
        raise ModuleError(
            "INVALID_WEBHOOK_DELIVERY", "Invalid webhook delivery ID", status_code=422
        )

    payload = parse_webhook_payload(body)
    if not should_review_event(event, payload):
        return Response(status_code=204)

    snapshot = read_snapshot(payload)
    if snapshot.repository not in {repo.lower() for repo in settings.github_allowed_repositories}:
        raise ModuleError("REPOSITORY_NOT_ALLOWED", "Repository is not allowed", status_code=403)
    if submit is None:
        raise ModuleError(
            "WEBHOOK_COORDINATOR_NOT_CONFIGURED",
            "Durable webhook review submission is not configured",
            status_code=503,
            retryable=True,
        )

    await request.app.state.webhook_deliveries.accept(
        delivery_id, hashlib.sha256(body).hexdigest(), snapshot, submit
    )
    return Response(status_code=202)
