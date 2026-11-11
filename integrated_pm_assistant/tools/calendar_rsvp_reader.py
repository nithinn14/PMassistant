from paths import TOKEN_FILE

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from typing import Optional, Dict, Any

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_event_attendee_status(event_id: str) -> Optional[Dict[str, Any]]:

    if not TOKEN_FILE.exists():
        print("❌ token.json not found.")
        return None

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    service = build("calendar", "v3", credentials=creds)

    event = service.events().get(
        calendarId="primary",
        eventId=event_id
    ).execute()

    attendees = event.get("attendees", [])

    status_map = {}

    for attendee in attendees:
        email = attendee.get("email")
        response = attendee.get("responseStatus")
        comment = attendee.get("comment", "") # Reason for decline/accepted
        
        # Native "Propose a new time" data
        # Google API returns: attendee.proposedTime.start = {"dateTime": "..."}
        proposal = attendee.get("proposedTime")
        p_start = proposal.get("start", {}).get("dateTime") if proposal else None
        p_end = proposal.get("end", {}).get("dateTime") if proposal else None

        if proposal:
            print(f"🗓 Calendar proposal found from {email}: {p_start}")
        elif response == "declined":
            print(f"⚠ {email} declined but no Calendar proposal found — will check Gmail fallback.")

        status_map[email] = {
            "response": response,
            "comment": comment,
            "proposed_start": p_start,
            "proposed_end": p_end
        }

    return status_map
