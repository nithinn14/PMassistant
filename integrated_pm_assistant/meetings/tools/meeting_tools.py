"""
meeting_tools.py
-----------------
ADK FunctionTool wrappers for meeting lifecycle operations.

Each tool is a standalone function that initialises its own dependencies
from config.yaml and environment variables — no class instantiation
needed by the calling agent.

Tools:
  • create_meeting_tool   — Create a calendar event for a project meeting
  • check_rsvp_tool       — Fetch live RSVP status from Google Calendar
  • send_reminder_tool    — Email escalation to declined R/A participants
  • reschedule_meeting_tool — Reschedule if a valid time proposal exists
"""

import json
import os
from pathlib import Path

from paths import APP_ROOT, TOKEN_FILE, resolve_path

from google.adk.tools.function_tool import FunctionTool

from config_loader import load_config


# ---------------------------------------------------------------------------
# Shared helpers (lazy-initialised from config)
# ---------------------------------------------------------------------------

def _output_dir() -> Path:
    config = load_config()
    excel_cfg = config.get("data_backend", {}).get("excel", {})

    root = resolve_path(excel_cfg.get("root_dir", APP_ROOT), APP_ROOT)
    path = resolve_path(excel_cfg.get("output_dir", "output"), root)

    path.mkdir(parents=True, exist_ok=True)
    return path


def _token_path() -> Path:
    return TOKEN_FILE

# ---------------------------------------------------------------------------
# Tool 1: Create Meeting
# ---------------------------------------------------------------------------

def create_meeting(project_name: str, meeting_type: str) -> str:
    """
    Create a Google Calendar meeting for the given project.

    Args:
        project_name: Name of the project (must have a _Scheduled.xlsx file).
        meeting_type: One of 'Kickoff', 'Mid-Project Review', 'Final Review'.

    Returns:
        JSON string with meeting status and details.
    """
    from meetings.integrations.real_meet import notify_project_level_meetings

    output_dir = _output_dir()
    excel_path = str(output_dir / f"{project_name}_Scheduled.xlsx")

    result = notify_project_level_meetings(
        task_excel_path=excel_path,
        project_name=project_name,
        meeting_type=meeting_type,
    )
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Tool 2: Check RSVP
# ---------------------------------------------------------------------------

def check_rsvp(project_name: str, meeting_type: str) -> str:
    """
    Check live RSVP status for a project meeting from Google Calendar.
    Updates the Participants Excel file with latest responses.

    Args:
        project_name: Name of the project.
        meeting_type: One of 'Kickoff', 'Mid-Project Review', 'Final Review'.

    Returns:
        JSON string with RSVP counts (accepted, declined, tentative, awaiting).
    """
    import pandas as pd
    from meetings.services.rsvp_checker import RSVPChecker

    output_dir = _output_dir()
    meetings_file = output_dir / f"{project_name}_Meetings.xlsx"

    if not meetings_file.exists():
        return json.dumps({"error": f"No meetings file found for '{project_name}'"})

    meetings_df = pd.read_excel(meetings_file)
    meeting_row = meetings_df[
        (meetings_df["Project"] == project_name) &
        (meetings_df["Meeting_Type"] == meeting_type)
    ]

    if meeting_row.empty:
        return json.dumps({"error": f"No '{meeting_type}' meeting found for '{project_name}'"})

    event_id = meeting_row.iloc[-1]["Event_ID"]

    checker = RSVPChecker(output_dir=output_dir)
    result = checker.check(project_name, event_id)
    print("RSVP status checked")
    return json.dumps(result, default=str)


def _build_notifiers():
    config = load_config()
    smtp = config["smtp"]
    from communication.email.email_notifier import EmailNotifier
    email_notifier = EmailNotifier(
        sender_email=os.environ[smtp["email_env"]],
        sender_password=os.environ[smtp["password_env"]],
        smtp_host=smtp["host"],
        smtp_port=smtp["port"],
    )

    telegram_notifier = None
    tg_cfg = config.get("telegram", {})
    if tg_cfg.get("enabled", False):
        bot_token = os.environ.get(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        pm_chat_id = os.environ.get(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")
        if bot_token:
            from communication.telegram.telegram_notifier import TelegramNotifier
            telegram_notifier = TelegramNotifier(bot_token=bot_token, pm_chat_id=pm_chat_id)
    
    return email_notifier, telegram_notifier


# ---------------------------------------------------------------------------
# Tool 3: Send Reminders
# ---------------------------------------------------------------------------

def send_reminder(project_name: str, meeting_type: str) -> str:
    """
    Send escalation reminder emails to participants who declined a meeting
    without proposing an alternative time. Only R/A role participants
    are escalated.
    """
    from meetings.services.reminder_service import ReminderService

    output_dir = _output_dir()
    email_notifier, telegram_notifier = _build_notifiers()

    service = ReminderService(
        output_dir=output_dir, 
        email_notifier=email_notifier,
        telegram_notifier=telegram_notifier
    )
    result = service.send_reminders(project_name, meeting_type)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Tool 4: Reschedule Meeting
# ---------------------------------------------------------------------------

def reschedule_meeting(project_name: str, meeting_type: str) -> str:
    """
    Check if a valid reschedule proposal exists (from Calendar or Gmail)
    and manage the semi-automated consensus gathering process.
    """
    from meetings.services.meeting_rescheduler import MeetingRescheduler
    from meetings.services.consensus_service import ConsensusService

    output_dir = _output_dir()
    token = _token_path()
    
    email_notifier, telegram_notifier = _build_notifiers()
    consensus_svc = ConsensusService(
        output_dir=output_dir,
        email_notifier=email_notifier,
        telegram_notifier=telegram_notifier
    )

    rescheduler = MeetingRescheduler(
        output_dir=output_dir, 
        token_path=token,
        consensus_service=consensus_svc
    )
    result = rescheduler.reschedule_if_proposed(project_name, meeting_type)
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# ADK FunctionTool registrations
# ---------------------------------------------------------------------------

create_meeting_tool = FunctionTool(create_meeting)
check_rsvp_tool = FunctionTool(check_rsvp)
send_reminder_tool = FunctionTool(send_reminder)
reschedule_meeting_tool = FunctionTool(reschedule_meeting)
