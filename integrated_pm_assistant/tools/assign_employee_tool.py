"""
assign_employee_tool.py
-----------------------
Incremental resource assignment tool.

Key behaviours:
  1. PRESERVE existing assignments — reads {project}_Assigned.xlsx on every run
     and keeps already-assigned rows unchanged.
  2. PROCESS ONLY waiting tasks — a task is eligible for (re-)assignment when:
       • Assigned_Employee is empty / NaN, OR
       • Assigned_Employee == "No Resource Available", OR
       • task_status == "waiting_resource"
  3. BALANCED selection — picks the employee with the LOWEST Allocated_Hours
     among eligible candidates (those with Free_Hours > 0), not the highest.
  4. SHORTAGE marking — when no candidate is found, sets task_status to
     "waiting_resource" so the ValidationAgent can detect and report it.
"""

from typing import List, Dict, Any

import pandas as pd
from google.adk.tools.function_tool import FunctionTool

from data_backend import get_backend
from config_loader import load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_unassigned(row: pd.Series) -> bool:
    """Return True if a task row still needs an employee assigned."""
    emp = str(row.get("Assigned_Employee", "")).strip()
    status = str(row.get("task_status", "")).strip().lower()

    unassigned_values = {"", "nan", "none", "no resource available"}
    return emp.lower() in unassigned_values or status == "waiting_resource"


def _load_existing_assignments(backend, project_name: str) -> pd.DataFrame:
    """
    Read the previously saved assigned-tasks file, if it exists.
    Returns an empty DataFrame if this is the first run.
    """
    table_name = f"{project_name}_Assigned"
    if backend.exists(table_name):
        return backend.read(table_name)
    return pd.DataFrame()


def _merge_tasks(existing_df: pd.DataFrame, incoming_tasks: List[Dict]) -> pd.DataFrame:
    """
    Merge incoming task list with the existing assignments DataFrame.

    Rules:
    • If a task_id already exists in the saved file AND is assigned → keep the
      saved row (preserves previous assignments).
    • Otherwise use the incoming row (new task or waiting_resource task).
    """
    incoming_df = pd.DataFrame(incoming_tasks)

    if existing_df.empty or "task_id" not in existing_df.columns:
        return incoming_df.copy()

    if "task_id" not in incoming_df.columns:
        return incoming_df.copy()

    # Index existing by task_id for O(1) lookup (preserve task_id column)
    existing_indexed = existing_df.set_index("task_id", drop=False)

    merged_rows = []
    for _, row in incoming_df.iterrows():
        tid = row.get("task_id")
        if tid in existing_indexed.index:
            existing_row = existing_indexed.loc[tid]
            if isinstance(existing_row, pd.DataFrame):
                existing_row = existing_row.iloc[0]
            # Keep existing row only if it is already properly assigned
            if not _is_unassigned(existing_row):
                row_dict = existing_row.to_dict()
                row_dict["task_id"] = tid  # restore / ensure task_id is present
                merged_rows.append(row_dict)
                continue
        # Otherwise use the incoming (unassigned) row
        merged_rows.append(row.to_dict())

    return pd.DataFrame(merged_rows)


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

def assign_resources(project_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Incrementally assign employees to tasks.

    Args:
        project_name: Used to locate the existing Assigned file and to
                      update employees' Current_Project field.
        tasks:        Full task list from the TaskAgent (or LoopAgent retry).

    Returns:
        {"project_name": str, "tasks": list, "resource_blocked": bool}
    """
    if not tasks:
        return {"error": "No tasks provided"}

    # ── 1. Validate incoming tasks ──────────────────────────────────────────
    incoming_df = pd.DataFrame(tasks)

    if "assigned_role" not in incoming_df.columns:
        return {"error": "Missing assigned_role column"}

    if "time_days" not in incoming_df.columns:
        incoming_df["time_days"] = 0
    incoming_df["time_days"] = pd.to_numeric(
        incoming_df["time_days"], errors="coerce"
    ).fillna(0)

    # ── 2. Load and merge with existing assignments ─────────────────────────
    backend = get_backend()
    existing_df = _load_existing_assignments(backend, project_name)
    tasks_df = _merge_tasks(existing_df, tasks)

    # ── 3. Load employees ───────────────────────────────────────────────────
    employees_df = backend.read("employees")

    required_cols = ["Role", "Free_Hours", "Allocated_Hours", "Employee_Name"]
    # Support legacy column name "Allocated_Hour" (without trailing 's')
    if "Allocated_Hours" not in employees_df.columns and "Allocated_Hour" in employees_df.columns:
        employees_df = employees_df.rename(columns={"Allocated_Hour": "Allocated_Hours"})

    for col in required_cols:
        if col not in employees_df.columns:
            return {"error": f"Missing column '{col}' in employees data"}

    employees_df["Free_Hours"] = pd.to_numeric(
        employees_df["Free_Hours"], errors="coerce"
    ).fillna(0)
    employees_df["Allocated_Hours"] = pd.to_numeric(
        employees_df["Allocated_Hours"], errors="coerce"
    ).fillna(0)

    if "Current_Project" not in employees_df.columns:
        employees_df["Current_Project"] = ""
    else:
        employees_df["Current_Project"] = employees_df["Current_Project"].astype(object)

    # ── 4. Role mapping ─────────────────────────────────────────────────────
    config = load_config()
    ROLE_MAP = config.get("role_map", {})

    # ── 5. Incremental assignment loop ──────────────────────────────────────
    for i in range(len(tasks_df)):
        row = tasks_df.iloc[i]

        # Skip rows that already have a valid assignment
        if not _is_unassigned(row):
            continue

        # Resolve role abbreviation → canonical name
        raw_role = str(row["assigned_role"]).strip()
        first_role = raw_role.split(",")[0].strip()
        target_role = ROLE_MAP.get(first_role, first_role)

        # Find eligible candidates — role match AND has free capacity
        candidates = employees_df[
            (employees_df["Role"].astype(str).str.strip() == target_role)
            & (employees_df["Free_Hours"] > 0)
        ]

        if not candidates.empty:
            # BALANCED SELECTION: pick the employee with the LOWEST
            # Allocated_Hours to distribute work evenly across the team
            emp_index = candidates["Allocated_Hours"].idxmin()
            emp_name = employees_df.loc[emp_index, "Employee_Name"]

            hours_needed = tasks_df.at[i, "time_days"] * 8

            tasks_df.at[i, "Assigned_Employee"] = emp_name
            tasks_df.at[i, "task_status"] = "assigned"

            # ── Update the employee's running totals ──────────
            employees_df.at[emp_index, "Allocated_Hours"] += hours_needed
            employees_df.at[emp_index, "Free_Hours"] -= hours_needed
            
            # CUMULATIVE: Append project name if not already present
            old_projects = str(employees_df.at[emp_index, "Current_Project"]).strip()
            if old_projects and old_projects.lower() != "nan":
                plist = [p.strip() for p in old_projects.split(",") if p.strip()]
                if project_name not in plist:
                    plist.append(project_name)
                    employees_df.at[emp_index, "Current_Project"] = ", ".join(plist)
            else:
                employees_df.at[emp_index, "Current_Project"] = project_name


        else:
            # No capacity available — mark for PM attention
            tasks_df.at[i, "Assigned_Employee"] = "No Resource Available"
            tasks_df.at[i, "task_status"] = "waiting_resource"

    # ── 6. Persist results ──────────────────────────────────────────────────
    # Sanitize for JSON & Excel: replace NaN with None
    tasks_df = tasks_df.where(pd.notnull(tasks_df), None)

    # Write the FULL merged dataset back (assigned rows are preserved)
    backend.write(f"{project_name}_Assigned", tasks_df)
    backend.write("employees", employees_df)
    print("Resource assignment complete")

    # Note: employees_df rename for downstream is already handled above line 198
    # but that employees_df variable is separate from tasks_df.

    resource_blocked = any(
        str(row.get("task_status", "")).strip() == "waiting_resource"
        for row in tasks_df.to_dict(orient="records")
    )

    return {
        "project_name": project_name,
        "tasks": tasks_df.to_dict(orient="records"),
        "resource_blocked": resource_blocked,
    }


# Register as an ADK FunctionTool
assign_employee_tool = FunctionTool(assign_resources)
