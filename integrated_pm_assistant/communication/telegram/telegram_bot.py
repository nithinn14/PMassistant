"""
telegram_bot.py
----------------
Telegram command bot for the PM Assistant.

PRIMARY MODE - Embedded inside ResourceValidationAgent (ADK-native):
  When a resource shortage is detected, ResourceValidationAgent automatically
  starts the bot as an asyncio background task using:

      asyncio.create_task(run_telegram_listener(project_name))

  The bot stops automatically when the shortage is resolved.
  No second terminal needed -- just run:  python main.py

OPTIONAL standalone mode (kept for testing / manual use):
      python communication/telegram/telegram_bot.py

Supported commands:
  /add_employee          Interactive: Name -> Role -> Hours -> Email -> appends to employees.xlsx
                         Writing to employees.xlsx triggers the mtime-watcher in
                         ResourceValidationAgent, which fires incremental assignment.

  /approve_external_hiring <run_id>
                         Approves external hiring for the specified pipeline run.
                         Requires the current run ID; stale or mismatched approvals
                         are rejected.

  /rebalance_tasks       Sets resolution="REBALANCE" in workflow_state.yaml, which
                         causes the ADK LoopAgent to re-run incremental assignment.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Bootstrap: allow running from repo root
_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))


from paths import APP_ROOT, resolve_path

import requests



from data_backend import get_backend  # noqa: E402
from config_loader import load_config  # noqa: E402
from tools.resources.resource_validator import (  # noqa: E402
    _WORKFLOW_STATE_LOCK,
    _atomic_write_yaml,
)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_telegram_config() -> tuple[str, str]:
    """Return (bot_token, chat_id) from environment variables."""
    config = load_config()
    tg = config.get("telegram", {})
    token = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if not token or not chat_id:
        raise EnvironmentError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables."
        )
    return token, chat_id


def _output_dir() -> Path:
    config = load_config()
    excel_cfg = config.get("data_backend", {}).get("excel", {})

    root = resolve_path(excel_cfg.get("root_dir", APP_ROOT), APP_ROOT)
    path = resolve_path(excel_cfg.get("output_dir", "output"), root)

    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

class TelegramBot:
    """Minimal Telegram Bot using long-polling."""

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str):
        self._token = token
        self._chat_id = chat_id
        self._offset = 0

    def _call(self, method: str, payload: dict = None, timeout: int = 30) -> dict:
        url = self._API.format(token=self._token, method=method)
        try:
            resp = requests.post(url, json=payload or {}, timeout=timeout)
            return resp.json()
        except Exception as exc:
            print(f"Telegram API error ({method}): {exc}")
            return {"ok": False}

    def send(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
        result = self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )
        return result.get("ok", False)

    def reply(self, text: str) -> bool:
        """Send to the configured PM chat."""
        return self.send(self._chat_id, text)

    def get_updates(self) -> list:
        payload = {
            "offset": self._offset,
            "timeout": 20,
            "allowed_updates": ["message"],
        }
        result = self._call("getUpdates", payload, timeout=25)
        updates = result.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates


# ---------------------------------------------------------------------------
# Conversation state machine (for multi-step /add_employee)
# ---------------------------------------------------------------------------

# State keys: "step", "name", "role", "hours"
_conversations: dict[str, dict] = {}  # keyed by chat_id


def _get_state(chat_id: str) -> dict:
    return _conversations.setdefault(chat_id, {})


def _clear_state(chat_id: str) -> None:
    _conversations.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_add_employee(bot: TelegramBot, chat_id: str, text: str) -> None:
    """
    Multi-step conversation to collect employee details.
    State machine: idle -> await_name -> await_role -> await_hours -> await_email -> done
    """
    state = _get_state(chat_id)
    step = state.get("step")

    if text.strip() == "/add_employee" or step is None:
        _conversations[chat_id] = {"step": "await_name"}
        bot.send(chat_id, "Add New Employee\n\nPlease enter the employee's full name:")
        return

    if step == "await_name":
        state["name"] = text.strip()
        state["step"] = "await_role"
        bot.send(chat_id, f"Name: {state['name']}\n\nNow enter the employee's role\n(e.g. Backend Developer, QA Engineer):")
        return

    if step == "await_role":
        state["role"] = text.strip()
        state["step"] = "await_hours"
        bot.send(chat_id, f"Role: {state['role']}\n\nNow enter available hours (number):")
        return

    if step == "await_hours":
        try:
            hours = float(text.strip())
        except ValueError:
            bot.send(chat_id, "Please enter a valid number for available hours.")
            return

        state["hours"] = hours
        state["step"] = "await_email"
        bot.send(chat_id, f"Available Hours: {hours}\n\nNow enter the employee's email (or type 'skip'):")
        return

    if step == "await_email":
        email_input = text.strip()
        email = "" if email_input.lower() == "skip" else email_input

        name = state["name"]
        role = state["role"]
        hours = state["hours"]
        
        # Write new employee to Excel via shared data backend
        import pandas as pd
        backend = get_backend()
        employees_df = backend.read("employees")

        # Handle column name variations (some files use "Allocated_Hour" without 's')
        allocated_col = "Allocated_Hours" if "Allocated_Hours" in employees_df.columns else "Allocated_Hour"

        new_row = pd.DataFrame([{
            "Employee_Name": name,
            "Role": role,
            "Free_Hours": hours,
            allocated_col: 0,
            "Current_Project": "",
            "Email": email,
        }])

        # Append -- triggers mtime change on employees.xlsx -> auto-reassignment
        backend.write("employees", pd.concat([employees_df, new_row], ignore_index=True))

        _clear_state(chat_id)
        bot.send(
            chat_id,
            f"Employee Added Successfully\n\n"
            f"Name: {name}\n"
            f"Role: {role}\n"
            f"Available Hours: {hours}\n"
            f"Email: {email or 'Not provided'}\n\n"
            f"The system will automatically reassign waiting tasks."
        )
        print(f"Added employee '{name}' ({role}, {hours}h, email: {email or 'none'}) -> employees.xlsx updated")
        return

    # Unknown step - reset
    _clear_state(chat_id)
    bot.send(chat_id, "Conversation reset. Type /add_employee to start again.")


def _conditional_update_approval_resolution(
    outdir: Path,
    project_name: str,
    supplied_run_id: str,
    expected_run_id: str | None = None,
) -> tuple[bool, str]:
    """
    Atomically (within process) validate workflow_state.yaml and transition
    resolution to APPROVE_EXTERNAL_HIRING only if:
      - status == "RESOURCE_BLOCKED"
      - run_id matches supplied_run_id (and expected_run_id if provided)
      - resolution == "PENDING"
    """
    import yaml

    state_path = outdir / f"{project_name}_workflow_state.yaml"
    with _WORKFLOW_STATE_LOCK:
        if not state_path.exists():
            return False, f"Approval rejected: workflow state for '{project_name}' does not exist."

        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = yaml.safe_load(f) or {}
        except Exception as exc:
            return False, (
                f"Approval rejected: could not read workflow state for '{project_name}' ({exc})."
            )

        if not isinstance(state, dict):
            return False, (
                f"Approval rejected: workflow state for '{project_name}' "
                "is invalid (expected a YAML mapping)."
            )

        current_status = state.get("status")
        current_run_id = str(state.get("run_id") or "").strip()
        current_resolution = state.get("resolution")

        if current_status != "RESOURCE_BLOCKED":
            return False, (
                f"Approval rejected: project '{project_name}' is not RESOURCE_BLOCKED "
                f"(current status: {current_status})."
            )

        if not current_run_id or supplied_run_id != current_run_id or (
            expected_run_id is not None and supplied_run_id != expected_run_id
        ):
            return False, (
                f"Approval rejected: stale or mismatched run_id '{supplied_run_id}' "
                f"for project '{project_name}'."
            )

        if current_resolution != "PENDING":
            return False, (
                f"Approval rejected: project '{project_name}' resolution is already "
                f"'{current_resolution}' (not PENDING)."
            )

        state["resolution"] = "APPROVE_EXTERNAL_HIRING"
        state["timestamp"] = datetime.now().isoformat()

        _atomic_write_yaml(state_path, state)

        print(f"{state_path.name} -> resolution=APPROVE_EXTERNAL_HIRING (run_id={supplied_run_id})")
        return True, ""


def handle_approve_external_hiring(
    bot: TelegramBot,
    chat_id: str,
    text: str = "",
    project_name: str | None = None,
    expected_run_id: str | None = None,
) -> None:
    """
    Log the external hiring approval decision and update workflow state so the
    ADK LoopAgent can proceed.

    Requires `/approve_external_hiring <run_id>` and conditionally updates the
    target project's workflow_state.yaml only when status=RESOURCE_BLOCKED,
    run_id matches, and resolution=PENDING.
    """
    parts = text.strip().split()
    supplied_run_id = parts[1].strip() if len(parts) == 2 else ""
    if not supplied_run_id:
        bot.send(
            chat_id,
            "Invalid command format.\n\n"
            "Please supply the run ID:\n"
            "  /approve_external_hiring <run_id>",
        )
        return

    outdir = _output_dir()
    target_project = project_name if project_name is not None else _detect_active_project(outdir)
    if not target_project:
        bot.send(
            chat_id,
            "Approval rejected: no active project workflow state found.",
        )
        return

    ok, err_msg = _conditional_update_approval_resolution(
        outdir=outdir,
        project_name=target_project,
        supplied_run_id=supplied_run_id,
        expected_run_id=expected_run_id,
    )
    if not ok:
        bot.send(chat_id, err_msg)
        return

    log_path = outdir / "decision_log.json"

    # Load or create the decision log
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            log = json.load(f)
    else:
        log = []

    entry = {
        "command": "/approve_external_hiring",
        "project": target_project,
        "run_id": supplied_run_id,
        "timestamp": datetime.now().isoformat(),
        "decision": "APPROVE_EXTERNAL_HIRING",
    }
    log.append(entry)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, default=str)

    print(
        f"External hiring approved for '{target_project}' (run_id={supplied_run_id}) "
        f"-> logged to {log_path}"
    )
    bot.send(
        chat_id,
        f"External Hiring Approved\n\n"
        f"Project: {target_project}\n"
        f"Run ID: {supplied_run_id}\n"
        f"Decision logged to decision_log.json.\n\n"
        f"The system will proceed with the current resource plan.",
    )


def handle_rebalance_tasks(bot: TelegramBot, chat_id: str) -> None:
    """
    Trigger incremental reassignment by writing resolution to workflow_state.yaml.
    The ResourceValidationAgent's polling loop detects this and re-runs ResourceAgent.
    """
    outdir = _output_dir()
    project_name = _detect_active_project(outdir)

    if not project_name:
        bot.send(chat_id, "No active project workflow state found. Start the pipeline first.")
        return

    _update_resolution(outdir, project_name, "REBALANCE")

    print(f"Rebalance requested for '{project_name}' -> workflow_state.yaml updated")
    bot.send(
        chat_id,
        f"Rebalance Triggered\n\n"
        f"Project: {project_name}\n\n"
        f"The system will reassign only waiting_resource tasks.\n"
        f"Existing assignments will NOT be overwritten."
    )


def _dispatch(
    bot: TelegramBot,
    user_chat_id: str,
    text: str,
    project_name: str | None = None,
    run_id: str | None = None,
) -> None:
    """Route a single incoming message to the correct handler."""
    print(f"[Telegram] [{user_chat_id}] {text}")

    # Normalize the command token (first whitespace-delimited token)
    first_token = text.strip().split()[0] if text.strip() else ""
    cmd = first_token.lower().replace("_", "").replace("/", "")

    if cmd == "addemployee" or _get_state(user_chat_id).get("step"):
        handle_add_employee(bot, user_chat_id, text)
    elif cmd == "approveexternalhiring":
        handle_approve_external_hiring(
            bot,
            user_chat_id,
            text=text,
            project_name=project_name,
            expected_run_id=run_id,
        )
    elif cmd == "rebalancetasks":
        handle_rebalance_tasks(bot, user_chat_id)
    else:
        approve_hint = (
            f"  /approve_external_hiring {run_id}"
            if run_id
            else r"  /approve\_external\_hiring <run\_id>"
        )
        bot.send(
            user_chat_id,
            "Unknown command.\n\n"
            "Available commands:\n"
            r"  /add\_employee" "\n"
            f"{approve_hint}\n"
            r"  /rebalance\_tasks",
        )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _detect_active_project(outdir: Path) -> str:
    """
    Find the most recently modified workflow_state.yaml with RESOURCE_BLOCKED status.
    Returns the project name string, or "" if none found.
    """
    import yaml

    state_files = sorted(
        outdir.glob("*_workflow_state.yaml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # First pass: prefer RESOURCE_BLOCKED projects
    for path in state_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = yaml.safe_load(f)
            if state and state.get("status") == "RESOURCE_BLOCKED":
                return str(state.get("project", ""))
        except Exception:
            continue
    # Fallback: any state file (most recently modified)
    for path in state_files:
        try:
            return path.name.replace("_workflow_state.yaml", "")
        except Exception:
            continue
    return ""


def _update_resolution(outdir: Path, project_name: str, resolution: str) -> None:
    """Write resolution value into the project's workflow_state.yaml."""
    import yaml

    state_path = outdir / f"{project_name}_workflow_state.yaml"
    if not state_path.exists():
        print(f"Workflow state file not found: {state_path}")
        return

    with open(state_path, "r", encoding="utf-8") as f:
        state = yaml.safe_load(f) or {}

    state["resolution"] = resolution
    state["timestamp"] = datetime.now().isoformat()

    with open(state_path, "w", encoding="utf-8") as f:
        yaml.dump(state, f, default_flow_style=False, allow_unicode=True)

    print(f"{state_path.name} -> resolution={resolution}")


# ---------------------------------------------------------------------------
# Async listener — spawned by ResourceValidationAgent as an asyncio.Task
# ---------------------------------------------------------------------------

async def run_telegram_listener(project_name: str, run_id: str | None = None) -> None:
    """
    Async Telegram polling loop run as a background asyncio.Task inside
    ResourceValidationAgent during a resource shortage wait period.

    Usage inside ResourceValidationAgent:
        bot_task = asyncio.create_task(run_telegram_listener(project_name, run_id=run_id))
        # ... dual-watch wait loop ...
        bot_task.cancel()

    Uses asyncio.to_thread() for blocking requests calls so the ADK event loop
    is never blocked. Stops automatically when the task is cancelled.
    """
    try:
        token, chat_id = _load_telegram_config()
    except EnvironmentError as exc:
        print(f"Telegram bot not started: {exc}")
        return

    bot = TelegramBot(token=token, chat_id=chat_id)
    print("[Embedded] Telegram bot listening for PM commands...")

    approve_cmd = (
        f"/approve_external_hiring {run_id}"
        if run_id
        else "/approve_external_hiring <run_id>"
    )
    bot.reply(
        f"PM Bot active - waiting for your decision on {project_name}!\n\n"
        "Available commands:\n"
        r"  /add\_employee - Add a team member" "\n"
        f"  {approve_cmd} - Approve external hire\n"
        r"  /rebalance\_tasks - Trigger reassignment"
    )

    try:
        while True:
            # Run the blocking HTTP long-poll in a thread so the ADK event loop
            # (and all other ADK agents) continue to run concurrently.
            # asyncio.to_thread requires Python 3.9+.
            updates = await asyncio.to_thread(bot.get_updates)

            for update in updates:
                msg = update.get("message", {})
                user_chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()
                if text:
                    # Handlers do file I/O -- run in thread to stay non-blocking
                    await asyncio.to_thread(
                        _dispatch,
                        bot,
                        user_chat_id,
                        text,
                        project_name,
                        run_id,
                    )

            # Yield back to the event loop so the dual-watch loop can run
            await asyncio.sleep(0.5)

    except asyncio.CancelledError:
        # Raised by ResourceValidationAgent when the shortage is resolved
        bot.reply(
            f"Shortage resolved for {project_name}.\n"
            "The system is resuming the assignment pipeline. Bot going offline."
        )
        print("[Embedded] Telegram bot stopped - shortage resolved.")


# ---------------------------------------------------------------------------
# Standalone entry point (optional - for direct testing)
# ---------------------------------------------------------------------------

def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        token, chat_id = _load_telegram_config()
    except EnvironmentError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    bot = TelegramBot(token=token, chat_id=chat_id)
    print("PM Assistant Telegram Bot running (standalone mode)...")
    print("  Listening for: /add_employee | /approve_external_hiring <run_id> | /rebalance_tasks")
    print("  Press Ctrl+C to stop.\n")

    bot.reply(
        "PM Assistant Bot online!\n\n"
        "Available commands:\n"
        r"  /add\_employee - Add a new team member" "\n"
        r"  /approve\_external\_hiring - Approve external hire" "\n"
        r"  /rebalance\_tasks - Trigger incremental reassignment"
    )


    while True:
        try:
            updates = bot.get_updates()
            for update in updates:
                msg = update.get("message", {})
                user_chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()
                if text:
                    _dispatch(bot, user_chat_id, text)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as exc:
            print(f"Polling error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
