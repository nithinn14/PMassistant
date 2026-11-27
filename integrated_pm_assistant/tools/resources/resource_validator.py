"""
Resource Validator Tool
-----------------------
Validates resource assignments, generates diagnostic reports,
manages workflow state for shortage detection and resolution.
"""

import json
import os
import tempfile
import threading
import yaml
from pathlib import Path
from paths import DEFAULT_OUTPUT_DIR
from datetime import datetime
from typing import List, Dict, Any, Optional

_WORKFLOW_STATE_LOCK = threading.Lock()


class PMResponseTimeoutError(Exception):
    """Raised when PM response wait expires after reminder and second wait."""

    def __init__(self, project_name: str, run_id: Optional[str] = None, message: str = ""):
        self.project_name = project_name
        self.run_id = run_id
        super().__init__(
            message or f"PM response timed out for project '{project_name}' (run_id={run_id})"
        )


def _atomic_write_yaml(file_path: Path, data: Dict[str, Any]) -> None:
    """Write YAML data atomically via a temporary file in the same directory."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, file_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def validate_resources(project_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scans assigned tasks for shortages ("No Resource Available").
    Returns structured shortage report.
    """
    shortages = []

    for task in tasks:
        emp = str(task.get("Assigned_Employee", "")).strip().lower()
        status = str(task.get("task_status", "")).strip().lower()

        # Approved external-hire tasks are no longer unresolved shortages.
        if status == "needs_external_hire" or emp == "needs_external_hire":
            continue

        if emp == "no resource available" or status == "waiting_resource":
            shortages.append({
                "task": task.get("task_name", "Unknown Task"),
                "task_id": task.get("task_id", "N/A"),
                "skill_required": task.get("assigned_role", "Unknown Role"),
                "required_resources": 1,
                "available_resources": 0,
                "missing_resources": 1,
            })

    return {
        "project": project_name,
        "has_shortage": len(shortages) > 0,
        "total_shortages": len(shortages),
        "shortages": shortages,
        "missing_roles": _aggregate_missing_roles(shortages),
    }


def _aggregate_missing_roles(shortages: List[Dict]) -> Dict[str, int]:
    """Aggregate missing role counts from individual shortages."""
    roles = {}
    for s in shortages:
        role = s["skill_required"]
        roles[role] = roles.get(role, 0) + s["missing_resources"]
    return roles


def save_diagnostic_report(project_name: str, validation_result: Dict[str, Any]) -> str:
    """
    Writes resource_diagnostic.json to the output directory.
    Returns the file path.
    """
    output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "project": project_name,
        "timestamp": datetime.now().isoformat(),
        "total_shortages": validation_result.get("total_shortages", 0),
        "missing_roles": validation_result.get("missing_roles", {}),
        "shortage_details": validation_result.get("shortages", []),
    }
    if "notification_issues" in validation_result:
        report["notification_issues"] = validation_result["notification_issues"]

    file_path = output_dir / f"{project_name}_resource_diagnostic.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"📋 Diagnostic report saved: {file_path}")
    return str(file_path)


def record_notification_issues(project_name: str, issues: List[Dict[str, Any]]) -> str:
    """
    Writes notification issues to output/<project_name>_notification_issues.json
    and synchronizes with output/<project_name>_resource_diagnostic.json if present.
    Returns the file path.
    """
    output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "project": project_name,
        "timestamp": datetime.now().isoformat(),
        "total_issues": len(issues),
        "notification_issues": issues,
    }

    file_path = output_dir / f"{project_name}_notification_issues.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"📋 Notification issues report saved: {file_path}")

    diag_path = output_dir / f"{project_name}_resource_diagnostic.json"
    if diag_path.exists():
        try:
            with open(diag_path, "r", encoding="utf-8") as f:
                diag = json.load(f)
            diag["notification_issues"] = issues
            with open(diag_path, "w", encoding="utf-8") as f:
                json.dump(diag, f, indent=2, default=str)
        except Exception as exc:
            print(f"⚠️ Could not update diagnostic file with notification issues: {exc}")

    return str(file_path)


def _build_workflow_state(
    project_name: str,
    status: str,
    missing_roles: Dict[str, int],
    diagnostic_path: str = "",
    resolution: str = "PENDING",
    notification_issues: Optional[List[Dict[str, Any]]] = None,
    run_id: Optional[str] = None,
    existing_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = dict(existing_state or {})

    state.update({
        "project": project_name,
        "status": status,
        "resolution": resolution,
        "diagnostic_file": diagnostic_path,
        "missing_roles": missing_roles,
        "timestamp": datetime.now().isoformat(),
    })

    if run_id is not None:
        state["run_id"] = run_id

    if notification_issues is not None:
        state["notification_issues"] = notification_issues

    return state


def update_workflow_state(
    project_name: str,
    status: str,
    missing_roles: Dict[str, int],
    diagnostic_path: str = "",
    resolution: str = "PENDING",
    notification_issues: Optional[List[Dict[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> str:
    """
    Writes/updates workflow_state.yaml in the output directory.
    Returns the file path.
    """
    output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / f"{project_name}_workflow_state.yaml"

    with _WORKFLOW_STATE_LOCK:
        existing_state = {}

        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                loaded_state = yaml.safe_load(f)

            if not isinstance(loaded_state, dict):
                raise ValueError(
                    f"Invalid workflow state in {file_path}: expected a YAML mapping."
                )

            existing_state = loaded_state

        state = _build_workflow_state(
            project_name=project_name,
            status=status,
            missing_roles=missing_roles,
            diagnostic_path=diagnostic_path,
            resolution=resolution,
            notification_issues=notification_issues,
            run_id=run_id,
            existing_state=existing_state,
        )

        _atomic_write_yaml(file_path, state)

    print(f"📝 Workflow state updated: {file_path} → status={status}")
    return str(file_path)


def conditional_update_workflow_state(
    project_name: str,
    expected_run_id: str,
    status: str,
    missing_roles: Dict[str, int],
    diagnostic_path: str = "",
    resolution: str = "PENDING",
    notification_issues: Optional[List[Dict[str, Any]]] = None,
    expected_status: Optional[str] = None,
    expected_resolution: Optional[str] = None,
) -> bool:
    """
    Conditionally update workflow_state.yaml only if its saved run_id (and optional
    expected_status / expected_resolution) match. Returns True if updated, False
    if missing, unreadable, or mismatched.
    """
    expected = str(expected_run_id or "").strip()
    if not expected:
        return False

    output_dir = DEFAULT_OUTPUT_DIR
    file_path = output_dir / f"{project_name}_workflow_state.yaml"

    with _WORKFLOW_STATE_LOCK:
        if not file_path.exists():
            return False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                existing_state = yaml.safe_load(f)
        except Exception:
            return False

        if not isinstance(existing_state, dict):
            return False

        saved_run_id = str(existing_state.get("run_id") or "").strip()
        if saved_run_id != expected:
            return False
        if expected_status is not None and existing_state.get("status") != expected_status:
            return False
        if expected_resolution is not None and existing_state.get("resolution") != expected_resolution:
            return False

        state = _build_workflow_state(
            project_name=project_name,
            status=status,
            missing_roles=missing_roles,
            diagnostic_path=diagnostic_path,
            resolution=resolution,
            notification_issues=notification_issues,
            run_id=expected,
            existing_state=existing_state,
        )
        _atomic_write_yaml(file_path, state)

    print(f"📝 Workflow state updated: {file_path} → status={status}")
    return True


def check_workflow_resolution(project_name: str) -> Dict[str, Any]:
    """
    Reads workflow_state.yaml and returns the current state.
    Returns dict with 'resolution', 'status', etc.
    """
    file_path = DEFAULT_OUTPUT_DIR / f"{project_name}_workflow_state.yaml"

    if not file_path.exists():
        return {"resolution": "NO_STATE_FILE", "status": "UNKNOWN"}

    with open(file_path, "r", encoding="utf-8") as f:
        state = yaml.safe_load(f)

    return state or {"resolution": "NO_STATE_FILE", "status": "UNKNOWN"}
