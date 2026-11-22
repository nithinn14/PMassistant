"""
api/routes/jobs.py
──────────────────
FastAPI route definitions for project pipeline jobs and health check.
"""

from datetime import datetime
import threading
from typing import Optional
import uuid

from fastapi import APIRouter

from api.job_runtime import jobs
from api.schemas import StartProjectRequest
from api.serialization import _safe_jsonify
from api.services.pipeline import _run_pipeline

router = APIRouter()


@router.get("/api/ping")
def ping():
    """Health check endpoint returning system status and current timestamp."""
    return _safe_jsonify({"status": "ok", "time": datetime.now().isoformat()})


@router.post("/api/start-project")
def start_project(req: Optional[StartProjectRequest] = None):
    """Launch the ADK project management pipeline in a background thread."""
    req_dict = (req or StartProjectRequest()).model_dump()
    project_name = (req_dict.get("project_name") or "").strip()
    project_description = (req_dict.get("project_description") or "").strip()
    backend_choice = (req_dict.get("data_backend") or "excel").strip().lower()
    integrations = req_dict["integrations"]

    if not project_name or not project_description:
        return _safe_jsonify({"error": "project_name and project_description are required"}, status=400)
    if backend_choice not in ("excel", "csv", "sql", "odoo"):
        return _safe_jsonify({"error": f"data_backend must be one of: excel, csv, sql, odoo (got '{backend_choice}')"}, status=400)

    # Start background job
    job_id = str(uuid.uuid4())
    print(f"DEBUG: Starting project '{project_name}' with job_id {job_id}")

    jobs[job_id] = {
        "id": job_id,
        "project_name": project_name,
        "original_project_name": project_name,
        "actual_project_name": project_name,
        "status": "running",
        "current_step": "Initializing...",
        "completed_steps": [],
        "logs": [],
        "error": None,
        "started_at": datetime.now().isoformat(),
    }

    thread = threading.Thread(
        target=_run_pipeline, args=(job_id, project_name, project_description, backend_choice, integrations)
    )
    thread.daemon = True
    thread.start()

    return _safe_jsonify({"job_id": job_id, "status": "running"})


@router.get("/api/job-status/{job_id}")
def job_status(job_id: str):
    """Query execution status, progress steps, and logs for a background job."""
    job = jobs.get(job_id)
    if not job:
        return _safe_jsonify({"error": "Job not found"}, status=404)
    return _safe_jsonify(
        {
            "status": job["status"],
            "current_step": job["current_step"],
            "completed_steps": job["completed_steps"],
            "error": job["error"],
            "project_name": job.get("actual_project_name") or job["project_name"],
            "original_project_name": job.get("original_project_name", job["project_name"]),
            "actual_project_name": job.get("actual_project_name") or job["project_name"],
        }
    )


__all__ = [
    "router",
    "ping",
    "start_project",
    "job_status",
]
