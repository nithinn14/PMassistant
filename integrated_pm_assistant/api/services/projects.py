"""
api/services/projects.py
────────────────────────
Project query, discovery, and artifact-loading services for PMassistant.

Provides:
  - get_available_projects: Scans output directory and in-memory jobs to list known names.
  - resolve_project_name: Resolves case-insensitive, partial, or renamed project queries.
  - load_project_results: Aggregates PRD, task, schedule, and meeting artifacts for a project.
  - get_projects_overview: Summarizes generated projects with file counts and timestamps.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from api.job_runtime import jobs
from paths import DEFAULT_OUTPUT_DIR

OUTPUT_DIR = DEFAULT_OUTPUT_DIR


def get_available_projects(output_dir: Optional[Path] = None) -> List[str]:
    """Scan OUTPUT_DIR and memory jobs to find all existing project names."""
    if output_dir is None:
        output_dir = OUTPUT_DIR

    names = set()
    if output_dir.exists():
        for f in output_dir.glob("*_PRD.json"):
            if f.stem.endswith("_PRD"):
                names.add(f.stem[:-4])
        for suffix in ("_Tasks.xlsx", "_Assigned.xlsx", "_Scheduled.xlsx", "_PRD.pdf", "_workflow_state.yaml"):
            stem_suffix = suffix.split(".")[0]
            for f in output_dir.glob(f"*{suffix}"):
                if f.stem.endswith(stem_suffix):
                    names.add(f.stem[:-len(stem_suffix)])
    for j in jobs.values():
        if j.get("actual_project_name"):
            names.add(j["actual_project_name"])
        elif j.get("project_name"):
            names.add(j["project_name"])
    return sorted([n for n in names if n])


def resolve_project_name(query: str, output_dir: Optional[Path] = None) -> tuple[Optional[str], List[str]]:
    """
    Resolve a user query to matching existing projects.
    Returns:
        (canonical_name, []) if exactly 1 match
        (None, [matches...]) if >1 matches (ambiguous)
        (None, []) if 0 matches
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    # If query matches original_project_name of a renamed job, resolve first
    for j in jobs.values():
        if j.get("original_project_name") == query and j.get("actual_project_name"):
            query = j["actual_project_name"]
            break

    if not isinstance(query, str):
        print(f"⚠️ resolve_project_name got a non-string query: {query!r} (type: {type(query).__name__})")
        query = str(query[0]) if isinstance(query, list) and query else str(query)

    clean_q = query.strip()
    if not clean_q:
        return None, []

    available = get_available_projects(output_dir=output_dir)

    # 1. Exact case-sensitive match
    if clean_q in available:
        return clean_q, []

    # 2. Exact case-insensitive match
    exact_ci = [p for p in available if p.lower() == clean_q.lower()]
    if len(exact_ci) == 1:
        return exact_ci[0], []
    elif len(exact_ci) > 1:
        return None, exact_ci

    # 3. Partial / substring match (case-insensitive)
    q_lower = clean_q.lower()
    matches = [p for p in available if q_lower in p.lower()]
    if len(matches) == 1:
        return matches[0], []
    elif len(matches) > 1:
        return None, matches
    else:
        return None, []


def load_project_results(project_name: str, output_dir: Optional[Path] = None) -> tuple[dict, int]:
    """
    Load all result artifacts for a project.
    Returns:
        tuple[dict, int]: (response_payload, status_code)
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    canonical_name, ambiguous_matches = resolve_project_name(project_name, output_dir=output_dir)

    if ambiguous_matches:
        return {
            "ambiguous": True,
            "query": project_name,
            "matches": ambiguous_matches,
        }, 200

    if not canonical_name:
        return {
            "error": f"No project found matching '{project_name}'. Check the Projects page for available projects."
        }, 404

    project_name = canonical_name
    result = {"project_name": project_name}
    # Project integration settings (default to enabled for older projects)
    integrations_path = output_dir / f"{project_name}_integrations.json"
    result["integrations"] = {
        "google_calendar": True,
        "gmail": True,
        "telegram": True,
        "gantt": True,
    }

    if integrations_path.exists():
        try:
            with open(integrations_path, "r", encoding="utf-8") as f:
                saved_integrations = json.load(f)
            result["integrations"].update(saved_integrations)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Could not read integration settings for '{project_name}': {exc}")

    # PRD JSON
    prd_json_path = output_dir / f"{project_name}_PRD.json"
    if prd_json_path.exists():
        with open(prd_json_path, "r", encoding="utf-8") as f:
            result["prd"] = json.load(f)
        result["prd_json_url"] = f"/api/download/{project_name}_PRD.json"
        result["prd_pdf_url"] = f"/api/download/{project_name}_PRD.pdf"

    # Tasks
    tasks_path = output_dir / f"{project_name}_Tasks.xlsx"
    if tasks_path.exists():
        df = pd.read_excel(tasks_path)
        result["tasks"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Assigned
    assigned_path = output_dir / f"{project_name}_Assigned.xlsx"
    if assigned_path.exists():
        df = pd.read_excel(assigned_path)
        result["assigned"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Scheduled
    sched_path = output_dir / f"{project_name}_Scheduled.xlsx"
    if sched_path.exists():
        df = pd.read_excel(sched_path)
        df = df.where(pd.notnull(df), None)
        result["schedule"] = df.to_dict(orient="records")

    # Notification delivery status (real results, written by save_schedule_tool)
    status_path = output_dir / f"{project_name}_notification_status.json"
    if status_path.exists():
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status = json.load(f)
            both = [n for n, s in status.items() if s.get("telegram_sent") and s.get("email_sent")]
            telegram_only = [n for n, s in status.items() if s.get("telegram_sent") and not s.get("email_sent")]
            email_only = [n for n, s in status.items() if s.get("email_sent") and not s.get("telegram_sent")]
            not_reached = [n for n, s in status.items() if not s.get("telegram_sent") and not s.get("email_sent")]
            result["notification_status"] = {
                "both": both,
                "telegram_only": telegram_only,
                "email_only": email_only,
                "not_reached": not_reached,
                "total": len(status),
                "notified": len(both) + len(telegram_only) + len(email_only),
                "telegram_count": len(both) + len(telegram_only),
                "email_count": len(both) + len(email_only),
            }
        except Exception as exc:
            print(f"⚠️ Could not read notification status: {exc}")

    # Meetings
    meetings_path = output_dir / f"{project_name}_Meetings.xlsx"
    if meetings_path.exists():
        df = pd.read_excel(meetings_path)
        result["meetings"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Participants
    parts_path = output_dir / f"{project_name}_Participants.xlsx"
    if parts_path.exists():
        df = pd.read_excel(parts_path)
        result["participants"] = df.where(pd.notnull(df), None).to_dict(orient="records")

    # Meeting summary
    if "meetings" in result:
        meeting = result["meetings"][-1] if result["meetings"] else {}
        event_id = meeting.get("Event_ID")
        participants = result.get("participants", [])

        # Dashboard expects capitalized keys: Accepted, Declined, Tentative, Awaiting
        rsvp_counts = {"Accepted": 0, "Declined": 0, "Tentative": 0, "Awaiting": 0}

        count_debug = []
        for p in participants:
            # ONLY count participants for THIS meeting
            if event_id and p.get("Meeting_ID") != event_id:
                continue

            # Check both possible column names from different tool versions
            raw_status = p.get("RSVP_Status") or p.get("Response") or "Awaiting"
            status = str(raw_status).strip().lower()

            count_debug.append(f"{p.get('Email')}: {status}")

            if status == "accepted":
                rsvp_counts["Accepted"] += 1
            elif status == "declined":
                rsvp_counts["Declined"] += 1
            elif status == "tentative":
                rsvp_counts["Tentative"] += 1
            else:
                rsvp_counts["Awaiting"] += 1

        if count_debug:
            print(f"DEBUG: Project: {project_name}, Meeting: {event_id}, Statuses: {count_debug}")

        result["meeting_summary"] = {
            "status": meeting.get("Status", "Unknown"),
            "meeting_type": meeting.get("Meeting_Type", ""),
            "event_id": event_id,
            **rsvp_counts,
        }

    return result, 200


def get_projects_overview(output_dir: Optional[Path] = None) -> list[dict]:
    """List all generated projects by scanning *_PRD.json in the output dir."""
    if output_dir is None:
        output_dir = OUTPUT_DIR

    projects = []
    if output_dir.exists():
        for f in sorted(output_dir.glob("*_PRD.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            name = f.stem.replace("_PRD", "")
            project_files = list(output_dir.glob(f"{name}*"))
            mtime = f.stat().st_mtime
            modified = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %I:%M %p")
            projects.append({
                "name": name,
                "files": len(project_files),
                "modified": modified,
            })
    return projects


__all__ = [
    "OUTPUT_DIR",
    "get_available_projects",
    "resolve_project_name",
    "load_project_results",
    "get_projects_overview",
]
