import pandas as pd
from pathlib import Path
from meetings.integrations.calendar_rsvp_reader import get_event_attendee_status


class ReminderService:
    """
    Single responsibility: send escalation reminders to R/A participants
    who declined without providing a proposed alternative time.
    Depends on an EmailNotifier injected via constructor.
    """

    def __init__(self, output_dir: Path, email_notifier, telegram_notifier=None):
        self._output_dir = output_dir
        self._email_notifier = email_notifier
        self._telegram_notifier = telegram_notifier

    def send_reminders(self, project_name: str, meeting_type: str) -> dict:
        """
        Check for declined R/A participants and send proposal request emails.
        Returns a dict with escalated email addresses.
        """
        meetings_file = self._output_dir / f"{project_name}_Meetings.xlsx"
        participant_file = self._output_dir / f"{project_name}_Participants.xlsx"

        meetings_df = pd.read_excel(meetings_file)
        meeting_row = meetings_df[
            (meetings_df["Project"] == project_name) &
            (meetings_df["Meeting_Type"] == meeting_type)
        ]
        event_id = meeting_row.iloc[-1]["Event_ID"]

        rsvp_status = get_event_attendee_status(event_id)
        participants_df = pd.read_excel(participant_file)
        meeting_participants = participants_df[participants_df["Meeting_ID"] == event_id]

        escalated = []

        for email, data in rsvp_status.items():
            if data["response"] != "declined":
                continue

            person = meeting_participants[meeting_participants["Email"] == email]
            if person.empty:
                continue

            idx = person.index[0]
            role = person.iloc[0]["Role"]
            already_sent = str(person.iloc[0].get("Template_Sent", "")).upper() == "YES"
            has_reason = str(person.iloc[0].get("Reason", "")).strip().lower() not in ["", "nan", "nil"]
            has_time = str(person.iloc[0].get("Suggested_Time", "")).strip().lower() not in ["", "nan", "nil"]

            if already_sent or has_reason or has_time:
                continue

            if role in ["R", "A"]:
                self._email_notifier.send_reschedule_proposal_request(email, project_name)
                
                # ── Telegram Escalation (if available) ───────────────────────
                if self._telegram_notifier:
                    try:
                        employees_df = pd.read_excel(self._output_dir.parent / "employees.xlsx")
                        emp_row = employees_df[employees_df["Email"].str.strip().str.lower() == email.lower()]
                        if not emp_row.empty:
                            chat_id = str(emp_row.iloc[0].get("telegram_chat_id", "")).strip()
                            if chat_id.endswith(".0"): chat_id = chat_id[:-2]
                            emp_name = str(emp_row.iloc[0].get("Employee_Name", "")).strip()
                            
                            if chat_id and chat_id.lower() not in ("", "nan", "none"):
                                self._telegram_notifier.send_reschedule_proposal_request(
                                    chat_id=chat_id,
                                    project_name=project_name,
                                    employee_name=emp_name
                                )
                    except Exception as te:
                        print(f"⚠️ Telegram escalation failed for {email}: {te}")
                
                participants_df.at[idx, "Template_Sent"] = "YES"
                escalated.append(email)

        if escalated:
            participants_df.to_excel(participant_file, index=False)

        return {"escalated": escalated}
