"""
api/routes/projects.py
──────────────────────
FastAPI route handlers for project results and project listing.
"""

from fastapi import APIRouter
from api.serialization import _safe_jsonify
from api.services.projects import (
    load_project_results,
    get_projects_overview,
)

router = APIRouter()


@router.get("/api/project-results/{project_name:path}")
def project_results(project_name: str):
    """Fetch structured results and artifacts for a project."""
    data, status_code = load_project_results(project_name)
    return _safe_jsonify(data, status=status_code)


@router.get("/api/projects")
def list_projects():
    """List all generated projects by scanning *_PRD.json in the output dir."""
    projects = get_projects_overview()
    return _safe_jsonify({"projects": projects})


__all__ = [
    "router",
    "project_results",
    "list_projects",
]
