"""
setup_google_calendar_auth.py
------------------------------
One-time setup script: performs the interactive Google OAuth login that
produces the 'token.json' file required by all Calendar-related features
of this app (meeting scheduling, RSVP reading, rescheduling, etc.).

When to run:
    Run this ONCE, before using any Google Calendar feature for the first
    time, or whenever the existing token.json has been deleted or revoked.
    You do NOT need to run it again as long as token.json is present and
    valid — the app refreshes the token automatically when it expires.

What happens:
    1. The script checks that credentials.json is present.
    2. It opens your default web browser and asks you to log into Google
       and approve access to Google Calendar.
    3. Once you approve, the browser tab closes automatically and the
       script saves the resulting token to token.json alongside this file.
    4. After that, all Calendar features in the app will work without any
       further manual steps.

Usage (from the integrated_pm_assistant/ directory):
    python setup_google_calendar_auth.py

Requirements:
    - credentials.json must exist in integrated_pm_assistant/ (download it
      from Google Cloud Console -> APIs & Services -> Credentials, then
      choose the OAuth 2.0 Desktop client and click Download JSON).
    - The google-auth-oauthlib package must be installed (it is already
      listed in the project's requirements).
"""

import sys
from pathlib import Path

# ── Constants — must stay in sync with tools/real_meet.py ─────────────────
# real_meet.py defines:
#   BASE_DIR = Path(__file__).resolve().parents[1]   # -> integrated_pm_assistant/
#   SCOPES = ["https://www.googleapis.com/auth/calendar"]
#   CREDENTIALS_PATH = BASE_DIR / "credentials.json"
#   TOKEN_PATH       = BASE_DIR / "token.json"
#
# This script lives one level shallower (integrated_pm_assistant/ directly),
# so Path(__file__).resolve().parent gives the same BASE_DIR.

BASE_DIR = Path(__file__).resolve().parent          # integrated_pm_assistant/
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"


def main() -> None:
    # ── Guard 1: token already exists ─────────────────────────────────────
    if TOKEN_PATH.exists():
        print(
            f"\ntoken.json already exists at:\n  {TOKEN_PATH}\n\n"
            "If you want to re-authenticate (e.g. because the account changed\n"
            "or you revoked access in Google Account settings), delete that\n"
            "file first and then re-run this script.\n"
            "Nothing was changed."
        )
        sys.exit(0)

    # ── Guard 2: credentials.json must exist ──────────────────────────────
    if not CREDENTIALS_PATH.exists():
        print(
            f"\nERROR: credentials.json not found at:\n  {CREDENTIALS_PATH}\n\n"
            "To get it:\n"
            "  1. Go to https://console.cloud.google.com/\n"
            "  2. Select your project (pm-assistant-testing).\n"
            "  3. Navigate to APIs & Services -> Credentials.\n"
            "  4. Under 'OAuth 2.0 Client IDs', find the Desktop client\n"
            "     and click the download (arrow) icon on the right.\n"
            "  5. Rename the downloaded file to 'credentials.json' and\n"
            "     place it in:\n"
            f"     {BASE_DIR}\n"
        )
        sys.exit(1)

    # ── Import here so the guards above give clean errors even if the     ──
    # ── google packages are not installed                                  ──
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "\nERROR: google-auth-oauthlib is not installed.\n"
            "Run:  pip install google-auth-oauthlib\n"
            "then try again."
        )
        sys.exit(1)

    # ── Run the interactive OAuth flow ────────────────────────────────────
    print(
        "\nStarting Google Calendar OAuth login...\n"
        "A browser window will open shortly. Log in with the Google account\n"
        "you want to use for meeting scheduling, then click 'Allow'.\n"
        "(If no browser opens automatically, check the terminal for a URL\n"
        "you can paste into your browser manually.)\n"
    )

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        # port=0 lets the OS pick a free port; no manual code copy-pasting needed.
        creds = flow.run_local_server(port=0)

    except KeyboardInterrupt:
        print("\nLogin cancelled by user (Ctrl+C). No token was saved.")
        sys.exit(1)

    except FileNotFoundError:
        # Shouldn't happen after guard 2, but be safe.
        print(
            f"\nERROR: Could not read credentials.json at {CREDENTIALS_PATH}.\n"
            "Please verify the file exists and is not corrupted."
        )
        sys.exit(1)

    except Exception as exc:
        print(
            f"\nERROR: The OAuth login flow failed with the following error:\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "Common causes:\n"
            "  - You closed the browser before approving access.\n"
            "  - credentials.json is from a different Google project or is\n"
            "    corrupted — re-download it from Google Cloud Console.\n"
            "  - No internet connection was available during the login.\n"
            "\nNo token was saved. Fix the issue above and try again."
        )
        sys.exit(1)

    # ── Save the token — same format real_meet.py uses to write refreshes ─
    # real_meet.py:45-46:
    #   with open(TOKEN_PATH, "w") as token:
    #       token.write(creds.to_json())
    try:
        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())
    except OSError as exc:
        print(
            f"\nERROR: Login succeeded but failed to save token.json:\n"
            f"  {exc}\n"
            "Check that the directory is writable and try again."
        )
        sys.exit(1)

    print(
        f"\nSuccess! Google Calendar token saved to:\n  {TOKEN_PATH}\n\n"
        "You can now use all Calendar-related features of PM Assistant\n"
        "(meeting scheduling, RSVP reading, rescheduling).\n"
        "You do not need to run this script again unless the token is\n"
        "deleted or access is revoked."
    )


if __name__ == "__main__":
    main()
