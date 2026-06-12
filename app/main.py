from collections.abc import Callable
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.models import ApiErrorResponse, ErrorDetail
from app.protected_routes import router as protected_router
from app.routes import router

logger = logging.getLogger(__name__)


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or getattr(
        request.state,
        "request_id",
        f"req_{uuid.uuid4().hex}",
    )
    payload = ApiErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=request_id,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def create_app(settings_factory: Callable[[], Settings] = get_settings) -> FastAPI:
    settings = settings_factory()
    app = FastAPI(
        title="StockBrief API",
        version=settings.service_version,
        openapi_url=f"{settings.api_base_path}/openapi.json",
        docs_url=f"{settings.api_base_path}/docs",
        redoc_url=f"{settings.api_base_path}/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-Id",
            "x-request-id",
        ],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Callable):
        request.state.request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        details = None
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", "INVALID_REQUEST"))
            message = str(exc.detail.get("message", "HTTP error occurred."))
            raw_details = exc.detail.get("details")
            details = raw_details if isinstance(raw_details, list | dict) else None
        else:
            code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST"
            message = str(exc.detail) if exc.detail else "HTTP error occurred."
        return _error_response(
            request=request,
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=400,
            code="INVALID_REQUEST",
            message="Request validation failed.",
            details=[
                {
                    "field": ".".join(str(part) for part in error["loc"]),
                    "reason": str(error["type"]),
                }
                for error in exc.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception("Unexpected server error occurred: %s", str(exc))
        return _error_response(
            request=request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="서버 내부 오류가 발생했습니다.",
            details=None,
        )

    app.include_router(router, prefix=settings.api_base_path)
    app.include_router(protected_router, prefix=settings.api_base_path)
    return app


app = create_app()
