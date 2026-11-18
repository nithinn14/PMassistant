
"""
api_server.py
─────────────
FastAPI REST API that wraps the existing ADK orchestrator.

Endpoints:
    POST /api/start-project          → launch pipeline in background thread
    GET  /api/job-status/{id}        → poll progress / completion
    GET  /api/project-results/{name} → structured results JSON
    GET  /api/download/{filename}    → serve output files
    POST /api/upload-file            → upload data files / smart merge
    GET  /api/projects               → list available projects
    POST /api/sync-meetings          → sync RSVPs & trigger reminders
    GET  /api/ping                   → health check
"""

import json, os, sys, re, io, uuid, threading, time, math, asyncio, yaml, contextvars, shutil
from typing import Optional, Dict, Any, List, Union
from datetime import datetime

# Fix Windows encoding
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response, FileResponse
from pydantic import BaseModel
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Import meeting tools for sync endpoint
from meetings.tools.meeting_tools import check_rsvp, send_reminder, reschedule_meeting

load_dotenv(override=True)

from paths import APP_ROOT, DEFAULT_OUTPUT_DIR

BASE_DIR = APP_ROOT
OUTPUT_DIR = DEFAULT_OUTPUT_DIR

# ── FastAPI Application & Handlers ──────────────────────────────────────────
from api.app import (
    app,
    create_app,
    validation_exception_handler,
    http_exception_handler,
)

from api.schemas import StartProjectRequest, SyncMeetingsRequest
from api.serialization import _sanitize, _safe_jsonify

# ── Job runtime ─────────────────────────────────────────────────────────────
# Importing api.job_runtime installs the process-wide JobOutputRouter on
# sys.stdout exactly once (at module load).  All runtime objects are shared
# by reference — there is only one jobs dict, one ContextVar, and one router.
from api.job_runtime import (
    jobs,
    STEP_KEYWORDS,
    OutputCapture,
    _current_job_id_var,
    _router_lock,
    JobOutputRouter,
    _stdout_router,
)


# ── Pipeline service ─────────────────────────────────────────────────────────
# Pipeline execution logic lives in api/services/pipeline.py.
# Re-exported here so existing callers (tests, scripts) can still reach them
# as api_server.<name> without change.
from api.services.pipeline import (
    MAX_RETRIES,
    _coerce_project_name_to_str,
    find_actual_project_name,
    verify_pipeline_output,
    _run_pipeline,
)

# ── Projects routes and services ─────────────────────────────────────────────
from api.routes.projects import (
    router as projects_router,
    project_results,
    list_projects,
)
from api.services.projects import (
    get_available_projects,
    resolve_project_name,
    load_project_results,
    get_projects_overview,
)

# ── Files routes and services ───────────────────────────────────────────────
from api.routes.files import (
    router as files_router,
    upload_file,
    download_file,
)
from api.services.files import (
    handle_upload_file,
    resolve_download_path,
)

# ── Jobs routes ─────────────────────────────────────────────────────────────
from api.routes.jobs import (
    router as jobs_router,
    ping,
    start_project,
    job_status,
)

# ── Meetings routes ─────────────────────────────────────────────────────────
from api.routes.meetings import (
    router as meetings_router,
    sync_meetings,
)


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "=" * 60)
    print("🚀 PM Assistant is ready!")
    print("👉 Open the Web UI here: http://localhost:5173")
    print("=" * 60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info", workers=1)
