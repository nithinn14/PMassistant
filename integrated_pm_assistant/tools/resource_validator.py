"""
Resource Validator Tool
-----------------------
Validates resource assignments, generates diagnostic reports,
manages workflow state for shortage detection and resolution.
"""

import json
import yaml
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional


def validate_resources(project_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scans assigned tasks for shortages ("No Resource Available").
    Returns structured shortage report.
    """
    shortages = []

    for task in tasks:
        if task.get("Assigned_Employee") == "No Resource Available":
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
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

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
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

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


def update_workflow_state(
    project_name: str,
    status: str,
    missing_roles: Dict[str, int],
    diagnostic_path: str = "",
    resolution: str = "PENDING",
    notification_issues: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Writes/updates workflow_state.yaml in the output directory.
    Returns the file path.
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    state = {
        "project": project_name,
        "status": status,
        "resolution": resolution,
        "diagnostic_file": diagnostic_path,
        "missing_roles": missing_roles,
        "timestamp": datetime.now().isoformat(),
    }
    if notification_issues is not None:
        state["notification_issues"] = notification_issues

    file_path = output_dir / f"{project_name}_workflow_state.yaml"
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(state, f, default_flow_style=False, allow_unicode=True)

    print(f"📝 Workflow state updated: {file_path} → status={status}")
    return str(file_path)


def check_workflow_resolution(project_name: str) -> Dict[str, Any]:
    """
    Reads workflow_state.yaml and returns the current state.
    Returns dict with 'resolution', 'status', etc.
    """
    file_path = Path("output") / f"{project_name}_workflow_state.yaml"

    if not file_path.exists():
        return {"resolution": "NO_STATE_FILE", "status": "UNKNOWN"}

    with open(file_path, "r", encoding="utf-8") as f:
        state = yaml.safe_load(f)

    return state or {"resolution": "NO_STATE_FILE", "status": "UNKNOWN"}
