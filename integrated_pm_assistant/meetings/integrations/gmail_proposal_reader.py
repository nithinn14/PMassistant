from __future__ import annotations

import imaplib
import email
from email.message import Message
from email.utils import parseaddr
from typing import Optional, Tuple, Dict

from paths import ENV_PATH

from dotenv import load_dotenv
import os


load_dotenv(ENV_PATH)

EMAIL_ADDRESS = os.getenv("SENDER_EMAIL")
EMAIL_PASSWORD = os.getenv("SENDER_PASSWORD")


def read_project_proposals(project_name: str) -> Dict[str, Dict[str, str]]:
    """
    Returns a dict: { "sender_email": { "RESPONSE": ..., "PROPOSED_TIME": ..., "REASON": ... } }
    """

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("❌ Email credentials missing.")
        return {}

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")

        status, data = mail.search(None, "ALL")

        if status != "OK" or not data or not data[0]:
            return {}

        email_ids = data[0].split()

        proposals = {}

        # Check last 15 emails only
        for email_id in reversed(email_ids[-15:]):

            status, msg_data = mail.fetch(email_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            if not isinstance(raw_email, bytes):
                continue

            msg: Message = email.message_from_bytes(raw_email)

            subject = msg.get("Subject", "").lower()

            if project_name.lower() not in subject:
                continue
            
            sender = parseaddr(msg.get("From", ""))[1].strip().lower()

            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body = payload.decode(errors="ignore")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(errors="ignore")

            if not body:
                continue

            body = body.strip()

            response = None
            proposed_time = None
            reason = None

            # 🔹 CHECK 1: Structured reply (Our custom template)
            if any(k in body.upper() for k in ["REASON:", "PROPOSED_TIME:", "DECLINE", "AGREE", "OK", "YES", "NO"]):
                for line in body.splitlines():
                    clean = line.strip()
                    uc = clean.upper()
                    if uc == "ACCEPT": response = "ACCEPT"
                    elif uc == "DECLINE": response = "DECLINE"
                    elif uc in ["AGREE", "OK", "YES"]: response = "AGREE"
                    elif uc == "NO": response = "DISAGREE"
                    elif uc.startswith("PROPOSED_TIME:"):
                        proposed_time = clean[len("PROPOSED_TIME:"):].strip()
                    elif uc.startswith("REASON:"):
                        reason = clean[len("REASON:"):].strip()

            # 🔹 CHECK 1b: Looser Parsing for unstructured replies
            if not response and ("re:" in subject or "reply" in subject):
                # If they just replied with text, treat it as a Decline with Reason
                response = "DECLINE"
                # Take first 3 lines of body as Reason, avoiding signatures/old threads
                reason_lines = []
                for line in body.splitlines():
                    l = line.strip()
                    if not l or l.startswith(">") or l.lower().startswith("from:") or l.lower().startswith("on "):
                        break
                    reason_lines.append(l)
                reason = " ".join(reason_lines).strip()
            
            # 🔹 CHECK 2: Native Google Calendar Proposal
            elif "proposed new time" in subject or "new time proposed" in subject:
                response = "DECLINE"
                lines = [L.strip() for L in body.splitlines()]
                for i, line in enumerate(lines):
                    if "proposed a new time:" in line.lower():
                        if i + 1 < len(lines):
                            proposed_time = lines[i+1].strip()
                        if i + 2 < len(lines) and lines[i+2]:
                            possible_reason = lines[i+2]
                            if possible_reason.startswith('"') and possible_reason.endswith('"'):
                                reason = possible_reason.strip('"')
                            elif not project_name.lower() in possible_reason.lower() and "kickoff" not in possible_reason.lower() and "fake news" not in possible_reason.lower():
                                reason = possible_reason
                        break
                # Fallbacks in case format differs
                if not proposed_time:
                    for line in lines:
                        if "proposed time:" in line.lower():
                            proposed_time = line.lower().split("proposed time:", 1)[1].strip()
                            break

            if response:
                if sender not in proposals:
                    proposals[sender] = {
                        "RESPONSE": response,
                        "PROPOSED_TIME": proposed_time,
                        "REASON": reason
                    }
        return proposals

    except Exception as e:
        print("❌ Gmail read error:", e)
        return {}