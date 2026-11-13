import pandas as pd
from pathlib import Path
from meetings.integrations.calendar_rsvp_reader import get_event_attendee_status
from meetings.integrations.gmail_proposal_reader import read_project_proposals


class RSVPChecker:
    """
    Single responsibility: read RSVP status for a meeting event
    and sync it into the Participants file.
    """

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def check(self, project_name: str, event_id: str) -> dict:
        """
        Fetch live RSVP status from Google Calendar + Gmail fallback.
        Updates Participants file and returns a summary dict.
        """
        status_map = get_event_attendee_status(event_id)
        if not status_map:
            return {"accepted": 0, "declined": 0, "tentative": 0, "awaiting": 0}

        gmail_proposals = read_project_proposals(project_name)

        counts = {"accepted": 0, "declined": 0, "tentative": 0, "awaiting": 0}
        for email, data in status_map.items():
            r = data.get("response", "")
            if r == "accepted":   counts["accepted"] += 1
            elif r == "declined": counts["declined"] += 1
            elif r == "tentative":counts["tentative"] += 1
            else:                  counts["awaiting"] += 1

            # Enrich with Gmail if Calendar has no reason/time
            if email.lower() in gmail_proposals:
                gp = gmail_proposals[email.lower()]
                if not data.get("comment"):
                    data["comment"] = gp.get("REASON", "")
                if not data.get("proposed_start"):
                    data["proposed_start"] = gp.get("PROPOSED_TIME", "")

        self._sync_participants(project_name, event_id, status_map)
        self._update_meeting_status(project_name, event_id, counts)

        print(f"📊 RSVP — Accepted:{counts['accepted']} Declined:{counts['declined']} "
              f"Tentative:{counts['tentative']} Awaiting:{counts['awaiting']}")
        return {"status_map": status_map, "counts": counts}

    def _sync_participants(self, project_name: str, event_id: str, status_map: dict):
        participant_file = self._output_dir / f"{project_name}_Participants.xlsx"
        if not participant_file.exists():
            return

        df = pd.read_excel(participant_file)
        for col in ["Response", "Reason", "Suggested_Time", "Template_Sent", "Consensus_Response"]:
            if col not in df.columns:
                df[col] = ""
            else:
                df[col] = df[col].fillna("").astype(str).replace("nan", "")

        for email, data in status_map.items():
            mask = (df["Meeting_ID"] == event_id) & (df["Email"].str.lower() == email.lower())
            if df.loc[mask].empty:
                continue
            
            resp = data.get("response", "")
            df.loc[mask, "Response"] = resp
            df.loc[mask, "Reason"] = data.get("comment", "")
            df.loc[mask, "Suggested_Time"] = data.get("proposed_start", "")

            # If the response is a consensus vote, store it separately
            if resp in ["AGREE", "DISAGREE"]:
                df.loc[mask, "Consensus_Response"] = resp

        df.to_excel(participant_file, index=False)

    def _update_meeting_status(self, project_name: str, event_id: str, counts: dict):
        meetings_file = self._output_dir / f"{project_name}_Meetings.xlsx"
        if not meetings_file.exists():
            return

        if counts["declined"] > 0:
            status = "Attention Required"
        elif counts["awaiting"] > 0:
            status = "Awaiting Responses"
        else:
            status = "Confirmed"

        df = pd.read_excel(meetings_file)
        df.loc[df["Event_ID"] == event_id, "Status"] = status
        df.to_excel(meetings_file, index=False)
