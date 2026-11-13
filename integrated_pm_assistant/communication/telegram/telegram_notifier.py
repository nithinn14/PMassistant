"""
telegram_notifier.py
----------------------
Sends Telegram messages via the Bot API.

Supports three sending modes:
  1. send(message)                  → PM fallback channel (uses pm_chat_id, backward-compatible)
  2. send_to_chat(chat_id, message) → Direct DM to any employee by chat_id
  3. broadcast(employees, message)  → Loop over all telegram-enabled employees

Personalized message builders:
  - send_task_assignment(...)   → Task assigned DM template
  - send_meeting_alert(...)     → Meeting scheduled DM template

Configuration:
    TelegramNotifier(bot_token=..., pm_chat_id=...)
    pm_chat_id is the original TELEGRAM_CHAT_ID (used for PM-level alerts like
    resource shortage). Employee direct messages use per-employee chat_ids
    stored in the employee database.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Single responsibility: send messages via the Telegram Bot API.

    Configuration is injected via the constructor.
    All methods are error-safe — failures are logged, never re-raised,
    so email delivery is never blocked by Telegram issues.
    """

    def __init__(self, bot_token: str, chat_id: str = "", pm_chat_id: str = ""):
        """
        Args:
            bot_token:   Telegram Bot API token (from BotFather / .env).
            chat_id:     Legacy single chat_id (kept for backward compatibility).
            pm_chat_id:  Chat ID for the PM / admin fallback alert channel.
                         If only chat_id is provided, pm_chat_id is set from it.
        """
        self._token = bot_token
        # Support both old (chat_id) and new (pm_chat_id) constructor styles
        self._pm_chat_id = pm_chat_id or chat_id

    # ── Primary send methods ──────────────────────────────────────────────────

    def send(self, message: str, name: str = "PM/System") -> bool:
        """
        Send to the PM fallback chat_id (backward-compatible with original API).
        Used for system-level alerts: resource shortage, meeting creation, etc.

        Returns True on success, False on failure.
        """
        if not self._pm_chat_id:
            logger.warning("⚠️ send() called but pm_chat_id is not configured. Skipping.")
            return False
        return self._post(self._pm_chat_id, message, name=name)

    def send_to_chat(self, chat_id: str, message: str, name: str = "Unknown") -> bool:
        """
        Send a direct Telegram message to any employee by their chat_id.

        Args:
            chat_id: The employee's Telegram chat_id (stored in employee DB).
            message: The message text (supports Telegram Markdown).
            name:    Optional employee name for logging/printing.

        Returns True on success, False on failure.
        """
        if not chat_id or str(chat_id).lower() in ("none", "nan", ""):
            logger.warning("⚠️ send_to_chat() called with empty/invalid chat_id. Skipping.")
            return False
        return self._post(str(chat_id), message, name=name)

    def broadcast(self, employees: List[Dict[str, Any]], message: str) -> Dict[str, Any]:
        """
        Send the same message to all Telegram-enabled employees.

        Args:
            employees: List of employee dicts, each must have 'telegram_chat_id' key.
                       (Use get_telegram_enabled_employees() to get this list.)
            message:   The broadcast message text.

        Returns:
            {"sent": [list of names], "failed": [list of names]}
        """
        result: Dict[str, list] = {"sent": [], "failed": []}

        for emp in employees:
            chat_id = emp.get("telegram_chat_id")
            name = emp.get("Employee_Name", "Unknown")
            if not chat_id or str(chat_id).lower() in ("none", "nan", ""):
                result["failed"].append(name)
                continue

            success = self._post(str(chat_id), message, name=name)
            if success:
                result["sent"].append(name)
            else:
                result["failed"].append(name)

        logger.info("📡 Broadcast complete — sent: %d, failed: %d", len(result["sent"]), len(result["failed"]))
        return result

    # ── Personalized message builders ────────────────────────────────────────

    def send_task_assignment(
        self,
        chat_id: str,
        employee_name: str,
        project_name: str,
        task_name: str,
        deadline: str,
        priority: str = "",
    ) -> bool:
        """
        Send a personalized task assignment notification directly to one employee.

        Returns True on success, False on failure.
        """
        priority_line = f"Priority: {self.escape_md(priority)}\n" if priority else ""
        message = (
            f"🤖 *PM Assistant*\n\n"
            f"Hello {self.escape_md(employee_name)},\n\n"
            f"You have been assigned a new task\\.\n\n"
            f"📁 Project: {self.escape_md(project_name)}\n"
            f"📋 Task: {self.escape_md(task_name)}\n"
            f"⏰ Deadline: {self.escape_md(deadline)}\n"
            f"{priority_line}"
            f"\n📧 Check your email for full details\\."
        )
        return self.send_to_chat(chat_id, message, name=employee_name)

    def send_meeting_alert(
        self,
        chat_id: str,
        employee_name: str,
        project_name: str,
        meeting_type: str,
        meeting_time: str,
    ) -> bool:
        """
        Send a personalized meeting scheduled alert to one employee.

        Returns True on success, False on failure.
        """
        message = (
            f"📅 *Meeting Scheduled*\n\n"
            f"Hello {self.escape_md(employee_name)},\n\n"
            f"A meeting has been scheduled for your project\\.\n\n"
            f"📁 Project: {self.escape_md(project_name)}\n"
            f"🗓 Type: {self.escape_md(meeting_type)}\n"
            f"🕐 Time: {self.escape_md(meeting_time)}\n\n"
            f"📧 Check your email for the full invitation and calendar invite\\."
        )
        return self.send_to_chat(chat_id, message, name=employee_name)

    def send_reschedule_proposal_request(self, chat_id: str, project_name: str, employee_name: str) -> bool:
        """
        Send a Telegram DM asking a declined participant to provide a new time.
        """
        message = (
            f"⚠️ *Action Required*\n\n"
            f"Hello {self.escape_md(employee_name)},\n\n"
            f"You declined the *{self.escape_md(project_name)}* meeting invitation\\.\n\n"
            f"Please reply to the invitation email with a *PROPOSED\\_TIME* and *REASON* so the system can reschedule\\.\n\n"
            f"— PM Assistant"
        )
        return self.send_to_chat(chat_id, message, name=employee_name)

    def send_consensus_confirmation_request(
        self, chat_id: str, employee_name: str, project_name: str, proposer_name: str, proposed_time: str
    ) -> bool:
        """
        Send a Telegram DM asking a participant to confirm a new proposed time.
        """
        message = (
            f"🤝 *Consensus Required*\n\n"
            f"Hello {self.escape_md(employee_name)},\n\n"
            f"A key participant ({self.escape_md(proposer_name)}) has suggested a new time for the *{self.escape_md(project_name)}* meeting:\n\n"
            f"📅 *{self.escape_md(proposed_time)}*\n\n"
            f"Are you okay with this time? Please reply to the system email with *AGREE* or *OK* to confirm\\.\n\n"
            f"— PM Assistant"
        )
        return self.send_to_chat(chat_id, message, name=employee_name)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _post(self, chat_id: str, message: str, name: str = "Unknown") -> bool:
        """
        POST a message to the Telegram Bot API.
        Errors are caught and logged — never raised.
        """
        url = _API_URL.format(token=self._token)
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "MarkdownV2",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if resp.status_code == 200 and result.get("ok"):
                logger.info("📲 Telegram sent → %s", chat_id)
                print(f"📲 Telegram message sent →  {chat_id}")
                return True
            logger.error("❌ Telegram error  (%s): %s", chat_id, result.get("description", resp.status_code))
            print(f"❌ Telegram error for {chat_id}: {result.get('description', resp.status_code)}")
            return False
        except requests.exceptions.Timeout:
            logger.warning("❌ Telegram send timed out  %s.", chat_id)
            print(f"❌ Telegram alert for {chat_id} timed out.")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("❌ Telegram send failed %s : %s", chat_id, exc)
            print(f"❌ Telegram for {chat_id} failed: {exc}")
            return False

    @staticmethod
    def escape_md(text: str) -> str:
        """Escape special Markdown characters for Telegram MarkdownV2 (used in builders)."""
        for char in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
            text = text.replace(char, f"\\{char}")
        return text
