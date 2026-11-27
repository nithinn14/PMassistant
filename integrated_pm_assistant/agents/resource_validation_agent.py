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
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional
from pydantic import Field

import pandas as pd
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from google.adk.events.event import Event, EventActions

from tools.resources.resource_validator import (
    validate_resources,
    save_diagnostic_report,
    update_workflow_state,
    conditional_update_workflow_state,
    check_workflow_resolution,
    PMResponseTimeoutError,
)
from communication.factory import build_notification_agent
from communication.email.email_notifier import is_valid_email
from data_backend import get_backend
from config_loader import load_config
from communication.telegram.telegram_bot import run_telegram_listener


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_poll_interval() -> float:
    try:
        val = float(load_config().get("workflow", {}).get("poll_interval_seconds", 30))
        return val if math.isfinite(val) and val > 0 else 30.0
    except Exception:
        return 30.0


def _parse_non_negative_float(value, default: float) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _parse_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= 1 else default
    except (TypeError, ValueError):
        return default


def _get_timeout_settings() -> dict:
    try:
        wf = load_config().get("workflow", {}) or {}
        if not isinstance(wf, dict):
            wf = {}
    except Exception:
        wf = {}

    return {
        "initial_wait_seconds": _parse_non_negative_float(
            wf.get("pm_initial_wait_seconds"), 86400.0
        ),
        "reminder_wait_seconds": _parse_non_negative_float(
            wf.get("pm_reminder_wait_seconds"), 86400.0
        ),
        "reminder_max_attempts": _parse_positive_int(
            wf.get("reminder_max_attempts"), 3
        ),
        "reminder_retry_delay_seconds": _parse_non_negative_float(
            wf.get("reminder_retry_delay_seconds"), 5.0
        ),
    }


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

    Approved external-hire tasks are excluded.
    """
    for t in tasks:
        status = str(t.get("task_status", "")).strip().lower()
        emp = str(t.get("Assigned_Employee", "")).strip().lower()

        if status == "needs_external_hire" or emp == "needs_external_hire":
            continue

        if emp == "no resource available" or status == "waiting_resource":
            return True

    return False

def _mark_external_hire_tasks(tasks: list, shortages: list) -> None:
    """Mark only reliably identified shortage tasks for external hiring."""
    shortage_ids = {
        str(item.get("task_id")).strip()
        for item in shortages
        if item.get("task_id") is not None
        and str(item.get("task_id")).strip().lower() not in ("", "n/a", "none")
    }

    for task in tasks:
        task_id = str(task.get("task_id", "")).strip()

        if task_id and task_id in shortage_ids:
            task["task_status"] = "NEEDS_EXTERNAL_HIRE"


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

    integrations: dict = Field(default_factory=lambda: {
        "google_calendar": True,
        "gmail": True,
        "telegram": True,
        "gantt": True,
    })

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
        if self.integrations.get("gmail", True) and not is_valid_email(pm_email):
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
        run_id = str(uuid.uuid4())
        # Important: always reset resolution to PENDING and assign a fresh run_id
        # when entering a new shortage cycle, so stale state from a previous run is overwritten.
        workflow_path = update_workflow_state(
            project_name=project_name,
            status="RESOURCE_BLOCKED",
            missing_roles=missing_roles,
            diagnostic_path=diagnostic_path,
            resolution="PENDING",
            notification_issues=notification_issues if notification_issues else None,
            run_id=run_id,
        )

        # Send Email + Telegram alert (with bot command hints)
        notifier = build_notification_agent(load_config(), integrations=self.integrations)
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
        bot_task = None
        if self.integrations.get("telegram", True):
            bot_task = asyncio.create_task(
                run_telegram_listener(project_name, run_id=run_id),
                name=f"telegram_bot_{project_name}",
            )

        # These will be set inside the loop to decide what to yield
        should_escalate = False
        stale_run_detected = False
        timed_out = False

        timeout_cfg = _get_timeout_settings()
        stage = 1
        deadline = time.monotonic() + timeout_cfg["initial_wait_seconds"]

        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(poll_interval, remaining))

            # Check 1: Manual resolution via workflow_state.yaml
            state = check_workflow_resolution(project_name)
            state_run_id = str(state.get("run_id") or "").strip()
            if state_run_id != run_id:
                print(
                    f"⚠️ Stale ResourceValidationAgent invocation for '{project_name}' "
                    f"(state run_id={state_run_id!r}, expected={run_id!r}) — stopping wait cycle."
                )
                stale_run_detected = True
                break

            res = state.get("resolution", "PENDING")

            if res not in ("PENDING", None):
                print(f"Resolution received: {res}")

                if res == "APPROVE_EXTERNAL_HIRING":
                    if not conditional_update_workflow_state(
                        project_name=project_name,
                        expected_run_id=run_id,
                        status="COMPLETED",
                        missing_roles=missing_roles,
                        diagnostic_path=diagnostic_path,
                        resolution=res,
                        expected_status="RESOURCE_BLOCKED",
                        expected_resolution="APPROVE_EXTERNAL_HIRING",
                    ):
                        stale_run_detected = True
                        break
                    _mark_external_hire_tasks(tasks, shortages)
                    should_escalate = True

                else:
                    # Retry (ADD_RESOURCE, REBALANCE, etc.)
                    # Reset resolution to PENDING so we don't loop on same signal
                    if not conditional_update_workflow_state(
                        project_name=project_name,
                        expected_run_id=run_id,
                        status="RETRYING",
                        missing_roles=missing_roles,
                        diagnostic_path=diagnostic_path,
                        resolution="PENDING",
                        expected_status="RESOURCE_BLOCKED",
                        expected_resolution=res,
                    ):
                        stale_run_detected = True
                        break
                    should_escalate = False

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
                if not conditional_update_workflow_state(
                    project_name=project_name,
                    expected_run_id=run_id,
                    status="RETRYING",
                    missing_roles=missing_roles,
                    diagnostic_path=diagnostic_path,
                    resolution="PENDING",
                    expected_status="RESOURCE_BLOCKED",
                    expected_resolution="PENDING",
                ):
                    stale_run_detected = True
                    break
                should_escalate = False
                break

            last_employees_mtime = current_mtime

            # Check 3: Monotonic deadline expiration (two-stage wait)
            if time.monotonic() >= deadline:
                if stage == 1:
                    await self._send_reminder_with_retries(
                        project_name=project_name,
                        pm_email=pm_email,
                        missing_roles=missing_roles,
                        run_id=run_id,
                        max_attempts=timeout_cfg["reminder_max_attempts"],
                        retry_delay=timeout_cfg["reminder_retry_delay_seconds"],
                    )
                    stage = 2
                    deadline = time.monotonic() + timeout_cfg["reminder_wait_seconds"]
                else:
                    if not conditional_update_workflow_state(
                        project_name=project_name,
                        expected_run_id=run_id,
                        status="RESOURCE_BLOCKED",
                        missing_roles=missing_roles,
                        diagnostic_path=diagnostic_path,
                        resolution="PM_RESPONSE_TIMEOUT",
                        expected_status="RESOURCE_BLOCKED",
                        expected_resolution="PENDING",
                    ):
                        stale_run_detected = True
                        break
                    timed_out = True
                    break

        # Cancel the Telegram bot BEFORE yielding (avoids async generator crash)
        if bot_task is not None and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass

        if stale_run_detected:
            raise RuntimeError(
                f"ResourceValidationAgent invocation for '{project_name}' was superseded by a newer run."
            )

        if timed_out:
            raise PMResponseTimeoutError(project_name=project_name, run_id=run_id)

        yield self._event(ctx, data, escalate=should_escalate)

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _send_reminder_with_retries(
        self,
        project_name: str,
        pm_email: Optional[str],
        missing_roles: dict,
        run_id: str,
        max_attempts: int,
        retry_delay: float,
    ) -> bool:
        if not self.integrations.get("gmail", True):
            print(
                f"⚠️ [ResourceValidation] Gmail disabled; skipping reminder email for '{project_name}'."
            )
            return False

        if not is_valid_email(pm_email):
            print(
                f"⚠️ [ResourceValidation] PM email missing/invalid ('{pm_email}'); "
                f"skipping reminder email for '{project_name}'."
            )
            return False

        for attempt in range(1, max_attempts + 1):
            try:
                notifier = build_notification_agent(
                    load_config(), integrations=self.integrations
                )
                sent = notifier.notify_resource_shortage_reminder(
                    pm_email=pm_email,
                    project_name=project_name,
                    missing_roles=missing_roles,
                    run_id=run_id,
                )
                if sent:
                    print(
                        f"📧 Reminder email sent for '{project_name}' on attempt {attempt}/{max_attempts}."
                    )
                    return True
                print(
                    f"⚠️ Reminder email attempt {attempt}/{max_attempts} returned False for '{project_name}'."
                )
            except Exception as exc:
                print(
                    f"⚠️ Reminder email attempt {attempt}/{max_attempts} failed for '{project_name}': {exc}"
                )

            if attempt < max_attempts and retry_delay > 0:
                await asyncio.sleep(retry_delay)

        print(
            f"❌ All {max_attempts} reminder email attempts failed for '{project_name}'; "
            "continuing to second wait period."
        )
        return False

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

def build_resource_validation_agent(integrations=None):
    return ResourceValidationAgent(
        name="resource_validation_agent",
        description=(
            "Validates resource assignments. "
            "On shortage: notifies PM and watches for resolution "
            "(workflow_state.yaml or employees.xlsx change)."
        ),
        integrations=integrations if integrations is not None else {
            "google_calendar": True,
            "gmail": True,
            "telegram": True,
            "gantt": True,
        },
    )
