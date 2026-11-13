"""
telegram_registration_handler.py
----------------------------------
Polls the Telegram Bot API for /start messages and auto-registers employees.

Workflow:
  1. Employee receives onboarding email with the bot link (t.me/<bot_username>)
  2. Employee clicks link and presses Start in Telegram
  3. This handler (running in background polling) captures the update
  4. Extracts chat_id, username, first_name from the Telegram update
  5. Matches to an employee record (by first_name or username)
  6. Saves chat_id to employees.xlsx via update_telegram_chat_id()
  7. Sends a welcome confirmation DM back to the employee

Usage:
    # Run once during onboarding / system startup:
    from communication.telegram.telegram_registration_handler import TelegramRegistrationHandler
    handler = TelegramRegistrationHandler(bot_token="...", pm_chat_id="...")
    handler.listen_for_registrations(timeout_seconds=300)  # blocking poll

    # Or run continuously in background thread:
    import threading
    t = threading.Thread(target=handler.run_forever, daemon=True)
    t.start()

ADK Integration:
    Call handler.listen_for_registrations() as a one-shot tool call, or run
    run_forever() in a daemon thread alongside the ADK agent.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

from communication.telegram.employee_database import (
    get_employee_by_name,
    update_telegram_chat_id,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30      # long-poll timeout (seconds) sent to Telegram servers
REQUEST_TIMEOUT = 40   # requests lib timeout (must be > POLL_TIMEOUT)


class TelegramRegistrationHandler:
    """
    Polls the Telegram Bot API for /start messages and registers employees.

    Args:
        bot_token:   Telegram Bot API token (from BotFather).
        pm_chat_id:  Optional PM chat_id; if set, a notification is sent to the
                     PM whenever a new employee registers.

    This class is entirely self-contained and does not depend on ADK runtime.
    It interacts with the file-based employee backend via employee_database.py.
    """

    def __init__(self, bot_token: str, pm_chat_id: Optional[str] = None):
        self._token = bot_token
        self._pm_chat_id = pm_chat_id
        self._offset: int = 0   # Telegram getUpdates offset (sliding window)

    # ── Public API ───────────────────────────────────────────────────────────

    def listen_for_registrations(self, timeout_seconds: int = 300) -> Dict[str, Any]:
        """
        Poll Telegram for /start messages for `timeout_seconds` wall-clock seconds.

        Suitable for a one-shot call (e.g., triggered by the ADK agent after
        sending onboarding emails to a new batch of employees).

        Returns a summary dict:
            {"registered": [...], "skipped": [...], "errors": [...]}
        """
        logger.info("🤖 Telegram registration handler started (timeout=%ds)", timeout_seconds)
        end_time = time.time() + timeout_seconds
        summary: Dict[str, list] = {"registered": [], "skipped": [], "errors": []}

        while time.time() < end_time:
            updates = self._get_updates()
            for update in updates:
                self._process_update(update, summary)

        logger.info(
            "✅ Registration poll complete — registered: %d, skipped: %d, errors: %d",
            len(summary["registered"]), len(summary["skipped"]), len(summary["errors"]),
        )
        return summary

    def run_forever(self, sleep_on_error: int = 5) -> None:
        """
        Blocking infinite polling loop — intended to run in a daemon thread.

        Suitable for production deployments where you want the bot to
        continuously accept new employee registrations.
        """
        logger.info("🤖 Telegram registration handler running (infinite loop)")
        while True:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._process_update(update, summary=None)
            except Exception as exc:  # noqa: BLE001
                logger.error("❌ Registration poll error: %s — retrying in %ds", exc, sleep_on_error)
                time.sleep(sleep_on_error)

    # ── Parsing helpers (also unit-testable without network) ─────────────────

    def _parse_start_update(self, update: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Extract registration data from a Telegram update dict.

        Returns a dict with keys: chat_id, username, first_name
        Returns None if the update is not a /start message.
        """
        msg = update.get("message", {})
        text = msg.get("text", "").strip()

        # Only handle /start (may include a payload: "/start <payload>")
        if not text.startswith("/start"):
            return None

        chat = msg.get("chat", {})
        sender = msg.get("from", {})

        return {
            "chat_id": str(chat.get("id", "")),
            "username": sender.get("username", ""),
            "first_name": sender.get("first_name", ""),
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_updates(self) -> list:
        """Call Telegram getUpdates with long polling. Returns list of updates."""
        url = TELEGRAM_API.format(token=self._token, method="getUpdates")
        params = {"offset": self._offset, "timeout": POLL_TIMEOUT}

        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            if not data.get("ok"):
                logger.warning("⚠️ Telegram getUpdates not ok: %s", data)
                return []

            updates = data.get("result", [])
            if updates:
                # Advance offset to avoid re-processing the same updates
                self._offset = updates[-1]["update_id"] + 1
            return updates

        except requests.exceptions.Timeout:
            # Long-poll timeout is expected — not an error
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error("❌ getUpdates error: %s", exc)
            return []

    def _process_update(self, update: Dict[str, Any], summary: Optional[Dict]) -> None:
        """Process one Telegram update — register employee if it's a /start message."""
        parsed = self._parse_start_update(update)
        if not parsed:
            return  # Not a /start message — ignore silently

        chat_id = parsed["chat_id"]
        username = parsed["username"]
        first_name = parsed["first_name"]

        logger.info("📲 /start from chat_id=%s username=%s first_name=%s", chat_id, username, first_name)

        # ── Step 1: Try to match this Telegram user to an employee ──────────
        # We try first_name first (most common), then username
        employee = self._match_employee(first_name, username)

        if "error" in employee:
            # Could not match — send a helpful DM to guide the user
            self._send_unmatched_message(chat_id, first_name or username)
            logger.warning(
                "⚠️ /start from unknown user: first_name=%s username=%s — no employee matched",
                first_name, username,
            )
            if summary is not None:
                summary["skipped"].append({"chat_id": chat_id, "username": username, "reason": "no_employee_match"})
            return

        # ── Step 2: Save chat_id to the employee database ───────────────────
        emp_email = employee.get("Email", "")
        result = update_telegram_chat_id(
            email=str(emp_email),
            chat_id=chat_id,
            username=username,
        )

        if "error" in result:
            logger.error("❌ Failed to save chat_id for %s: %s", emp_email, result["error"])
            if summary is not None:
                summary["errors"].append({"chat_id": chat_id, "error": result["error"]})
            return

        emp_name = result.get("employee", first_name)

        # ── Step 3: Send a welcome DM to the employee ───────────────────────
        self._send_welcome_message(chat_id, emp_name)

        # ── Step 4: Notify PM if configured ─────────────────────────────────
        if self._pm_chat_id:
            self._send_message(
                self._pm_chat_id,
                f"✅ <b>New Telegram Registration</b>\n\n"
                f"👤 Employee: {emp_name}\n"
                f"📲 Chat ID: {chat_id}\n"
                f"They will now receive direct Telegram notifications.",
            )

        if summary is not None:
            summary["registered"].append({"employee": emp_name, "chat_id": chat_id})

    def _match_employee(self, first_name: str, username: str) -> Dict[str, Any]:
        """Try to match a Telegram user to an employee by first_name, then username."""
        if first_name:
            result = get_employee_by_name(first_name)
            if "error" not in result:
                return result

        if username:
            result = get_employee_by_name(username)
            if "error" not in result:
                return result

        return {"error": "no_match"}

    def _send_welcome_message(self, chat_id: str, emp_name: str) -> None:
        """Send a welcome confirmation DM to a newly registered employee."""
        message = (
            f"👋 Hello <b>{emp_name}</b>!\n\n"
            f"You are now registered with the <b>PM Assistant</b> notification system.\n\n"
            f"✅ You will receive direct Telegram alerts for:\n"
            f"  • Task assignments\n"
            f"  • Meeting schedules\n"
            f"  • Project updates\n\n"
            f"📧 Full details will always be sent to your email first.\n"
            f"Telegram alerts are quick summaries — check your inbox for more.\n\n"
            f"— 🤖 PM Assistant"
        )
        self._send_message(chat_id, message)

    def _send_unmatched_message(self, chat_id: str, name: str) -> None:
        """Send a DM explaining that the user could not be auto-matched."""
        message = (
            f"👋 Hello <b>{name}</b>!\n\n"
            f"We couldn't automatically match you to an employee record.\n\n"
            f"Please contact your Project Manager and share your Telegram username "
            f"so they can link your account manually.\n\n"
            f"— 🤖 PM Assistant"
        )
        self._send_message(chat_id, message)

    def _send_message(self, chat_id: str, text: str) -> bool:
        """Send an HTML-formatted message to a specific chat_id. Returns True on success."""
        url = TELEGRAM_API.format(token=self._token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if resp.status_code == 200 and result.get("ok"):
                return True
            logger.error("❌ sendMessage failed: %s", result.get("description", resp.status_code))
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("❌ sendMessage exception: %s", exc)
            return False

