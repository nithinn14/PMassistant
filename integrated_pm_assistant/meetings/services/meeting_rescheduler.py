import datetime
import pandas as pd
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from meetings.integrations.calendar_rsvp_reader import get_event_attendee_status
from meetings.integrations.gmail_proposal_reader import read_project_proposals


class MeetingRescheduler:
    """
    Single responsibility: reschedule a meeting when quorum has
    responded and a valid time proposal exists (Calendar or Gmail).
    """

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, output_dir: Path, token_path: Path, consensus_service=None):
        self._output_dir = output_dir
        self._token_path = token_path
        self._consensus_service = consensus_service

    def reschedule_if_proposed(self, project_name: str, meeting_type: str) -> dict:
        """
        Check for proposals and manage the consensus state machine.
        """
        meetings_file = self._output_dir / f"{project_name}_Meetings.xlsx"
        participant_file = self._output_dir / f"{project_name}_Participants.xlsx"

        meetings_df = pd.read_excel(meetings_file)
        meeting_mask = (meetings_df["Project"] == project_name) & (meetings_df["Meeting_Type"] == meeting_type)
        if not any(meeting_mask):
            return {"status": "not_found"}

        latest = meetings_df[meeting_mask].iloc[-1]
        event_id = latest["Event_ID"]
        current_status = latest["Status"]

        if current_status == "Rescheduled":
            return {"status": "already_rescheduled"}

        status_map = get_event_attendee_status(event_id)

        # ── State 1: Awaiting Consensus ──────────────────────────────────────
        if current_status == "Awaiting Consensus":
            if self._consensus_service and self._consensus_service.check_consensus(project_name, event_id):
                proposed_time = pd.to_datetime(latest["Proposed_Time"])
                self._apply_reschedule(project_name, event_id, proposed_time, meetings_df, meetings_file)
                return {"status": "rescheduled", "new_time": str(proposed_time)}
            return {"status": "waiting_for_consensus"}

        # ── State 2: Scheduled (Looking for new proposals) ───────────────────
        awaiting_replies = [e for e, d in status_map.items() if d["response"] in ["needsAction", "tentative"]]
        if awaiting_replies:
            return {"status": "waiting_for_quorum", "awaiting": awaiting_replies}

        proposed_time, proposed_by = self._find_proposal(event_id, project_name, status_map, participant_file)

        if not proposed_time:
            return {"status": "no_proposal_found"}

        # New proposal found! Transition to Awaiting Consensus
        meetings_df.loc[meeting_mask, ["Status", "Proposed_Time", "Proposed_By"]] = \
            ["Awaiting Consensus", str(proposed_time), proposed_by]
        meetings_df.to_excel(meetings_file, index=False)

        # Proposer effectively 'AGREEs' to their own proposal
        participants_df = pd.read_excel(participant_file)
        p_mask = (participants_df["Meeting_ID"] == event_id) & (participants_df["Email"].str.lower() == proposed_by.lower())
        participants_df.loc[p_mask, "Consensus_Response"] = "AGREE"
        participants_df.to_excel(participant_file, index=False)

        if self._consensus_service:
            self._consensus_service.request_consensus(project_name, event_id, proposed_by, str(proposed_time))
            return {"status": "consensus_requested", "proposed_time": str(proposed_time), "by": proposed_by}

        return {"status": "proposal_received_but_no_consensus_service"}

    def _find_proposal(self, event_id, project_name, status_map, participant_file):
        """Find a valid proposal from Calendar or Gmail. Returns (time, email) or (None, None)."""
        participants_df = pd.read_excel(participant_file)
        meeting_participants = participants_df[participants_df["Meeting_ID"] == event_id]

        # Priority 1: native Google Calendar proposal
        for email, data in status_map.items():
            if data.get("proposed_start"):
                person = meeting_participants[meeting_participants["Email"].str.lower() == email.lower()]
                if not person.empty and person.iloc[0]["Role"] in ["R", "A"]:
                    return pd.to_datetime(data["proposed_start"]), email

        # Priority 2: Gmail proposal
        for email, gp in read_project_proposals(project_name).items():
            if gp.get("RESPONSE") == "DECLINE" and gp.get("PROPOSED_TIME"):
                person = meeting_participants[meeting_participants["Email"].str.lower() == email.lower()]
                if not person.empty and person.iloc[0]["Role"] in ["R", "A"]:
                    try:
                        return pd.to_datetime(gp["PROPOSED_TIME"]), email
                    except Exception:
                        continue

        return None, None

    def _apply_reschedule(self, project_name, event_id, new_start, meetings_df, meetings_file):
        """Patch the Google Calendar event and update local files."""
        new_end = new_start + datetime.timedelta(minutes=45)

        creds = Credentials.from_authorized_user_file(str(self._token_path), self.SCOPES)
        service = build("calendar", "v3", credentials=creds)

        event = service.events().get(calendarId="primary", eventId=event_id).execute()
        event["start"]["dateTime"] = new_start.isoformat()
        event["end"]["dateTime"] = new_end.isoformat()
        service.events().update(calendarId="primary", eventId=event_id, body=event, sendUpdates="all").execute()

        meetings_df.loc[meetings_df["Event_ID"] == event_id, ["Start_Time", "End_Time", "Status"]] = \
            [new_start, new_end, "Rescheduled"]
        meetings_df.to_excel(meetings_file, index=False)

        self._append_history(project_name, event_id, new_start, new_end)
        print(f"🔁 Meeting rescheduled → {new_start}")

    def _append_history(self, project_name, event_id, new_start, new_end):
        history_file = self._output_dir / f"{project_name}_reschedule_history.xlsx"
        row = pd.DataFrame([{
            "Project": project_name,
            "Event_ID": event_id,
            "New_Start": new_start.isoformat(),
            "New_End": new_end.isoformat(),
            "Rescheduled_At": datetime.datetime.now().isoformat(),
        }])
        if history_file.exists():
            row = pd.concat([pd.read_excel(history_file), row], ignore_index=True)
        row.to_excel(history_file, index=False)
