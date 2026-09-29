import json
import os
import re
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from google.adk.tools.function_tool import FunctionTool
from data_backend import get_backend
from config_loader import load_config


def _parse_json(raw_input):
    if isinstance(raw_input, dict):
        return raw_input
    raw_str = str(raw_input)
    match = re.search(r'\{.*\}', raw_str, re.DOTALL)
    if not match:
        raise ValueError("No valid JSON found in input")
    # Replace literal NaN (from Pandas) with null for valid JSON
    cleaned = re.sub(r'\bNaN\b', 'null', match.group(0))
    return json.loads(cleaned)


def add_working_days(start_date, days):
    current = start_date
    added = 0
    while added < int(days):
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def save_schedule(input_json: str) -> str:
    data = _parse_json(input_json)

    project_name = data["project_name"]
    tasks = data.get("tasks", [])

    if not tasks:
        return json.dumps({"error": "No tasks provided to scheduler"})

    df = pd.DataFrame(tasks)
    df.columns = df.columns.str.strip()
    col_map = {col.lower().replace("_", ""): col for col in df.columns}

    if "assignedemployee" in col_map:
        df = df.rename(columns={col_map["assignedemployee"]: "assigned_empl"})
    elif "assignedempl" in col_map:
        df = df.rename(columns={col_map["assignedempl"]: "assigned_empl"})

    if "assigned_empl" not in df.columns:
        return json.dumps({"error": "Missing assigned_empl or Assigned_Employee column in task data"})

    df["time_days"] = pd.to_numeric(df["time_days"], errors="coerce").fillna(0)

    backend = get_backend()
    employees_df = backend.read("employees")
    employees_df.columns = employees_df.columns.str.strip()
    email_map = dict(zip(employees_df["Employee_Name"], employees_df["Email"]))

    config = load_config()
    project_start = datetime.now().date()

    pm_candidates = employees_df[
        employees_df["Role"].str.contains("Project Manager", case=False, na=False)
    ]
    pm_name = pm_candidates.iloc[0]["Employee_Name"]
    pm_email = pm_candidates.iloc[0]["Email"]

    start_dates, end_dates = [], []
    current_cursor = project_start
    while current_cursor.weekday() >= 5:
        current_cursor += timedelta(days=1)

    for _, row in df.iterrows():
        task_start = current_cursor
        task_end = add_working_days(task_start, float(row["time_days"]))
        start_dates.append(task_start.strftime("%Y-%m-%d"))
        end_dates.append(task_end.strftime("%Y-%m-%d"))
        current_cursor = task_end + timedelta(days=1)
        while current_cursor.weekday() >= 5:
            current_cursor += timedelta(days=1)

    df["start_date"] = start_dates
    df["end_date"] = end_dates
    df["assigned_email"] = df["assigned_empl"].map(email_map)
    df["project_manager"] = pm_name
    df["project_manager_email"] = pm_email
    df["RACI"] = "R"

    # Sanitize NaN -> None for clean JSON output
    df = df.where(pd.notnull(df), None)

    backend.write(f"{project_name}_Scheduled", df)
    print(f"\u2705 Schedule saved: {project_name}_Scheduled")


    # ── Telegram task-assignment DMs ────────────────────────────────────────
    try:
        from agents.communication.notifiers.telegram_notifier import TelegramNotifier

        tg_cfg = config.get("telegram", {})
        if tg_cfg.get("enabled", False):
            bot_token = os.environ.get(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
            if bot_token:
                tg = TelegramNotifier(bot_token=bot_token)

                # Read telegram_chat_id from employees
                if "telegram_chat_id" in employees_df.columns:
                    chat_id_map = dict(zip(
                        employees_df["Employee_Name"].astype(str).str.strip(),
                        employees_df["telegram_chat_id"].astype(str).str.strip(),
                    ))

                    # Group tasks by assigned employee
                    for emp_name, emp_tasks in df.groupby("assigned_empl"):
                        emp_name_str = str(emp_name).strip()
                        chat_id = str(chat_id_map.get(emp_name_str, "")).strip()
                        if chat_id.endswith(".0"):
                            chat_id = chat_id[:-2]
                        if not chat_id or chat_id.lower() in ("", "nan", "none"):
                            continue

                        # Build task list for this employee
                        task_lines = []
                        for _, row in emp_tasks.iterrows():
                            t_name = row.get("task_name", row.get("task", "Task"))
                            t_start = row.get("start_date", "")
                            t_end = row.get("end_date", "")
                            task_lines.append(
                                TelegramNotifier.escape_md(f"  • {str(t_name)} ({t_start} → {t_end})")
                            )

                        msg = (
                            f"🤖 *PM Assistant*\n\n"
                            f"Hello {TelegramNotifier.escape_md(emp_name_str)},\n\n"
                            f"You have been assigned to *{TelegramNotifier.escape_md(project_name)}*\\.\n\n"
                            f"📋 *Your Tasks:*\n" + "\n".join(task_lines) + "\n\n"
                            f"📧 Check your email for full details\\."
                        )
                        tg.send_to_chat(chat_id, msg, name=emp_name_str)
    except Exception as tg_err:
        print(f"⚠️ Telegram task-assignment DMs skipped: {tg_err}")

    return json.dumps({"project_name": project_name, "tasks": df.to_dict(orient="records")}, default=str)


save_schedule_tool = FunctionTool(save_schedule)
