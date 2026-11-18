"""
api/routes/meetings.py
──────────────────────
FastAPI route definitions for meeting synchronization, RSVPs, reminders,
and rescheduling.
"""

import json
from typing import Optional

from fastapi import APIRouter

from api.routes.projects import project_results
from api.schemas import SyncMeetingsRequest
from api.serialization import _safe_jsonify
from meetings.tools.meeting_tools import check_rsvp, reschedule_meeting, send_reminder

router = APIRouter()


@router.post("/api/sync-meetings")
def sync_meetings(req: Optional[SyncMeetingsRequest] = None):
    """Sync RSVPs, send reminders, and potentially reschedule meetings."""
    req_dict = req.model_dump() if req else {}
    project_name = (req_dict.get("project_name") or "").strip()
    meeting_type = (req_dict.get("meeting_type") or "Kickoff").strip()

    if not project_name:
        return _safe_jsonify({"error": "project_name is required"}, status=400)

    try:
        # Step 1: Check RSVP
        rsvp_res = json.loads(check_rsvp(project_name, meeting_type))
        if "error" in rsvp_res:
            return _safe_jsonify({"error": rsvp_res["error"]}, status=400)

        counts = rsvp_res.get("counts", {})

        # Step 2: Send reminders if anyone declined
        if counts.get("declined", 0) > 0:
            send_reminder(project_name, meeting_type)

        # Step 3: Try to reschedule if proposals exist
        reschedule_meeting(project_name, meeting_type)

        # Return updated project results for the UI to refresh
        return project_results(project_name)

    except Exception as e:
        return _safe_jsonify({"error": str(e)}, status=500)


__all__ = [
    "router",
    "sync_meetings",
]
