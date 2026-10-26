"""Core API application factory."""
from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .core import ApiError, problem_body
from .db import engine
from .deps import route_permission

log = logging.getLogger("shikshasetu")

_NUMBER_RUN = re.compile(r"\b\d{10,12}\b")


class RedactFilter(logging.Filter):
    """REQ-OPS-08: scrub 10-12 digit runs (phone / APAAR-like / Aadhaar-like) from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _NUMBER_RUN.sub("[redacted]", str(record.msg))
        return True


def configure_logging() -> None:
    logging.basicConfig(level=get_settings().log_level, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
    for h in logging.getLogger().handlers:
        h.addFilter(RedactFilter())


def api_routes(app: FastAPI) -> list:
    """Every APIRoute of every included router (newer FastAPI hides included routes behind lazy wrappers)."""
    return [r for router in app.state.routers for r in router.routes if getattr(r, "path", "").startswith("/api/")]


def check_startup(app: FastAPI) -> None:
    s = get_settings()
    if s.app_env == "production":
        problems = s.production_problems()
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    missing = [f"{sorted(r.methods)} {r.path}" for r in api_routes(app) if route_permission(r) is None]
    if missing:
        raise RuntimeError("Routes without a declared permission (REQ-ACL-01): " + ", ".join(missing))


def create_app() -> FastAPI:
    from . import routes_auth, routes_claims, routes_ops, routes_reference, routes_student, routes_dashboards, routes_submissions, routes_uploads, routes_matching, routes_cases, routes_reports, routes_final

    configure_logging()
    s = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        check_startup(app)
        try:
            from .ops import sync_clock
            sync_clock()
        except Exception:  # noqa: BLE001  (database may not be migrated yet; /readyz reports that)
            pass
        yield

    app = FastAPI(title="ShikshaSetu core API", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs" if s.demo_mode else None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json" if s.demo_mode else None)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",")], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-Id", "Idempotent-Replay", "Retry-After"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        routes_ops.record_latency(request.url.path, (time.perf_counter() - t0) * 1000)
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(problem_body(exc, request.state.request_id), status_code=exc.status,
                            media_type="application/problem+json", headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(x) for x in e["loc"][1:]) or str(e["loc"][0]), "code": e["type"], "message": e["msg"],
                   "fix_suggestion": "Correct this field and resubmit."} for e in exc.errors()]
        err = ApiError(422, "VALIDATION_ERROR", "The request has invalid fields", "Fix the listed fields.", errors=errors)
        return JSONResponse(problem_body(err, request.state.request_id), status_code=422, media_type="application/problem+json")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", "?"))
        err = ApiError(500, "INTERNAL_ERROR", "Something went wrong", "The request failed. Quote the request id to support.",
                       retryable=True)
        return JSONResponse(problem_body(err, getattr(request.state, "request_id", "")), status_code=500,
                            media_type="application/problem+json")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        from .rlscheck import check as rls_check
        problems = rls_check()
        if problems:
            return JSONResponse({"status": "not_ready", "problems": problems}, status_code=503)
        return {"status": "ready", "migration": head}

    app.state.routers = [routes_auth.router, routes_reference.router, routes_claims.router, routes_student.router,
                         routes_dashboards.router, routes_ops.router, routes_submissions.router, routes_uploads.router, routes_matching.router, routes_cases.router, routes_reports.router, routes_final.router]
    for r in app.state.routers:
        app.include_router(r)
    return app


app = create_app() if __name__ != "__main__" else None
