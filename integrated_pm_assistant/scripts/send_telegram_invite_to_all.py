"""
send_telegram_invite_to_all.py
--------------------------------
Sends the Telegram bot registration invite email to ALL employees
currently in employees.xlsx who have not yet registered.

Reads employees.xlsx → filters those without a telegram_chat_id →
sends each one an email with the bot link and /start instructions.

Usage:
    cd integrated_pm_assistant
    python scripts/send_telegram_invite_to_all.py

Prerequisites:
    - .env must have SENDER_EMAIL and SENDER_PASSWORD set
    - config.yaml must have telegram.bot_username set to your actual bot username
"""

import os
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

# Load .env variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config_loader import load_config
from data_backend import get_backend
from communication.email.email_notifier import EmailNotifier


def main():
    config = load_config()
    backend = get_backend()

    # ── Read bot_username from config ────────────────────────────────────────
    bot_username = config.get("telegram", {}).get("bot_username", "")
    if not bot_username or bot_username == "YourBotUsername":
        print("❌ ERROR: Set your actual bot username in config.yaml → telegram.bot_username")
        print("   Example: bot_username: \"pm_assistant_bot\"")
        sys.exit(1)

    # ── Build email notifier ─────────────────────────────────────────────────
    smtp = config["smtp"]
    email_notifier = EmailNotifier(
        sender_email=os.environ[smtp["email_env"]],
        sender_password=os.environ[smtp["password_env"]],
        smtp_host=smtp["host"],
        smtp_port=smtp["port"],
    )

    # ── Load employees ───────────────────────────────────────────────────────
    df = backend.read("employees")

    # Ensure telegram columns exist
    if "telegram_chat_id" not in df.columns:
        df["telegram_chat_id"] = None
    if "telegram_enabled" not in df.columns:
        df["telegram_enabled"] = False

    # Filter: only employees who have NOT registered yet
    unregistered = df[
        (df["telegram_chat_id"].isna())
        | (df["telegram_chat_id"].astype(str).str.strip() == "")
        | (df["telegram_chat_id"].astype(str).str.lower() == "none")
    ]

    if unregistered.empty:
        print("✅ All employees are already registered on Telegram!")
        return

    print(f"📋 Found {len(unregistered)} unregistered employee(s) out of {len(df)} total.")
    print(f"🤖 Bot link: https://t.me/{bot_username}")
    print(f"{'─' * 50}")

    sent = 0
    failed = 0

    for _, row in unregistered.iterrows():
        name = str(row.get("Employee_Name", "Team Member"))
        email = str(row.get("Email", ""))

        if not email or email.lower() == "nan":
            print(f"   ⚠️  Skipping {name} — no email address")
            failed += 1
            continue

        try:
            email_notifier.send_telegram_registration_invite(
                to_email=email,
                employee_name=name,
                bot_username=bot_username,
            )
            sent += 1
        except Exception as exc:
            print(f"   ❌ Failed to send to {name} ({email}): {exc}")
            failed += 1

    print(f"{'─' * 50}")
    print(f"✅ Done! Sent: {sent} | Failed: {failed}")
    print(f"\n💡 Next step: Start the registration handler to capture /start responses:")
    print(f"   python scripts/start_registration_listener.py")


if __name__ == "__main__":
    main()
