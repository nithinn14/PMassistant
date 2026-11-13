"""
start_registration_listener.py
---------------------------------
Starts the Telegram registration listener that captures /start messages
from employees and stores their chat_id in employees.xlsx.

Run this AFTER sending invite emails (send_telegram_invite_to_all.py).
It will keep polling until you stop it (Ctrl+C).

Usage:
    cd integrated_pm_assistant
    python scripts/start_registration_listener.py
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

from communication.factory import build_registration_handler
from config_loader import load_config


def main():
    config = load_config()
    handler = build_registration_handler(config)

    print("=" * 50)
    print("  🤖 Telegram Registration Listener")
    print("=" * 50)
    print()
    print("Waiting for employees to press /start on the bot...")
    print("When they do, their chat_id will be saved to employees.xlsx.")
    print()
    print("Press Ctrl+C to stop.\n")

    try:
        handler.run_forever()
    except KeyboardInterrupt:
        print("\n\n✅ Listener stopped.")


if __name__ == "__main__":
    main()
