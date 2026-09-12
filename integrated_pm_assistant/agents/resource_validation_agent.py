"""
Resource Validator Agent (ADK-Native)
--------------------------------------
Validates resource assignments and controls the LoopAgent flow.

Key improvements:
  • Shortage detection includes task_status == "waiting_resource"
  • Resolution polling loop ALSO watches employees.xlsx mtime — when the
    file changes (PM adds a new employee, directly or via Telegram bot),
    the agent automatically breaks the wait and lets the LoopAgent re-run
    ResourceAgent for incremental assignment. No manual restart needed.
"""

import json
import asyncio
import os
import re
from pathlib import Path
from typing import AsyncGenerator, Optional

import pandas as pd
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from google.adk.events.event import Event, EventActions

from tools.resource_validator import (
    validate_resources,
    save_diagnostic_report,
    update_workflow_state,
    check_workflow_resolution,
)
from agents.communication.factory import build_notification_agent
from agents.communication.notifiers.email_notifier import is_valid_email
from data_backend import get_backend
from config_loader import load_config
from telegram_bot import run_telegram_listener


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_poll_interval() -> int:
    return load_config()["workflow"]["poll_interval_seconds"]


def _get_pm_email() -> Optional[str]:
    """
    Safely retrieve the Project Manager's email address from employees data.
    Returns a stripped email string if valid and present, otherwise None.
    """
    try:
        df = get_backend().read("employees")
        if df.empty or "Role" not in df.columns or "Email" not in df.columns:
            return None
        pm_rows = df[df["Role"].str.contains("Project Manager", case=False, na=False)]
        if pm_rows.empty:
            return None
        val = pm_rows.iloc[0]["Email"]
        if pd.isna(val) or val is None:
            return None
        email_str = str(val).strip()
        if not email_str or email_str.lower() in {"nan", "none", "null"}:
            return None
        return email_str
    except Exception as exc:
        print(f"⚠️ Could not retrieve PM email: {exc}")
        return None


def _get_employees_path() -> Path:
    """Resolve the absolute path to employees.xlsx used by the backend."""
    config = load_config()
    root_dir = config.get("data_backend", {}).get("excel", {}).get("root_dir", ".")
    return Path(root_dir) / "employees.xlsx"


def _has_shortage(tasks: list) -> bool:
    """
    A task is considered a resource shortage when:
      • Assigned_Employee == "No Resource Available", OR
      • task_status == "waiting_resource"
    """
    for t in tasks:
        emp = str(t.get("Assigned_Employee", "")).strip().lower()
        status = str(t.get("task_status", "")).strip().lower()
        if emp == "no resource available" or status == "waiting_resource":
            return True
    return False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class ResourceValidationAgent(BaseAgent):
    """
    ADK BaseAgent that validates resource assignments and controls LoopAgent flow.

    Flow:
      • No shortage   → escalate=True  (LoopAgent exits)
      • Shortage      → notify PM via Email + Telegram
                        enter dual-watch async loop:
                          – watches workflow_state.yaml for manual resolution
                          – watches employees.xlsx mtime for auto-trigger
                        On either trigger → escalate=False (LoopAgent re-runs
                        ResourceAgent, which does incremental assignment only)
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        input_text = self._extract_input(ctx)

        match = re.search(r"\{.*\}", input_text, re.DOTALL)
        json_str = match.group(0) if match else input_text
        
        # Robust parsing: handle literal NaN which is invalid in non-Python JSON parsers
        json_str = re.sub(r"\bNaN\b", "null", json_str)
        data = json.loads(json_str)

        project_name = data["project_name"]
        tasks = data.get("tasks", [])

        # ── Evaluate current shortage state ────────────────────────────────
        is_blocked = data.get("resource_blocked", False) or _has_shortage(tasks)

        if not is_blocked:
            print(f"✅ All resources assigned for '{project_name}'")
            yield self._event(ctx, data, escalate=True)
            return

        # ── Shortage detected ──────────────────────────────────────────────
        print(f"\n🚨 Resource shortage for '{project_name}' — entering resolution flow.\n")

        validation = validate_resources(project_name, tasks)
        missing_roles = validation["missing_roles"]
        shortages = validation["shortages"]

        pm_email = _get_pm_email()
        notification_issues = []
        if not is_valid_email(pm_email):
            notification_issues.append({
                "recipient": "Project Manager",
                "role": "Project Manager",
                "email": pm_email if pm_email is not None else None,
                "reason": f"Missing or invalid email address ('{pm_email}')",
                "channel": "email",
                "action_impacted": "resource_shortage_alert",
            })
            print(
                f"⚠️ [Notification] PM email is missing or invalid ('{pm_email}'). "
                f"Skipping email alert and recording issue in diagnostic report."
            )

        if notification_issues:
            validation["notification_issues"] = notification_issues

        diagnostic_path = save_diagnostic_report(project_name, validation)
        # Important: always reset resolution to PENDING when entering a new
        # shortage cycle, so stale state from a previous run is overwritten.
        workflow_path = update_workflow_state(
            project_name=project_name,
            status="RESOURCE_BLOCKED",
            missing_roles=missing_roles,
            diagnostic_path=diagnostic_path,
            resolution="PENDING",
            notification_issues=notification_issues if notification_issues else None,
        )

        # Send Email + Telegram alert (with bot command hints)
        notifier = build_notification_agent(load_config())
        notifier.notify_resource_shortage(
            pm_email=pm_email,
            project_name=project_name,
            missing_roles=missing_roles,
            impacted_tasks=shortages,
            diagnostic_file_path=diagnostic_path,
            workflow_state_path=workflow_path,
        )

        # ── Dual-watch resolution loop ─────────────────────────────────────
        poll_interval = _get_poll_interval()
        employees_path = _get_employees_path()

        # Record the current mtime of employees.xlsx as our baseline
        try:
            last_employees_mtime = os.stat(employees_path).st_mtime
        except FileNotFoundError:
            last_employees_mtime = None

        print(
            f"Watching for resolution every {poll_interval}s ...\n"
            f"  - workflow_state.yaml: manual PM resolution\n"
            f"  - {employees_path.name}: auto-trigger on employee added\n"
            f"  - Telegram bot: listening for /add_employee, /approve_external_hiring, /rebalance_tasks"
        )

        # Start the Telegram bot as a background asyncio task so the PM can
        # respond with commands without needing a separate terminal.
        # The task is cancelled before we yield any event back to the ADK.
        bot_task = asyncio.create_task(
            run_telegram_listener(project_name),
            name=f"telegram_bot_{project_name}",
        )

        # These will be set inside the loop to decide what to yield
        should_escalate = False
        resolved = False

        while True:
            await asyncio.sleep(poll_interval)

            # Check 1: Manual resolution via workflow_state.yaml
            state = check_workflow_resolution(project_name)
            res = state.get("resolution", "PENDING")

            if res not in ("PENDING", None):
                print(f"Resolution received: {res}")

                if res == "APPROVE_EXTERNAL_HIRING":
                    update_workflow_state(
                        project_name=project_name,
                        status="COMPLETED",
                        missing_roles=missing_roles,
                        diagnostic_path=diagnostic_path,
                        resolution=res,
                    )
                    should_escalate = True
                else:
                    # Retry (ADD_RESOURCE, REBALANCE, etc.)
                    # Reset resolution to PENDING so we don't loop on same signal
                    update_workflow_state(
                        project_name=project_name,
                        status="RETRYING",
                        missing_roles=missing_roles,
                        diagnostic_path=diagnostic_path,
                        resolution="PENDING",
                    )
                    should_escalate = False

                resolved = True
                break

            # Check 2: employees.xlsx mtime changed -> auto-trigger
            try:
                current_mtime = os.stat(employees_path).st_mtime
            except FileNotFoundError:
                current_mtime = last_employees_mtime

            if last_employees_mtime is not None and current_mtime != last_employees_mtime:
                print(
                    f"'{employees_path.name}' changed -- auto-triggering "
                    f"incremental assignment for '{project_name}'"
                )
                update_workflow_state(
                    project_name=project_name,
                    status="RETRYING",
                    missing_roles=missing_roles,
                    diagnostic_path=diagnostic_path,
                    resolution="PENDING",
                )
                should_escalate = False
                resolved = True
                break

            last_employees_mtime = current_mtime

        # Cancel the Telegram bot BEFORE yielding (avoids async generator crash)
        if not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass

        yield self._event(ctx, data, escalate=should_escalate)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _extract_input(self, ctx) -> str:
        for event in reversed(ctx._get_events(current_invocation=True)):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        return part.text
        return "{}"

    def _event(self, ctx, data: dict, escalate: bool) -> Event:
        event_kwargs = dict(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(parts=[types.Part(text=json.dumps(data))]),
            branch=ctx.branch,
        )
        if escalate:
            event_kwargs["actions"] = EventActions(escalate=True)
        return Event(**event_kwargs)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_resource_validation_agent():
    return ResourceValidationAgent(
        name="resource_validation_agent",
        description=(
            "Validates resource assignments. "
            "On shortage: notifies PM and watches for resolution "
            "(workflow_state.yaml or employees.xlsx change)."
        ),
    )
