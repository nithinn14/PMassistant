from __future__ import print_function

import datetime
import os
import pandas as pd
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/calendar"]

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"


def create_calendar_event(summary, description, start_time, end_time, attendees):
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    print("DEBUG: Checking Google Calendar credentials...")
    if not creds or not creds.valid:
        print("DEBUG: Credentials invalid or expired. Attempting refresh...")
        if creds and creds.expired and creds.refresh_token:
            try:
                print("DEBUG: Refreshing token...")
                creds.refresh(Request())
                print("DEBUG: Token refreshed successfully.")
            except Exception as e:
                print(f"❌ Failed to refresh Google Token: {e}")
                raise ValueError("Google Token expired and refresh failed. Please run 'python start_registration_listener.py' to re-authenticate.")
        else:
            print("DEBUG: No refresh token or creds missing.")
            raise ValueError("Google Calendar Token missing or invalid. Please run 'python start_registration_listener.py' to authenticate.")
        
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    else:
        print("DEBUG: Credentials are valid.")

    service = build("calendar", "v3", credentials=creds)
    attendees = list(set([email.strip() for email in attendees if email]))

    event_body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_time.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end_time.isoformat(), "timeZone": "Asia/Kolkata"},
        "attendees": [{"email": email} for email in attendees],
        "conferenceData": {
            "createRequest": {
                "requestId": f"pm-assistant-{datetime.datetime.now().timestamp()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }

    event = service.events().insert(
        calendarId="primary",
        body=event_body,
        conferenceDataVersion=1,
        sendUpdates="all"
    ).execute()

    return {
        "meet_link": event.get("hangoutLink"),
        "event_id": event.get("id"),
        "start": event["start"]["dateTime"],
        "end": event["end"]["dateTime"],
    }


def notify_project_level_meetings(task_excel_path: str, project_name: str, meeting_type: str):
    df = pd.read_excel(task_excel_path)
    df.columns = df.columns.str.strip()

    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    df = df.dropna(subset=["start_date", "end_date"])

    project_start = df["start_date"].min()
    project_end = df["end_date"].max()

    participants = df[
        (df["assigned_empl"] != "No Resource Available") &
        (df["assigned_email"].notna())
    ]["assigned_email"].drop_duplicates()

    if participants.empty:
        print(f"⚠️ No valid participants. Skipping {meeting_type}.")
        return {"status": "no_participants"}

    if meeting_type == "Kickoff":
        meeting_time = project_start.to_pydatetime().replace(hour=10, minute=0)
    elif meeting_type == "Mid-Project Review":
        mid_date = project_start + (project_end - project_start) / 2
        meeting_time = mid_date.to_pydatetime().replace(hour=14, minute=0)
    elif meeting_type == "Final Review":
        meeting_time = project_end.to_pydatetime().replace(hour=16, minute=0)
    else:
        raise ValueError(f"Unknown meeting_type: '{meeting_type}'")

    meeting_end = meeting_time + datetime.timedelta(minutes=45)

    task_summary = "\n\n--- PROJECT ASSIGNMENTS ---\n"
    for email in participants:
        emp_tasks = df[df["assigned_email"] == email]
        if not emp_tasks.empty:
            empl_name = emp_tasks.iloc[0].get("assigned_empl", "")
            task_col = "task_name" if "task_name" in df.columns else "task"
            id_col = "task_id" if "task_id" in df.columns else None
            task_lines = []
            for _, row in emp_tasks.iterrows():
                line = f"• {row[task_col]}"
                if id_col:
                    line += f" [{row[id_col]}]"
                task_lines.append(line)
            task_summary += f"\n👤 {empl_name}:\n" + "\n".join(task_lines) + "\n"

    # ── Idempotency Check: Don't create meeting if it already exists ────────
    master_file = OUTPUT_DIR / f"{project_name}_Meetings.xlsx"
    if master_file.exists():
        master_df = pd.read_excel(master_file)
        existing = master_df[(master_df["Project"] == project_name) & (master_df["Meeting_Type"] == meeting_type)]
        if not existing.empty:
            print(f"ℹ️ {meeting_type} meeting already exists for {project_name}. Skipping creation/notifications.")
            return {"status": "already_exists", "event_id": existing.iloc[-1]["Event_ID"]}

    meeting = create_calendar_event(
        summary=f"{project_name} - {meeting_type}",
        description=f"{meeting_type} for project: {project_name}.{task_summary}",
        start_time=meeting_time,
        end_time=meeting_end,
        attendees=participants.tolist()
    )

    participant_file = OUTPUT_DIR / f"{project_name}_Participants.xlsx"

    new_row = {
        "Project": project_name,
        "Meeting_Type": meeting_type,
        "Event_ID": meeting["event_id"],
        "Start_Time": meeting["start"],
        "End_Time": meeting["end"],
        "Status": "Scheduled",
        "Proposed_Time": "",
        "Proposed_By": ""
    }

    if master_file.exists():
        master_df = pd.read_excel(master_file)
        master_df = pd.concat([master_df, pd.DataFrame([new_row])], ignore_index=True)
        master_df.to_excel(master_file, index=False)
    else:
        pd.DataFrame([new_row]).to_excel(master_file, index=False)

    participant_data = [
        {"Meeting_ID": meeting["event_id"], "Email": email, "Role": str(df[df["assigned_email"] == email].iloc[0].get("RACI", "C")).strip().upper()}
        for email in participants
    ]
    new_participants_df = pd.DataFrame(participant_data)

    if participant_file.exists():
        existing = pd.read_excel(participant_file)
        final = pd.concat([existing, new_participants_df], ignore_index=True).drop_duplicates(subset=["Meeting_ID", "Email"])
        final.to_excel(participant_file, index=False)
    else:
        new_participants_df.to_excel(participant_file, index=False)

    print(f"✅ {meeting_type} meeting created successfully")
    
    # ── Meeting email with join link (in addition to the Google Calendar invite) ──
    try:
        from config_loader import load_config
        from agents.communication.factory import build_email_notifier

        mailer = build_email_notifier(load_config())
        meet_link = meeting.get("meet_link")
        when_date = meeting_time.strftime("%A, %d %B %Y")
        when_time = f"{meeting_time.strftime('%H:%M')} - {meeting_end.strftime('%H:%M')} IST"

        meeting_mail_sent, meeting_mail_failed = [], []
        for email in participants:
            person_rows = df[df["assigned_email"] == email]
            person = str(person_rows.iloc[0].get("assigned_empl", "")).strip() if not person_rows.empty else ""
            greeting = f"Hello {person}," if person and person.lower() != "nan" else "Hello,"
            link_line = (
                f"Join here: {meet_link}"
                if meet_link
                else "The join link is in the Google Calendar invite."
            )
            body = (
                f"{greeting}\n\n"
                f"A {meeting_type} meeting has been scheduled for the project {project_name}.\n\n"
                f"Date: {when_date}\n"
                f"Time: {when_time}\n"
                f"{link_line}\n\n"
                f"You will also receive a Google Calendar invite. Please respond to that invite "
                f"(Yes / No / Maybe) so your RSVP is recorded.\n\n"
                f"- PM Assistant"
            )
            ok = mailer.send(
                to_email=str(email).strip(),
                subject=f"{meeting_type} Meeting: {project_name}",
                message=body,
            )
            (meeting_mail_sent if ok else meeting_mail_failed).append(str(email))
        print(f"📧 Meeting emails: {len(meeting_mail_sent)} sent, {len(meeting_mail_failed)} failed")
    except Exception as mail_err:
        print(f"⚠️ Meeting emails skipped: {mail_err}")

    # ── Telegram notifications ──────────────────────────────────────────────
    try:
        from config_loader import load_config
        from agents.communication.notifiers.telegram_notifier import TelegramNotifier

        config = load_config()
        tg_cfg = config.get("telegram", {})
        if tg_cfg.get("enabled", False):
            bot_token = os.environ.get(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
            pm_chat_id = os.environ.get(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")

            if bot_token:
                tg = TelegramNotifier(bot_token=bot_token, pm_chat_id=pm_chat_id)

                # 1. PM-channel alert
                attendee_list = "\n".join(f"  • {e}" for e in participants)
                tg.send(
                    f"📅 *Meeting Scheduled*\n"
                    f"*Project:* {TelegramNotifier.escape_md(project_name)}\n"
                    f"*Type:* {TelegramNotifier.escape_md(meeting_type)}\n"
                    f"*Time:* {TelegramNotifier.escape_md(meeting_time.strftime('%Y-%m-%d %H:%M'))}\n\n"
                    f"*Attendees:*\n{TelegramNotifier.escape_md(attendee_list)}",
                    name="PM Alert"
                )

                # 2. Per-employee DMs (if they registered via /start)
                try:
                    employees_df = pd.read_excel(BASE_DIR / "employees.xlsx")
                    if "telegram_chat_id" in employees_df.columns:
                        for email in participants:
                            emp_row = employees_df[
                                employees_df["Email"].astype(str).str.strip().str.lower()
                                == str(email).strip().lower()
                            ]
                            if emp_row.empty:
                                continue
                            chat_id = str(emp_row.iloc[0].get("telegram_chat_id", "")).strip()
                            emp_name = str(emp_row.iloc[0].get("Employee_Name", "")).strip()
                            if chat_id and chat_id.lower() not in ("", "nan", "none"):
                                tg.send_meeting_alert(
                                    chat_id=chat_id,
                                    employee_name=emp_name,
                                    project_name=project_name,
                                    meeting_type=meeting_type,
                                    meeting_time=meeting_time.strftime("%Y-%m-%d %H:%M"),
                                )
                except Exception as tg_emp_err:
                    print(f"⚠️ Telegram per-employee DM failed: {tg_emp_err}")
            else:
                print("⚠️ Telegram bot token not configured — skipping Telegram alerts")
    except Exception as tg_err:
        print(f"⚠️ Telegram alert skipped: {tg_err}")

    return {"status": "success"}
