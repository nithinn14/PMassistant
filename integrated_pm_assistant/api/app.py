"""
api/app.py
──────────
FastAPI application factory and configuration for PMassistant.

Constructs the FastAPI application instance, configures CORS middleware,
registers custom validation and HTTP exception handlers, and attaches all
modular route controllers (jobs, projects, files, meetings).
"""

import sys
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

# Ensure UTF-8 console output before job_runtime or routers are loaded
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from api.routes.files import router as files_router
from api.routes.jobs import router as jobs_router
from api.routes.meetings import router as meetings_router
from api.routes.projects import router as projects_router
from api.serialization import _safe_jsonify


async def validation_exception_handler(request, exc: RequestValidationError):
    """Custom handler preserving legacy API contract for validation errors."""
    first_err = exc.errors()[0] if exc.errors() else {}
    msg = first_err.get("msg", "Validation error")
    loc = " -> ".join(str(l) for l in first_err.get("loc", []))
    err_msg = f"{loc}: {msg}" if loc else msg
    return _safe_jsonify({"error": f"Invalid request: {err_msg}"}, status=400)


async def http_exception_handler(request, exc: HTTPException):
    """Custom handler preserving legacy API contract for HTTP exceptions."""
    return _safe_jsonify({"error": exc.detail}, status=exc.status_code)


def create_app() -> FastAPI:
    """Create, configure, and assemble the FastAPI application."""
    application = FastAPI(title="PM Assistant API")

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(HTTPException, http_exception_handler)

    # Register each modular router exactly once
    application.include_router(projects_router)
    application.include_router(files_router)
    application.include_router(jobs_router)
    application.include_router(meetings_router)

    return application


app = create_app()

__all__ = [
    "app",
    "create_app",
    "validation_exception_handler",
    "http_exception_handler",
]
