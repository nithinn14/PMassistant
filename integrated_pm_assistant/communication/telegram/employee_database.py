"""
employee_database.py
---------------------
ADK-compatible tool module for employee Telegram registration management.

Provides:
  - get_employee_by_email()           → look up one employee record
  - get_employee_by_name()            → fuzzy name matching (for bot registration)
  - update_telegram_chat_id()         → save chat_id after /start
  - get_telegram_enabled_employees()  → all registered Telegram employees (for broadcast)

Each function is also wrapped as an ADK FunctionTool so agents can call it directly.

Integration:
    Used by TelegramRegistrationHandler (auto-registration) and
    NotificationAgent (per-employee DM lookup).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from google.adk.tools.function_tool import FunctionTool

from data_backend import get_backend

logger = logging.getLogger(__name__)

# ── Column name constants ────────────────────────────────────────────────────

COL_NAME = "Employee_Name"
COL_EMAIL = "Email"
COL_CHAT_ID = "telegram_chat_id"
COL_TG_ENABLED = "telegram_enabled"


# ── Internal helpers ─────────────────────────────────────────────────────────

def _load_employees() -> pd.DataFrame:
    """Load the employees DataFrame from the configured backend."""
    backend = get_backend()
    df = backend.read("employees")

    # Ensure Telegram columns exist (safe migration for existing sheets)
    if COL_CHAT_ID not in df.columns:
        df[COL_CHAT_ID] = None
    if COL_TG_ENABLED not in df.columns:
        df[COL_TG_ENABLED] = False

    # Force telegram_chat_id to string (object) dtype.
    # When the column is all-empty, pandas reads it as float64 from Excel,
    # causing errors when storing large chat_ids like 6032802179.
    df[COL_CHAT_ID] = df[COL_CHAT_ID].astype(object)

    return df


def _save_employees(df: pd.DataFrame) -> None:
    """Write the employees DataFrame back to the configured backend."""
    backend = get_backend()
    backend.write("employees", df)


# ── Public functions (also exposed as ADK FunctionTools) ─────────────────────

def get_employee_by_email(email: str) -> Dict[str, Any]:
    """
    Return the employee record matching `email` (case-insensitive).

    Returns:
        dict with employee fields, or {"error": "..."} if not found.
    """
    df = _load_employees()
    if COL_EMAIL not in df.columns:
        return {"error": "Email column not found in employees data"}

    match = df[df[COL_EMAIL].astype(str).str.lower() == email.lower()]
    if match.empty:
        return {"error": f"No employee found with email: {email}"}

    row = match.iloc[0].where(pd.notnull(match.iloc[0]), other=None)
    return row.to_dict()


def get_employee_by_name(name: str) -> Dict[str, Any]:
    """
    Return the first employee record whose name contains `name` (case-insensitive).

    Used by the Telegram registration handler to match a Telegram first_name or
    username to an employee record when no email is available.

    Returns:
        dict with employee fields, or {"error": "..."} if not found.
    """
    df = _load_employees()
    if COL_NAME not in df.columns:
        return {"error": "Employee_Name column not found in employees data"}

    match = df[df[COL_NAME].astype(str).str.lower().str.contains(name.lower(), na=False)]
    if match.empty:
        return {"error": f"No employee found matching name: {name}"}

    row = match.iloc[0].where(pd.notnull(match.iloc[0]), other=None)
    return row.to_dict()


def update_telegram_chat_id(
    email: str,
    chat_id: str,
    username: str = "",
) -> Dict[str, Any]:
    """
    Store a Telegram `chat_id` for the employee identified by `email`.

    Sets `telegram_enabled = True` automatically.
    If the employee is not found by email, tries a name-contains fallback
    using `username`.

    Returns:
        {"status": "ok", "employee": <name>} on success,
        {"error": "..."} on failure.
    """
    df = _load_employees()

    # Primary match: by email
    if COL_EMAIL in df.columns:
        mask = df[COL_EMAIL].astype(str).str.lower() == email.lower()
        if mask.any():
            df.loc[mask, COL_CHAT_ID] = str(chat_id)
            df.loc[mask, COL_TG_ENABLED] = True
            emp_name = df.loc[mask, COL_NAME].iloc[0] if COL_NAME in df.columns else email
            _save_employees(df)
            logger.info("✅ Telegram registered: %s → chat_id=%s", emp_name, chat_id)
            return {"status": "ok", "employee": str(emp_name), "chat_id": str(chat_id)}

    # Fallback: try matching by username or name
    if username and COL_NAME in df.columns:
        mask = df[COL_NAME].astype(str).str.lower().str.contains(username.lower(), na=False)
        if mask.any():
            df.loc[mask, COL_CHAT_ID] = str(chat_id)
            df.loc[mask, COL_TG_ENABLED] = True
            emp_name = df.loc[mask, COL_NAME].iloc[0]
            _save_employees(df)
            logger.info("✅ Telegram registered (by username): %s → chat_id=%s", emp_name, chat_id)
            return {"status": "ok", "employee": str(emp_name), "chat_id": str(chat_id)}

    logger.warning("⚠️ No employee matched for registration: email=%s, username=%s", email, username)
    return {"error": f"No employee found for email={email} or username={username}"}


def get_telegram_enabled_employees() -> List[Dict[str, Any]]:
    """
    Return all employee records where `telegram_enabled == True` and
    `telegram_chat_id` is not null/empty.

    Used by the broadcast notification flow.

    Returns:
        List of employee dicts (may be empty if no one has registered yet).
    """
    df = _load_employees()

    tg_df = df[
        (df[COL_TG_ENABLED].astype(str).str.lower().isin(["true", "1", "yes"]))
        & (df[COL_CHAT_ID].notna())
        & (df[COL_CHAT_ID].astype(str).str.strip() != "")
        & (df[COL_CHAT_ID].astype(str).str.lower() != "none")
    ]

    if tg_df.empty:
        logger.info("ℹ️ No Telegram-registered employees found.")
        return []

    result = []
    for _, row in tg_df.iterrows():
        clean = row.where(pd.notnull(row), other=None)
        result.append(clean.to_dict())

    return result


# ── ADK FunctionTool wrappers ────────────────────────────────────────────────

get_employee_by_email_tool = FunctionTool(get_employee_by_email)
get_employee_by_name_tool = FunctionTool(get_employee_by_name)
update_telegram_chat_id_tool = FunctionTool(update_telegram_chat_id)
get_telegram_enabled_employees_tool = FunctionTool(get_telegram_enabled_employees)
