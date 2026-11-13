"""
sync_meetings.py
-----------------
Standalone utility to sync meeting lifecycle for an existing project.

Uses the meeting FunctionTools directly — no full pipeline run needed.
Checks RSVP status, sends reminders, and reschedules if proposals exist.
"""

import json
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from dotenv import load_dotenv
from paths import DEFAULT_OUTPUT_DIR

# Fix Windows encoding for emojis
sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
sys.stderr.reconfigure(encoding='utf-8')  # type: ignore

from meetings.tools.meeting_tools import check_rsvp, send_reminder, reschedule_meeting

MEETING_TYPES = ["Kickoff", "Mid-Project Review", "Final Review"]


def main():
    load_dotenv()

    print("\n--- Project Meeting Sync Utility ---")

    # List existing project meeting files
    print("\nFound existing project meeting files:")
    meeting_files = DEFAULT_OUTPUT_DIR.glob("*_Meetings.xlsx")

    meeting_files = list(meeting_files)

    if not meeting_files:
        print(" - No project-specific meeting files found.")
    else:
        for f in meeting_files:
            p_name = f.name.replace("_Meetings.xlsx", "")
            print(f" - {p_name}")

    project_name = input("\nEnter project name to sync: ").strip()

    if not project_name:
        print("❌ Error: Project name is required.")
        return

    print(f"\n🔄 Syncing meeting status for '{project_name}'...")

    results = []
    for m_type in MEETING_TYPES:
        print(f"\n── {m_type} ──")

        # Step 1: Check RSVP
        rsvp_result = json.loads(check_rsvp(project_name, m_type))
        if "error" in rsvp_result:
            results.append(f"{m_type}: {rsvp_result['error']}")
            continue

        counts = rsvp_result.get("counts", {})
        results.append(
            f"{m_type}: Accepted={counts.get('accepted', 0)}, "
            f"Declined={counts.get('declined', 0)}, "
            f"Awaiting={counts.get('awaiting', 0)}"
        )

        # Step 2: Send reminders if anyone declined
        if counts.get("declined", 0) > 0:
            reminder_result = json.loads(send_reminder(project_name, m_type))
            escalated = reminder_result.get("escalated", [])
            if escalated:
                print(f"  📧 Reminders sent to: {', '.join(escalated)}")

        # Step 3: Try to reschedule if proposals exist
        resched_result = json.loads(reschedule_meeting(project_name, m_type))
        if resched_result.get("status") == "rescheduled":
            print(f"  🔁 Rescheduled to: {resched_result.get('new_time')}")
            results[-1] += f" → Rescheduled to {resched_result.get('new_time')}"

    # Final summary
    print("\n✅ Sync Completed Successfully")
    print("\nResults:")
    for res in results:
        print(f" - {res}")

    print(f"\nCheck '{DEFAULT_OUTPUT_DIR / f'{project_name}_Meetings.xlsx'}' for the latest status.")

if __name__ == "__main__":
    main()
