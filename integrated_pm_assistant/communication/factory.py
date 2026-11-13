"""
CommunicationFactory
---------------------
Reads config.yaml once and wires together all communication objects
via dependency injection. To change SMTP, Telegram, or registration: update config.yaml only.

Object graph built here:
    EmailNotifier ──┐
                    ├─► NotificationAgent
    TelegramNotifier┘

    TelegramRegistrationHandler  ← standalone, used for onboarding
"""

import os

from config_loader import load_config
from communication.email.email_notifier import EmailNotifier
from communication.telegram.telegram_notifier import TelegramNotifier
from communication.telegram.telegram_registration_handler import TelegramRegistrationHandler


def build_email_notifier(config: dict) -> EmailNotifier:
    smtp = config["smtp"]
    return EmailNotifier(
        sender_email=os.environ[smtp["email_env"]],
        sender_password=os.environ[smtp["password_env"]],
        smtp_host=smtp["host"],
        smtp_port=smtp["port"],
    )


def build_telegram_notifier(config: dict) -> TelegramNotifier:
    """
    Build TelegramNotifier.

    pm_chat_id is the PM fallback alert channel (original TELEGRAM_CHAT_ID env var).
    Per-employee chat_ids are stored in the employee database — not needed here.
    """
    tg = config["telegram"]
    return TelegramNotifier(
        bot_token=os.environ[tg["bot_token_env"]],
        # pm_chat_id: used for system-level alerts (resource shortage, meeting creation)
        pm_chat_id=os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), ""),
    )


def build_registration_handler(config: dict) -> TelegramRegistrationHandler:
    """
    Build TelegramRegistrationHandler wired with the PM's chat_id for admin notifications.

    The handler polls for /start messages and stores chat_ids in the employee database.
    Typically run in a daemon thread during system startup, or triggered as a one-shot
    after onboarding emails are sent.
    """
    tg = config["telegram"]
    bot_token = os.environ[tg["bot_token_env"]]
    pm_chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")

    return TelegramRegistrationHandler(
        bot_token=bot_token,
        pm_chat_id=pm_chat_id or None,
    )


def build_notification_agent(config: dict):
    from agents.communication.notification_agent import NotificationAgent

    return NotificationAgent(
        email_notifier=build_email_notifier(config),
        telegram_notifier=build_telegram_notifier(config),
    )


def build_communication_components():
    """Entry point: returns NotificationAgent."""
    config = load_config()
    return build_notification_agent(config)
