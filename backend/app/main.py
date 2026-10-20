import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.routes.reviews import router
from app.core.config import Settings, configure_logging
from app.core.errors import ModuleError
from app.schemas.models import ErrorDetail, ErrorResponse, HealthResponse
from app.services.mock_store import MockReadStore

logger = logging.getLogger("coderisk")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        responses={code: {"model": ErrorResponse} for code in (404, 422, 500, 501, 503)},
    )
    application.state.settings = settings
    application.state.mock_store = MockReadStore()

    def error_response(request: Request, status: int, error: ErrorDetail) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        body = ErrorResponse(error=error, request_id=request_id)
        return JSONResponse(
            status_code=status,
            content=body.model_dump(mode="json"),
            headers={"X-Request-ID": request_id},
        )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        # Do not log URLs, query parameters, request bodies or exception text.
        logger.info(
            "request_id=%s method=%s status=%s",
            request.state.request_id,
            request.method,
            response.status_code,
        )
        return response

    @application.exception_handler(ModuleError)
    async def module_error(request: Request, exc: ModuleError):
        return error_response(
            request,
            exc.status_code,
            ErrorDetail(code=exc.code, message=exc.message, retryable=exc.retryable),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(
            request, 422, ErrorDetail(code="VALIDATION_ERROR", message="Invalid request")
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(
            request, exc.status_code, ErrorDetail(code="HTTP_ERROR", message="Request rejected")
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
        return error_response(
            request, 500, ErrorDetail(code="INTERNAL_ERROR", message="Internal server error")
        )

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(
            service=settings.app_name, mode="mock" if settings.mock_enabled else "unconfigured"
        )

    application.include_router(router)
    return application


app = create_app()
