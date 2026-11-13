import smtplib
from email.message import EmailMessage


def is_valid_email(email: object) -> bool:
    """
    Validate that an email address is plausible before attempting to send.
    Must not be empty, None, literal 'nan'/'none'/'null', and must contain
    an '@' and at least one '.' after it.
    """
    if email is None:
        return False
    email_str = str(email).strip()
    if not email_str or email_str.lower() in {"nan", "none", "null"}:
        return False
    if "@" not in email_str:
        return False
    local, _, domain = email_str.partition("@")
    if not local or not domain or "." not in domain:
        return False
    domain_parts = domain.split(".")
    if any(not part for part in domain_parts):
        return False
    return True


class EmailNotifier:
    """
    Single responsibility: send emails via SMTP.
    All configuration injected via constructor — swap providers by changing config.yaml.
    """

    def __init__(
        self,
        sender_email: str,
        sender_password: str,
        smtp_host: str,
        smtp_port: int,
    ):
        self._sender = sender_email
        self._password = sender_password
        self._host = smtp_host
        self._port = smtp_port

    def send(self, to_email: str, subject: str, message: str) -> bool:
        """Send a plain-text email if to_email is valid."""
        if not is_valid_email(to_email):
            print(f"⚠️ [EmailNotifier] Invalid recipient email address: '{to_email}'. Skipping SMTP send.")
            return False

        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(message)

        try:
            with smtplib.SMTP_SSL(self._host, self._port) as server:
                server.login(self._sender, self._password)
                server.send_message(msg)
            print(f"📧 Email sent → {to_email}")
            return True
        except Exception as exc:
            print(f"❌ [EmailNotifier] SMTP send failed for {to_email}: {exc}")
            return False

    def send_telegram_registration_invite(
        self,
        to_email: str,
        employee_name: str,
        bot_username: str,
    ) -> None:
        """
        Send an onboarding email inviting a new employee to register with the Telegram bot.

        The email contains the bot link (t.me/<bot_username>) and instructions to press Start.
        Once the employee presses Start, TelegramRegistrationHandler auto-captures their chat_id.

        Args:
            to_email:      Employee's email address.
            employee_name: Employee's display name (used in greeting).
            bot_username:  The bot's Telegram username, without the @ sign.
                           Configured in config.yaml under telegram.bot_username.
        """
        bot_link = f"https://t.me/{bot_username}"
        self.send(
            to_email=to_email,
            subject="Action Required: Set Up Your PM Assistant Telegram Notifications",
            message=(
                f"Hello {employee_name},\n\n"
                f"Welcome to the team! Our project management system sends important "
                f"notifications (task assignments, meeting schedules, project updates) "
                f"directly to your email.\n\n"
                f"To also receive quick Telegram alerts, please take 30 seconds to link "
                f"your Telegram account:\n\n"
                f"  1. Click this link: {bot_link}\n"
                f"  2. Press the 'Start' button in Telegram\n\n"
                f"That's it — the system will automatically link your account and you'll "
                f"start receiving Telegram alerts for tasks assigned to you.\n\n"
                f"If you don't use Telegram, you can ignore this email — all notifications "
                f"will continue to be sent to your email address.\n\n"
                f"— PM Assistant\n"
            ),
        )
        print(f"📧 Telegram registration invite sent → {to_email}")

    def send_reschedule_proposal_request(self, to_email: str, project_name: str) -> None:
        """
        Send a structured reschedule proposal template to a declined participant.
        Asks them to reply with their reason and a proposed alternative time.
        """
        self.send(
            to_email=to_email,
            subject=f"Action Required: Please Propose a New Meeting Time | {project_name}",
            message=(
                f"Hello,\n\n"
                f"You declined the {project_name} meeting invitation.\n\n"
                f"Please reply to this email with:\n"
                f"  RESPONSE: DECLINE\n"
                f"  REASON: <your reason>\n"
                f"  PROPOSED_TIME: <YYYY-MM-DD HH:MM>\n\n"
                f"The system will automatically reschedule once a valid proposal is received.\n\n"
                f"— PM Assistant"
            ),
        )
    def send_consensus_confirmation_request(
        self, to_email: str, project_name: str, proposer_name: str, proposed_time: str
    ) -> None:
        """
        Send an email asking a participant to confirm a new proposed time.
        """
        self.send(
            to_email=to_email,
            subject=f"Consensus Required: New Meeting Time Proposed | {project_name}",
            message=(
                f"Hello,\n\n"
                f"A key participant ({proposer_name}) has suggested a new meeting time for {project_name}:\n\n"
                f"PROPOSED TIME: {proposed_time}\n\n"
                f"Are you okay with this time? Please reply to this email with 'AGREE', 'OK', or 'YES' to confirm.\n\n"
                f"The meeting will be automatically rescheduled once all required participants agree.\n\n"
                f"— PM Assistant"
            ),
        )
