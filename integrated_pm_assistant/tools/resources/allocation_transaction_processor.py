"""
allocation_transaction_processor.py
-----------------------------------
Dedicated transaction processor for resource allocations and rollbacks.

Coordinates:
- employees.xlsx: employee records and hour balances only (no transaction columns).
- allocation_transactions.xlsx: durable transaction journal, attempt IDs, original
  and intended values, transaction states, and recovery information.
- {project}_Assigned.xlsx: task assignments.

Guarantees:
- PREPARED → EMPLOYEES_APPLIED → ASSIGNMENTS_APPLIED → COMMITTED state progression.
- ROLLBACK_REQUESTED → EMPLOYEES_RESTORED → ASSIGNMENTS_RESTORED → ROLLED_BACK.
- Validated state transitions and exact journal write verification.
- Explicit write verification (balances, Employee_ID, Current_Project, task fields)
  before state advances.
- Crash recovery that inspects actual file contents against recorded original and
  intended states, halts with NEEDS_REVIEW on conflicts/corruption/missing/duplicate
  rows, and never overwrites newer committed transactions.
- Rejection or safe resumption of duplicate attempt IDs without duplicate deductions.
- Strict employee identity matching with stable Employee_ID (no name fallback if ID missing).
- Mutual exclusion using existing backend transaction locks.
"""

from typing import List, Dict, Any, Optional, Tuple
import json
import math
import uuid
from datetime import datetime
import pandas as pd

from data_backend import get_backend
from config_loader import load_config


# ---------------------------------------------------------------------------
# Transaction State Constants & Valid Transitions
# ---------------------------------------------------------------------------

STATE_PREPARED = "PREPARED"
STATE_EMPLOYEES_APPLIED = "EMPLOYEES_APPLIED"
STATE_ASSIGNMENTS_APPLIED = "ASSIGNMENTS_APPLIED"
STATE_COMMITTED = "COMMITTED"

STATE_ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
STATE_EMPLOYEES_RESTORED = "EMPLOYEES_RESTORED"
STATE_ASSIGNMENTS_RESTORED = "ASSIGNMENTS_RESTORED"
STATE_ROLLED_BACK = "ROLLED_BACK"

STATE_NEEDS_REVIEW = "NEEDS_REVIEW"

INCOMPLETE_ALLOCATION_STATES = {
    STATE_PREPARED,
    STATE_EMPLOYEES_APPLIED,
    STATE_ASSIGNMENTS_APPLIED,
}

INCOMPLETE_ROLLBACK_STATES = {
    STATE_ROLLBACK_REQUESTED,
    STATE_EMPLOYEES_RESTORED,
    STATE_ASSIGNMENTS_RESTORED,
}

ALL_INCOMPLETE_STATES = INCOMPLETE_ALLOCATION_STATES | INCOMPLETE_ROLLBACK_STATES

VALID_STATE_TRANSITIONS: Dict[str, set] = {
    STATE_PREPARED: {STATE_EMPLOYEES_APPLIED, STATE_ROLLBACK_REQUESTED, STATE_NEEDS_REVIEW},
    STATE_EMPLOYEES_APPLIED: {STATE_ASSIGNMENTS_APPLIED, STATE_ROLLBACK_REQUESTED, STATE_NEEDS_REVIEW},
    STATE_ASSIGNMENTS_APPLIED: {STATE_COMMITTED, STATE_ROLLBACK_REQUESTED, STATE_NEEDS_REVIEW},
    STATE_COMMITTED: {STATE_ROLLBACK_REQUESTED, STATE_NEEDS_REVIEW},
    STATE_ROLLBACK_REQUESTED: {STATE_EMPLOYEES_RESTORED, STATE_NEEDS_REVIEW},
    STATE_EMPLOYEES_RESTORED: {STATE_ASSIGNMENTS_RESTORED, STATE_NEEDS_REVIEW},
    STATE_ASSIGNMENTS_RESTORED: {STATE_ROLLED_BACK, STATE_NEEDS_REVIEW},
    STATE_ROLLED_BACK: set(),
    STATE_NEEDS_REVIEW: set(),
}

JOURNAL_TABLE = "allocation_transactions"
JOURNAL_COLUMNS = [
    "transaction_id",
    "attempt_id",
    "project_name",
    "transaction_type",
    "state",
    "created_at",
    "updated_at",
    "details_json",
    "notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_id(val: Any) -> str:
    """Normalize identifiers (strings, ints, floats) for robust matching."""
    if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
        return ""
    s = str(val).strip()
    if s.lower() in {"nan", "none"}:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _clean_str(val: Any) -> str:
    """Normalize optional string cell values (converting None/NaN to empty string)."""
    if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in {"nan", "none"} else s


def _find_employee_id_col(df: pd.DataFrame) -> Optional[str]:
    """Identify stable employee ID column if present in dataframe."""
    candidate_cols = [
        "Employee_ID",
        "Employee ID",
        "EmployeeId",
        "employee_id",
        "Emp_ID",
        "Emp ID",
        "emp_id",
        "ID",
        "id",
    ]
    for col in candidate_cols:
        if col in df.columns:
            return col
    return None


def _validate_duration(val: Any) -> Optional[float]:
    """
    Validate task duration in days.
    Returns positive float if valid and finite, else None.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
        if math.isfinite(f) and f > 0:
            return f
        return None
    except (ValueError, TypeError):
        return None


def _get_working_hours_per_day(config: Dict[str, Any]) -> float:
    """Extract working_hours_per_day from config, preserving default 8.0."""
    try:
        val = config.get("project_defaults", {}).get("working_hours_per_day")
        if val is not None:
            f = float(val)
            if math.isfinite(f) and f > 0:
                return f
    except (ValueError, TypeError):
        pass
    return 8.0


def _is_unassigned(row: pd.Series) -> bool:
    """Return True if a task row still needs an employee assigned."""
    emp = _clean_str(row.get("Assigned_Employee", ""))
    status = _clean_str(row.get("task_status", "")).lower()
    unassigned_values = {"", "no resource available"}
    return emp.lower() in unassigned_values or status == "waiting_resource"


def _update_current_project_add(current_val: Any, project_name: str) -> str:
    """Append project_name to comma-separated Current_Project string if absent."""
    old_str = _clean_str(current_val)
    if old_str:
        plist = [p.strip() for p in old_str.split(",") if p.strip()]
        if project_name not in plist:
            plist.append(project_name)
        return ", ".join(plist)
    return project_name


def _load_existing_assignments(backend, project_name: str) -> pd.DataFrame:
    """Read the previously saved assigned-tasks file, if it exists."""
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
    • Preserve any existing assigned tasks not present in incoming_tasks.
    """
    incoming_df = pd.DataFrame(incoming_tasks)

    if existing_df.empty or "task_id" not in existing_df.columns:
        return incoming_df.copy()

    if "task_id" not in incoming_df.columns:
        return incoming_df.copy()

    existing_indexed = existing_df.set_index(existing_df["task_id"].apply(_normalize_id), drop=False)

    merged_rows = []
    seen_tids = set()
    for _, row in incoming_df.iterrows():
        tid_raw = row.get("task_id")
        tid = _normalize_id(tid_raw)
        if tid and tid in existing_indexed.index:
            existing_row = existing_indexed.loc[tid]
            if isinstance(existing_row, pd.DataFrame):
                existing_row = existing_row.iloc[0]
            if not _is_unassigned(existing_row):
                row_dict = existing_row.to_dict()
                row_dict["task_id"] = tid_raw
                merged_rows.append(row_dict)
                seen_tids.add(tid)
                continue
        merged_rows.append(row.to_dict())
        if tid:
            seen_tids.add(tid)

    for tid, ex_row in existing_indexed.iterrows():
        if tid and tid not in seen_tids:
            if not _is_unassigned(ex_row):
                row_dict = ex_row.to_dict()
                merged_rows.append(row_dict)
                seen_tids.add(tid)

    return pd.DataFrame(merged_rows)


def _parse_and_validate_details(details_raw: Any) -> Dict[str, Any]:
    """
    Parse and validate journal details_json.
    Raises ValueError if corrupted or missing required structure.
    """
    if details_raw is None or (isinstance(details_raw, float) and pd.isna(details_raw)):
        raise ValueError("Journal details_json is missing or null.")
    raw_str = str(details_raw).strip()
    if not raw_str or raw_str.lower() in {"nan", "none"}:
        raise ValueError("Journal details_json is empty.")
    try:
        parsed = json.loads(raw_str)
    except Exception as exc:
        raise ValueError(f"Journal details_json is invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Journal details_json must decode to a JSON object.")
    if "employees" not in parsed or not isinstance(parsed["employees"], dict):
        raise ValueError("Journal details_json missing valid 'employees' mapping.")
    if "tasks" not in parsed or not isinstance(parsed["tasks"], list):
        raise ValueError("Journal details_json missing valid 'tasks' list.")
    return parsed


def _canonical_emp_set(emp_dict: Dict[str, Any]) -> set:
    """
    Build a canonical set of employee identifiers from an ``affected_employees``
    dict (as stored in journal details).

    Each employee may be keyed either by a stable Employee_ID (e.g. ``"E001"``)
    or by a legacy name key (``"name:alice"``).  To detect cross-format overlap
    between transactions, this function emits *both* a normalised ID token and a
    normalised name token for every employee present in *emp_dict*.

    Tokens:
      • ``"id:<normalised_id>"``  — when a stable Employee_ID is available.
      • ``"name:<lower_name>"``   — when a non-empty employee_name is recorded.

    Two transactions share an employee if and only if their canonical sets
    intersect on at least one token.
    """
    result: set = set()
    for _key, info in emp_dict.items():
        norm_id = _normalize_id(info.get("employee_id"))
        norm_name = _clean_str(info.get("employee_name", "")).lower()
        if norm_id:
            result.add(f"id:{norm_id}")
        if norm_name:
            result.add(f"name:{norm_name}")
    return result


# ---------------------------------------------------------------------------
# Allocation Transaction Processor
# ---------------------------------------------------------------------------

class AllocationTransactionProcessor:
    """
    Durable, recoverable transaction processor coordinating allocation and rollback.
    """

    def __init__(self, backend=None):
        self.backend = backend or get_backend()

    # ------------------------------------------------------------------
    # Journal Persistence & State Management
    # ------------------------------------------------------------------

    def _load_journal(self) -> pd.DataFrame:
        """Load allocation_transactions table, ensuring schema columns."""
        if self.backend.exists(JOURNAL_TABLE):
            df = self.backend.read(JOURNAL_TABLE)
            for col in JOURNAL_COLUMNS:
                if col not in df.columns:
                    df[col] = None
            return df
        return pd.DataFrame(columns=JOURNAL_COLUMNS)

    def _save_journal(
        self,
        df: pd.DataFrame,
        verify_project: Optional[str] = None,
        verify_attempt: Optional[str] = None,
    ) -> None:
        """Persist allocation_transactions table and verify the exact record write."""
        self.backend.write(JOURNAL_TABLE, df)
        read_back = self.backend.read(JOURNAL_TABLE)
        if len(read_back) != len(df):
            raise RuntimeError(
                f"Journal write verification failed: row count mismatch (expected {len(df)}, got {len(read_back)})."
            )

        if verify_project is not None and verify_attempt is not None:
            exp_mask = (
                (df["project_name"].astype(str) == verify_project)
                & (df["attempt_id"].astype(str) == verify_attempt)
            )
            act_mask = (
                (read_back["project_name"].astype(str) == verify_project)
                & (read_back["attempt_id"].astype(str) == verify_attempt)
            )
            if exp_mask.sum() != 1 or act_mask.sum() != 1:
                raise RuntimeError(
                    f"Journal write verification failed: expected 1 record for ({verify_project}, {verify_attempt}), "
                    f"got {act_mask.sum()}."
                )

            exp_row = df[exp_mask].iloc[0]
            act_row = read_back[act_mask].iloc[0]
            for field in ["transaction_id", "state", "transaction_type", "updated_at", "details_json"]:
                if _clean_str(act_row.get(field)) != _clean_str(exp_row.get(field)):
                    raise RuntimeError(
                        f"Journal write verification failed for ({verify_project}, {verify_attempt}) "
                        f"on field '{field}'."
                    )

    def _get_transaction(self, attempt_id: str, project_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Look up transaction record by (project_name, attempt_id)."""
        journal_df = self._load_journal()
        if journal_df.empty or "attempt_id" not in journal_df.columns:
            return None
        mask = journal_df["attempt_id"].astype(str) == attempt_id
        if project_name is not None:
            mask = mask & (journal_df["project_name"].astype(str) == project_name)
        matches = journal_df[mask]
        if matches.empty:
            return None
        if len(matches) > 1 and project_name is not None:
            raise RuntimeError(
                f"Duplicate journal records found for ({project_name}, {attempt_id})."
            )
        row = matches.iloc[-1]
        res = row.to_dict()
        try:
            res["details"] = _parse_and_validate_details(res.get("details_json"))
            res["corrupted"] = False
            res["corruption_error"] = None
        except ValueError as exc:
            res["details"] = None
            res["corrupted"] = True
            res["corruption_error"] = str(exc)
        return res

    def _record_transaction(self, tx_data: Dict[str, Any]) -> None:
        """Upsert transaction in allocation_transactions journal keyed by (project_name, attempt_id)."""
        journal_df = self._load_journal()
        att_id = str(tx_data["attempt_id"]).strip()
        proj_name = str(tx_data["project_name"]).strip()

        row_dict = {
            "transaction_id": tx_data.get("transaction_id", f"tx_{uuid.uuid4().hex[:12]}"),
            "attempt_id": att_id,
            "project_name": proj_name,
            "transaction_type": tx_data.get("transaction_type", "ALLOCATION"),
            "state": tx_data["state"],
            "created_at": tx_data.get("created_at", datetime.now().isoformat()),
            "updated_at": datetime.now().isoformat(),
            "details_json": json.dumps(tx_data.get("details", {}), ensure_ascii=False),
            "notes": tx_data.get("notes", ""),
        }

        if not journal_df.empty and "attempt_id" in journal_df.columns and "project_name" in journal_df.columns:
            mask = (
                (journal_df["project_name"].astype(str) == proj_name)
                & (journal_df["attempt_id"].astype(str) == att_id)
            )
        else:
            mask = pd.Series(False, index=journal_df.index)

        if mask.any():
            if mask.sum() > 1:
                raise RuntimeError(f"Duplicate journal records found for ({proj_name}, {att_id}).")
            idx = journal_df[mask].index[0]
            current_state = _clean_str(journal_df.at[idx, "state"])
            new_state = row_dict["state"]
            if current_state != new_state and new_state not in VALID_STATE_TRANSITIONS.get(current_state, set()):
                raise RuntimeError(
                    f"Invalid transaction state transition from '{current_state}' to '{new_state}' "
                    f"for ({proj_name}, {att_id})."
                )
            for col, val in row_dict.items():
                journal_df.at[idx, col] = val
        else:
            journal_df = pd.concat([journal_df, pd.DataFrame([row_dict])], ignore_index=True)

        self._save_journal(journal_df, verify_project=proj_name, verify_attempt=att_id)

    def _update_transaction_state(
        self,
        project_name: str,
        attempt_id: str,
        new_state: str,
        notes: Optional[str] = None,
    ) -> None:
        """Advance transaction state after validating transition and verify write."""
        journal_df = self._load_journal()
        mask = (
            (journal_df["project_name"].astype(str) == project_name)
            & (journal_df["attempt_id"].astype(str) == attempt_id)
        )
        if not mask.any():
            raise RuntimeError(f"Transaction ({project_name}, '{attempt_id}') not found in journal for state update.")
        if mask.sum() > 1:
            raise RuntimeError(f"Duplicate journal records found for ({project_name}, '{attempt_id}').")

        idx = journal_df[mask].index[0]
        current_state = _clean_str(journal_df.at[idx, "state"])
        if current_state == new_state:
            return
        if new_state not in VALID_STATE_TRANSITIONS.get(current_state, set()):
            raise RuntimeError(
                f"Invalid transaction state transition from '{current_state}' to '{new_state}' "
                f"for ({project_name}, '{attempt_id}')."
            )

        journal_df.at[idx, "state"] = new_state
        journal_df.at[idx, "updated_at"] = datetime.now().isoformat()
        if notes is not None:
            journal_df.at[idx, "notes"] = notes
        self._save_journal(journal_df, verify_project=project_name, verify_attempt=attempt_id)

    # ------------------------------------------------------------------
    # Employee Identity & Write Verification
    # ------------------------------------------------------------------

    def _find_unique_employee_index(
        self,
        df: pd.DataFrame,
        emp_id: Optional[str],
        emp_name: Optional[str],
        id_col: Optional[str],
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Enforce unique employee matching using stable IDs when present,
        or unique normalized names for legacy records without IDs.
        Returns (row_index, error_message).
        """
        name_col = "Employee_Name"
        norm_id = _normalize_id(emp_id)

        if norm_id:
            if not id_col or id_col not in df.columns:
                return None, f"Employee ID column missing; cannot match Employee_ID '{norm_id}'."
            match = df[id_col].apply(_normalize_id) == norm_id
            count = int(match.sum())
            if count == 0:
                return None, f"Employee with Employee_ID '{norm_id}' not found in employees table."
            if count > 1:
                return None, f"Ambiguous duplicate Employee_ID '{norm_id}' found ({count} rows) in employees table."
            return df[match].index[0], None

        norm_name = _clean_str(emp_name).lower()
        if not norm_name or name_col not in df.columns:
            return None, f"Legacy employee '{emp_name}' cannot be matched without Employee_Name column."
        match = df[name_col].astype(str).str.strip().str.lower() == norm_name
        count = int(match.sum())
        if count == 0:
            return None, f"Legacy employee '{emp_name}' not found in employees table."
        if count > 1:
            return None, f"Ambiguous duplicate legacy Employee_Name '{emp_name}' found ({count} rows) in employees table."
        return df[match].index[0], None

    def _verify_employees_write(self, expected_employees: Dict[str, Dict[str, Any]]) -> None:
        """
        Re-read employees table and verify all affected employees match expected
        balances, Employee_ID, and Current_Project.
        """
        df = self.backend.read("employees")
        if "Allocated_Hours" not in df.columns and "Allocated_Hour" in df.columns:
            df = df.rename(columns={"Allocated_Hour": "Allocated_Hours"})
        for req_col in ["Employee_Name", "Free_Hours", "Allocated_Hours"]:
            if req_col not in df.columns:
                raise RuntimeError(f"Write verification failed: missing required column '{req_col}' in employees table.")
        if any("expected_current_project" in exp for exp in expected_employees.values()) and "Current_Project" not in df.columns:
            raise RuntimeError("Write verification failed: missing required column 'Current_Project' in employees table.")

        id_col = _find_employee_id_col(df)

        for emp_key, exp in expected_employees.items():
            emp_id = exp.get("employee_id")
            emp_name = exp.get("employee_name")

            row_idx, err = self._find_unique_employee_index(df, emp_id, emp_name, id_col)
            if err:
                raise RuntimeError(f"Write verification failed for employee '{emp_key}': {err}")

            row = df.loc[row_idx]
            if emp_id and id_col:
                act_id = _normalize_id(row.get(id_col))
                if act_id != _normalize_id(emp_id):
                    raise RuntimeError(
                        f"Write verification failed for employee '{emp_key}': "
                        f"Employee_ID expected '{_normalize_id(emp_id)}', got '{act_id}'."
                    )

            actual_free = float(pd.to_numeric(row["Free_Hours"], errors="coerce"))
            actual_alloc = float(pd.to_numeric(row["Allocated_Hours"], errors="coerce"))

            exp_free = float(exp["expected_free_hours"])
            exp_alloc = float(exp["expected_allocated_hours"])

            if math.isnan(actual_free) or abs(actual_free - exp_free) > 1e-4:
                raise RuntimeError(
                    f"Write verification failed for employee '{emp_key}': "
                    f"Free_Hours expected {exp_free}, got {actual_free}."
                )
            if math.isnan(actual_alloc) or abs(actual_alloc - exp_alloc) > 1e-4:
                raise RuntimeError(
                    f"Write verification failed for employee '{emp_key}': "
                    f"Allocated_Hours expected {exp_alloc}, got {actual_alloc}."
                )

            if "expected_current_project" in exp:
                act_proj = _clean_str(row.get("Current_Project"))
                exp_proj = _clean_str(exp["expected_current_project"])
                if act_proj != exp_proj:
                    raise RuntimeError(
                        f"Write verification failed for employee '{emp_key}': "
                        f"Current_Project expected '{exp_proj}', got '{act_proj}'."
                    )

    def _verify_assignments_write(self, assigned_table: str, expected_tasks: List[Dict[str, Any]]) -> None:
        """
        Re-read assigned tasks table and verify affected tasks match expected values,
        including Assigned_Employee, Assigned_Employee_ID, task_status, and allocation_attempt_id.
        """
        df = self.backend.read(assigned_table)
        if df.empty:
            raise RuntimeError(f"Write verification failed: {assigned_table} is empty.")
        for req_col in ["task_id", "Assigned_Employee", "task_status", "allocation_attempt_id"]:
            if req_col not in df.columns:
                raise RuntimeError(
                    f"Write verification failed: {assigned_table} missing required column '{req_col}'."
                )

        norm_tids = df["task_id"].apply(_normalize_id)

        for exp in expected_tasks:
            tid = _normalize_id(exp["task_id"])
            matches = df[norm_tids == tid]
            if matches.empty:
                raise RuntimeError(f"Write verification failed: task '{tid}' missing from {assigned_table}.")
            if len(matches) > 1:
                raise RuntimeError(f"Write verification failed: duplicate task_id '{tid}' in {assigned_table}.")

            row = matches.iloc[0]

            act_emp = _clean_str(row.get("Assigned_Employee", ""))
            exp_emp = _clean_str(exp["expected_assigned_employee"])
            if act_emp.lower() != exp_emp.lower():
                raise RuntimeError(
                    f"Write verification failed for task '{tid}': "
                    f"Assigned_Employee expected '{exp_emp}', got '{act_emp}'."
                )

            exp_emp_id = _normalize_id(exp.get("expected_assigned_employee_id"))
            if exp_emp_id or "Assigned_Employee_ID" in df.columns:
                act_emp_id = _normalize_id(row.get("Assigned_Employee_ID"))
                if act_emp_id != exp_emp_id:
                    raise RuntimeError(
                        f"Write verification failed for task '{tid}': "
                        f"Assigned_Employee_ID expected '{exp_emp_id}', got '{act_emp_id}'."
                    )

            act_status = _clean_str(row.get("task_status", "")).lower()
            exp_status = _clean_str(exp["expected_task_status"]).lower()
            if act_status != exp_status:
                raise RuntimeError(
                    f"Write verification failed for task '{tid}': "
                    f"task_status expected '{exp_status}', got '{act_status}'."
                )

            act_att = _clean_str(row.get("allocation_attempt_id", ""))
            exp_att = _clean_str(exp.get("expected_attempt_id", ""))
            if act_att != exp_att:
                raise RuntimeError(
                    f"Write verification failed for task '{tid}': "
                    f"allocation_attempt_id expected '{exp_att}', got '{act_att}'."
                )

    # ------------------------------------------------------------------
    # Employee & Task Validation
    # ------------------------------------------------------------------

    def _validate_and_load_employees(self) -> Tuple[pd.DataFrame, Optional[str]]:
        """
        Load employees.xlsx and validate required columns, numeric hour values,
        and employee identity uniqueness.
        """
        if not self.backend.exists("employees"):
            raise FileNotFoundError("employees dataset not found")

        df = self.backend.read("employees")

        required_cols = ["Role", "Free_Hours", "Allocated_Hours", "Employee_Name"]
        if "Allocated_Hours" not in df.columns and "Allocated_Hour" in df.columns:
            df = df.rename(columns={"Allocated_Hour": "Allocated_Hours"})

        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required column '{col}' in employees data")

        df["Free_Hours"] = pd.to_numeric(df["Free_Hours"], errors="coerce")
        df["Allocated_Hours"] = pd.to_numeric(df["Allocated_Hours"], errors="coerce")
        if df["Free_Hours"].isna().any() or (df["Free_Hours"] < 0).any():
            raise ValueError("Invalid, negative, or NaN values found in Free_Hours")
        if df["Allocated_Hours"].isna().any() or (df["Allocated_Hours"] < 0).any():
            raise ValueError("Invalid, negative, or NaN values found in Allocated_Hours")

        if "Current_Project" not in df.columns:
            df["Current_Project"] = ""
        else:
            df["Current_Project"] = df["Current_Project"].astype(object)

        id_col = _find_employee_id_col(df)

        if id_col and id_col in df.columns:
            non_empty_ids = df[id_col].dropna().apply(_normalize_id)
            non_empty_ids = non_empty_ids[non_empty_ids != ""]
            dups = non_empty_ids[non_empty_ids.duplicated()].unique()
            if len(dups) > 0:
                raise ValueError(f"Ambiguous duplicate Employee_ID '{dups[0]}' found in employees table")

        name_series = df["Employee_Name"].astype(str).str.strip().str.lower()
        if id_col and id_col in df.columns:
            no_id_mask = df[id_col].isna() | (df[id_col].astype(str).str.strip() == "")
        else:
            no_id_mask = pd.Series(True, index=df.index)

        no_id_names = name_series[no_id_mask]
        dups_name = no_id_names[no_id_names.duplicated()].unique()
        if len(dups_name) > 0:
            raise ValueError(f"Ambiguous duplicate Employee_Name '{dups_name[0]}' found without unique Employee_ID")

        return df, id_col

    def _validate_task_ids(self, incoming_df: pd.DataFrame, existing_df: pd.DataFrame) -> None:
        """Validate that task_id column is present and all task IDs are non-empty and unique."""
        if "task_id" not in incoming_df.columns:
            raise ValueError("Missing required 'task_id' column in tasks.")

        inc_ids = incoming_df["task_id"].apply(_normalize_id)
        if (inc_ids == "").any():
            raise ValueError("All tasks must have a non-empty 'task_id'.")
        dup_inc = inc_ids[inc_ids.duplicated()].unique()
        if len(dup_inc) > 0:
            raise ValueError(f"Duplicate task_id '{dup_inc[0]}' found in incoming tasks.")

        if not existing_df.empty:
            if "task_id" not in existing_df.columns:
                raise ValueError("Existing assignments table is missing required 'task_id' column.")
            ex_ids = existing_df["task_id"].apply(_normalize_id)
            if (ex_ids == "").any():
                raise ValueError("Existing assignments table contains empty 'task_id' values.")
            dup_ex = ex_ids[ex_ids.duplicated()].unique()
            if len(dup_ex) > 0:
                raise ValueError(f"Duplicate task_id '{dup_ex[0]}' found in existing assignments table.")

    # ------------------------------------------------------------------
    # Newer Committed Conflict Detection
    # ------------------------------------------------------------------

    def _check_newer_committed_conflicts(self, tx: Dict[str, Any]) -> Optional[str]:
        """
        Check if any other COMMITTED transaction created at or after *tx* affects
        the same employees or project tasks. Returns conflict description or None.

        Employee identity is compared using a canonical set that merges both
        stable-ID keys (e.g. ``"E001"``) and legacy name keys (``"name:alice"``)
        for each transaction, so a cross-format match (one tx uses an ID, another
        a name for the same person) is detected correctly.
        """
        journal_df = self._load_journal()
        if journal_df.empty:
            return None

        target_proj = str(tx["project_name"])
        target_att = str(tx["attempt_id"])
        target_created = str(tx.get("created_at", ""))
        details = tx["details"]
        target_task_ids = {_normalize_id(t.get("task_id")) for t in details.get("tasks", [])}

        # Build canonical employee set for the target transaction.
        target_canon_emps = _canonical_emp_set(details.get("employees", {}))

        # Find row index of target tx for tie-breaking identical timestamps
        target_mask = (
            (journal_df["project_name"].astype(str) == target_proj)
            & (journal_df["attempt_id"].astype(str) == target_att)
        )
        target_idx = journal_df[target_mask].index[0] if target_mask.any() else -1

        committed_df = journal_df[journal_df["state"].astype(str) == STATE_COMMITTED]
        for idx, row in committed_df.iterrows():
            r_proj = str(row["project_name"])
            r_att = str(row["attempt_id"])
            if r_proj == target_proj and r_att == target_att:
                continue

            r_created = str(row.get("created_at", ""))
            is_newer = (r_created > target_created) or (r_created == target_created and idx > target_idx)
            if not is_newer:
                continue

            try:
                r_details = _parse_and_validate_details(row.get("details_json"))
            except ValueError as exc:
                return f"Newer committed transaction ({r_proj}, '{r_att}') has corrupted details: {exc}"

            r_canon_emps = _canonical_emp_set(r_details.get("employees", {}))
            emp_overlap = target_canon_emps & r_canon_emps
            if emp_overlap:
                return (
                    f"Newer committed transaction ({r_proj}, '{r_att}') modified affected employee(s) "
                    f"{sorted(emp_overlap)}."
                )

            if r_proj == target_proj:
                r_task_ids = {_normalize_id(t.get("task_id")) for t in r_details.get("tasks", [])}
                task_overlap = target_task_ids & r_task_ids
                if task_overlap:
                    return (
                        f"Newer committed transaction ({r_proj}, '{r_att}') modified affected task(s) "
                        f"{sorted(task_overlap)}."
                    )

        return None

    # ------------------------------------------------------------------
    # Unfinished Transaction Recovery
    # ------------------------------------------------------------------

    def check_and_recover_unfinished(self) -> None:
        """
        Inspect the journal for incomplete transactions and attempt recovery.
        Stops if any transaction is in NEEDS_REVIEW or if recovery cannot safely complete.
        """
        journal_df = self._load_journal()
        if journal_df.empty or "state" not in journal_df.columns:
            return

        review_mask = journal_df["state"].astype(str) == STATE_NEEDS_REVIEW
        if review_mask.any():
            r_row = journal_df[review_mask].iloc[0]
            raise RuntimeError(
                f"Cannot proceed: transaction ({r_row['project_name']}, '{r_row['attempt_id']}') "
                "requires manual review (NEEDS_REVIEW). Operation halted to protect data integrity."
            )

        incomplete_mask = journal_df["state"].astype(str).isin(ALL_INCOMPLETE_STATES)
        if not incomplete_mask.any():
            return

        incomplete_records = journal_df[incomplete_mask]
        for _, row in incomplete_records.iterrows():
            proj = str(row["project_name"]).strip()
            att = str(row["attempt_id"]).strip()
            print(f"⚠️ Found incomplete transaction ({proj}, '{att}') in state '{row['state']}'. Initiating recovery.")
            res = self.recover_transaction(att, project_name=proj)
            if res.get("state") == STATE_NEEDS_REVIEW:
                raise RuntimeError(
                    f"Recovery for transaction ({proj}, '{att}') halted with NEEDS_REVIEW: {res.get('notes')}"
                )

    def _mark_needs_review(self, project_name: str, attempt_id: str, reason: str) -> Dict[str, Any]:
        """Helper to transition a transaction to NEEDS_REVIEW and return standard structure."""
        self._update_transaction_state(project_name, attempt_id, STATE_NEEDS_REVIEW, notes=reason)
        return {
            "status": "needs_review",
            "attempt_id": attempt_id,
            "project_name": project_name,
            "state": STATE_NEEDS_REVIEW,
            "notes": reason,
        }

    def recover_transaction(
        self,
        attempt_id: str,
        project_name: Optional[str] = None,
        extra_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Recover an incomplete transaction or execute a requested rollback.
        Inspects actual file contents against recorded original and intended states.
        """
        tx = self._get_transaction(attempt_id, project_name=project_name)
        if not tx:
            return {"status": "not_found", "attempt_id": attempt_id, "project_name": project_name}

        proj = str(tx["project_name"])
        state = tx.get("state")

        if tx.get("corrupted"):
            reason = f"Corrupted journal details: {tx.get('corruption_error')}"
            return self._mark_needs_review(proj, attempt_id, reason)

        if state in {STATE_COMMITTED, STATE_ROLLED_BACK}:
            return {"status": "completed", "attempt_id": attempt_id, "project_name": proj, "state": state}

        if state == STATE_NEEDS_REVIEW:
            return {
                "status": "needs_review",
                "attempt_id": attempt_id,
                "project_name": proj,
                "state": STATE_NEEDS_REVIEW,
                "notes": tx.get("notes", ""),
            }

        assigned_table = f"{proj}_Assigned"
        details = tx["details"]
        affected_employees = details["employees"]
        affected_tasks = details["tasks"]

        # Capture the original state before any transition.  Used in Step 3 to
        # decide whether a missing newly-inserted task is safe to skip (only
        # when allocation never advanced past PREPARED, so task files were never
        # touched) or must produce NEEDS_REVIEW (when EMPLOYEES_APPLIED or later
        # was reached, meaning assignment writes may have occurred).
        original_state = state

        # Step 1: Advance to ROLLBACK_REQUESTED if in incomplete allocation state
        if state in INCOMPLETE_ALLOCATION_STATES:
            note_msg = "Recovery initiated: rolling back incomplete allocation."
            if extra_notes:
                note_msg = f"{extra_notes} | {note_msg}"
            self._update_transaction_state(proj, attempt_id, STATE_ROLLBACK_REQUESTED, notes=note_msg)
            state = STATE_ROLLBACK_REQUESTED

        # Step 2: Restore Employees if in ROLLBACK_REQUESTED
        if state == STATE_ROLLBACK_REQUESTED:
            if affected_employees:
                if not self.backend.exists("employees"):
                    return self._mark_needs_review(
                        proj, attempt_id, "Recovery failed: employees table is missing."
                    )

                emp_df = self.backend.read("employees")
                if "Allocated_Hours" not in emp_df.columns and "Allocated_Hour" in emp_df.columns:
                    emp_df = emp_df.rename(columns={"Allocated_Hour": "Allocated_Hours"})
                id_col = _find_employee_id_col(emp_df)
                modified = False

                for emp_key, emp_info in affected_employees.items():
                    emp_id = emp_info.get("employee_id")
                    emp_name = emp_info.get("employee_name")

                    row_idx, err = self._find_unique_employee_index(emp_df, emp_id, emp_name, id_col)
                    if err:
                        return self._mark_needs_review(proj, attempt_id, f"Recovery failed: {err}")

                    act_free = float(pd.to_numeric(emp_df.at[row_idx, "Free_Hours"], errors="coerce"))
                    act_alloc = float(pd.to_numeric(emp_df.at[row_idx, "Allocated_Hours"], errors="coerce"))
                    act_proj = _clean_str(emp_df.at[row_idx, "Current_Project"])

                    orig_free = float(emp_info["original_free_hours"])
                    orig_alloc = float(emp_info["original_allocated_hours"])
                    orig_proj = _clean_str(emp_info["original_current_project"])

                    int_free = float(emp_info["intended_free_hours"])
                    int_alloc = float(emp_info["intended_allocated_hours"])
                    int_proj = _clean_str(emp_info["intended_current_project"])

                    match_orig = (
                        not math.isnan(act_free) and not math.isnan(act_alloc)
                        and abs(act_free - orig_free) < 1e-4
                        and abs(act_alloc - orig_alloc) < 1e-4
                        and act_proj == orig_proj
                    )
                    match_int = (
                        not math.isnan(act_free) and not math.isnan(act_alloc)
                        and abs(act_free - int_free) < 1e-4
                        and abs(act_alloc - int_alloc) < 1e-4
                        and act_proj == int_proj
                    )

                    if match_int:
                        emp_df.at[row_idx, "Free_Hours"] = orig_free
                        emp_df.at[row_idx, "Allocated_Hours"] = orig_alloc
                        emp_df.at[row_idx, "Current_Project"] = orig_proj
                        modified = True
                    elif match_orig:
                        pass
                    else:
                        return self._mark_needs_review(
                            proj,
                            attempt_id,
                            f"Recovery failed: employee '{emp_key}' actual values (Free:{act_free}, "
                            f"Alloc:{act_alloc}, Proj:'{act_proj}') matched neither original nor intended.",
                        )

                if modified:
                    self.backend.write("employees", emp_df)
                    expected_verif = {
                        k: {
                            "employee_id": v.get("employee_id"),
                            "employee_name": v.get("employee_name"),
                            "expected_free_hours": v["original_free_hours"],
                            "expected_allocated_hours": v["original_allocated_hours"],
                            "expected_current_project": v["original_current_project"],
                        }
                        for k, v in affected_employees.items()
                    }
                    self._verify_employees_write(expected_verif)

            self._update_transaction_state(proj, attempt_id, STATE_EMPLOYEES_RESTORED)
            state = STATE_EMPLOYEES_RESTORED

        # Step 3: Restore Assignments if in EMPLOYEES_RESTORED
        if state == STATE_EMPLOYEES_RESTORED:
            if affected_tasks:
                if not self.backend.exists(assigned_table):
                    # If allocation never reached ASSIGNMENTS_APPLIED and tasks didn't exist originally
                    all_new = all(not t.get("existed_originally", True) for t in affected_tasks)
                    if all_new:
                        self._update_transaction_state(proj, attempt_id, STATE_ASSIGNMENTS_RESTORED)
                        state = STATE_ASSIGNMENTS_RESTORED
                    else:
                        return self._mark_needs_review(
                            proj, attempt_id, f"Recovery failed: {assigned_table} is missing."
                        )
                else:
                    tasks_df = self.backend.read(assigned_table)
                    if tasks_df.empty or "task_id" not in tasks_df.columns:
                        return self._mark_needs_review(
                            proj, attempt_id, f"Recovery failed: {assigned_table} is empty or missing task_id."
                        )

                    norm_tids = tasks_df["task_id"].apply(_normalize_id)
                    modified = False
                    tasks_to_verify: List[Dict[str, Any]] = []

                    for task_info in affected_tasks:
                        tid = _normalize_id(task_info["task_id"])
                        matches = tasks_df[norm_tids == tid]
                        existed_orig = task_info.get("existed_originally", True)

                        if matches.empty:
                            if not existed_orig and original_state == STATE_PREPARED:
                                # Safe to skip: allocation was recorded as PREPARED but
                                # never advanced to EMPLOYEES_APPLIED, so assignment
                                # files were never written. The task row was never
                                # inserted, so its absence is expected.
                                continue
                            # For any other state (EMPLOYEES_APPLIED, ASSIGNMENTS_APPLIED,
                            # ROLLBACK_REQUESTED, EMPLOYEES_RESTORED …) we cannot confirm
                            # that the assignment was never applied.  Treat conservatively.
                            return self._mark_needs_review(
                                proj,
                                attempt_id,
                                f"Recovery failed: {'newly inserted' if not existed_orig else 'affected'} task "
                                f"'{tid}' missing from {assigned_table}; cannot confirm allocation was not applied "
                                f"(original state was '{original_state}').",
                            )
                        if len(matches) > 1:
                            return self._mark_needs_review(
                                proj, attempt_id, f"Recovery failed: duplicate task_id '{tid}' in {assigned_table}."
                            )

                        row_idx = matches.index[0]
                        act_emp = _clean_str(tasks_df.at[row_idx, "Assigned_Employee"])
                        act_emp_id = _normalize_id(tasks_df.at[row_idx, "Assigned_Employee_ID"]) if "Assigned_Employee_ID" in tasks_df.columns else ""
                        act_status = _clean_str(tasks_df.at[row_idx, "task_status"]).lower()
                        act_att = _clean_str(tasks_df.at[row_idx, "allocation_attempt_id"]) if "allocation_attempt_id" in tasks_df.columns else ""

                        orig_emp = _clean_str(task_info.get("original_assigned_employee") or "No Resource Available")
                        orig_emp_id = _normalize_id(task_info.get("original_assigned_employee_id"))
                        orig_status = _clean_str(task_info.get("original_task_status") or "waiting_resource").lower()
                        orig_att = _clean_str(task_info.get("original_allocation_attempt_id"))

                        int_emp = _clean_str(task_info["intended_assigned_employee"])
                        int_emp_id = _normalize_id(task_info.get("intended_assigned_employee_id"))
                        int_status = _clean_str(task_info["intended_task_status"]).lower()
                        int_att = _clean_str(task_info["intended_allocation_attempt_id"])

                        match_int = (
                            act_emp.lower() == int_emp.lower()
                            and act_emp_id == int_emp_id
                            and act_status == int_status
                            and act_att == int_att
                        )
                        match_orig = (
                            act_emp.lower() == orig_emp.lower()
                            and act_emp_id == orig_emp_id
                            and act_status == orig_status
                            and act_att == orig_att
                        )

                        if match_int:
                            if not existed_orig and orig_att == "" and orig_emp.lower() == "no resource available":
                                # Restore to original unassigned/waiting state on the row
                                tasks_df.at[row_idx, "Assigned_Employee"] = orig_emp
                                if "Assigned_Employee_ID" in tasks_df.columns:
                                    tasks_df.at[row_idx, "Assigned_Employee_ID"] = None
                                tasks_df.at[row_idx, "task_status"] = orig_status
                                tasks_df.at[row_idx, "allocation_attempt_id"] = None
                                modified = True
                            else:
                                tasks_df.at[row_idx, "Assigned_Employee"] = orig_emp
                                if "Assigned_Employee_ID" in tasks_df.columns:
                                    tasks_df.at[row_idx, "Assigned_Employee_ID"] = orig_emp_id if orig_emp_id else None
                                tasks_df.at[row_idx, "task_status"] = orig_status
                                tasks_df.at[row_idx, "allocation_attempt_id"] = orig_att if orig_att else None
                                modified = True

                            tasks_to_verify.append({
                                "task_id": tid,
                                "expected_assigned_employee": orig_emp,
                                "expected_assigned_employee_id": orig_emp_id if orig_emp_id else None,
                                "expected_task_status": orig_status,
                                "expected_attempt_id": orig_att if orig_att else None,
                            })
                        elif match_orig:
                            tasks_to_verify.append({
                                "task_id": tid,
                                "expected_assigned_employee": orig_emp,
                                "expected_assigned_employee_id": orig_emp_id if orig_emp_id else None,
                                "expected_task_status": orig_status,
                                "expected_attempt_id": orig_att if orig_att else None,
                            })
                        else:
                            return self._mark_needs_review(
                                proj,
                                attempt_id,
                                f"Recovery failed: task '{tid}' actual state (Emp:'{act_emp}', "
                                f"ID:'{act_emp_id}', Status:'{act_status}', Att:'{act_att}') "
                                "matched neither original nor intended.",
                            )

                    if modified:
                        self.backend.write(assigned_table, tasks_df)
                    if tasks_to_verify:
                        self._verify_assignments_write(assigned_table, tasks_to_verify)

            if state == STATE_EMPLOYEES_RESTORED:
                self._update_transaction_state(proj, attempt_id, STATE_ASSIGNMENTS_RESTORED)
                state = STATE_ASSIGNMENTS_RESTORED

        # Step 4: Finalize to ROLLED_BACK
        if state == STATE_ASSIGNMENTS_RESTORED:
            final_note = "Recovery completed: transaction safely rolled back."
            if extra_notes:
                final_note = f"{extra_notes} | {final_note}"
            self._update_transaction_state(proj, attempt_id, STATE_ROLLED_BACK, notes=final_note)
            state = STATE_ROLLED_BACK

        return {"status": "recovered", "attempt_id": attempt_id, "project_name": proj, "state": STATE_ROLLED_BACK}

    # ------------------------------------------------------------------
    # Allocation Operation
    # ------------------------------------------------------------------

    def allocate(
        self,
        project_name: str,
        tasks: List[Dict[str, Any]],
        attempt_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Coordinate incremental resource assignment under transaction processor.
        """
        if not tasks:
            return {"error": "No tasks provided"}

        incoming_df = pd.DataFrame(tasks)
        if "assigned_role" not in incoming_df.columns:
            return {"error": "Missing assigned_role column"}

        if attempt_id is not None and str(attempt_id).strip():
            attempt_id = str(attempt_id).strip()
        else:
            attempt_id = f"att_{uuid.uuid4().hex[:12]}"

        assigned_table = f"{project_name}_Assigned"

        with self.backend.transaction(JOURNAL_TABLE):
            with self.backend.transaction("employees"):
                with self.backend.transaction(assigned_table):
                    # Check and recover any prior unfinished transactions before accepting new operation
                    self.check_and_recover_unfinished()

                    # Check for duplicate attempt ID in this project
                    existing_tx = self._get_transaction(attempt_id, project_name)
                    if existing_tx:
                        if existing_tx.get("corrupted"):
                            raise RuntimeError(
                                f"Existing journal record for ({project_name}, '{attempt_id}') is corrupted: "
                                f"{existing_tx.get('corruption_error')}"
                            )
                        tx_state = existing_tx.get("state")
                        if tx_state == STATE_COMMITTED:
                            assigned_df = self.backend.read(assigned_table) if self.backend.exists(assigned_table) else pd.DataFrame()
                            resource_blocked = any(
                                _clean_str(r.get("task_status", "")).lower() == "waiting_resource"
                                for r in assigned_df.to_dict(orient="records")
                            ) if not assigned_df.empty else False
                            total_hours = sum(
                                float(emp.get("hours_delta", 0.0))
                                for emp in existing_tx["details"]["employees"].values()
                            )
                            return {
                                "project_name": project_name,
                                "attempt_id": attempt_id,
                                "tasks": assigned_df.to_dict(orient="records"),
                                "resource_blocked": resource_blocked,
                                "allocated_hours": total_hours,
                            }
                        elif tx_state == STATE_ROLLED_BACK:
                            raise ValueError(
                                f"Attempt ID '{attempt_id}' for project '{project_name}' has already been rolled back. "
                                "Please use a unique attempt ID."
                            )
                        elif tx_state == STATE_NEEDS_REVIEW:
                            raise RuntimeError(
                                f"Attempt ID '{attempt_id}' for project '{project_name}' is marked NEEDS_REVIEW. "
                                "Manual review required."
                            )

                    # 1. Load and validate employees
                    employees_df, id_col = self._validate_and_load_employees()

                    # 2. Load existing assignments, validate task IDs, and merge
                    existing_assigned_df = _load_existing_assignments(self.backend, project_name)
                    self._validate_task_ids(incoming_df, existing_assigned_df)

                    existing_task_ids = (
                        set(existing_assigned_df["task_id"].apply(_normalize_id))
                        if not existing_assigned_df.empty and "task_id" in existing_assigned_df.columns
                        else set()
                    )

                    tasks_df = _merge_tasks(existing_assigned_df, tasks)

                    if "allocation_attempt_id" not in tasks_df.columns:
                        tasks_df["allocation_attempt_id"] = None

                    # 3. Load configuration
                    config = load_config()
                    ROLE_MAP = config.get("role_map", {})
                    working_hours = _get_working_hours_per_day(config)

                    # 4. Compute resource assignment and record original/intended states
                    affected_employees: Dict[str, Dict[str, Any]] = {}
                    affected_tasks: List[Dict[str, Any]] = []
                    total_allocated_hours = 0.0

                    for i in range(len(tasks_df)):
                        row = tasks_df.iloc[i]
                        if not _is_unassigned(row):
                            continue

                        task_id_val = row.get("task_id")
                        norm_tid = _normalize_id(task_id_val)
                        existed_orig = norm_tid in existing_task_ids

                        orig_assigned_emp = _clean_str(row.get("Assigned_Employee")) or "No Resource Available"
                        orig_assigned_id = _normalize_id(row.get("Assigned_Employee_ID")) if "Assigned_Employee_ID" in tasks_df.columns else ""
                        orig_task_stat = _clean_str(row.get("task_status")) or "waiting_resource"
                        orig_att_id = _clean_str(row.get("allocation_attempt_id"))

                        raw_time = row.get("time_days") if "time_days" in tasks_df.columns else None
                        valid_days = _validate_duration(raw_time)
                        raw_role = str(row.get("assigned_role", "")).strip()
                        first_role = raw_role.split(",")[0].strip()
                        target_role = ROLE_MAP.get(first_role, first_role)

                        if valid_days is None:
                            tasks_df.at[i, "Assigned_Employee"] = "No Resource Available"
                            if "Assigned_Employee_ID" in tasks_df.columns:
                                tasks_df.at[i, "Assigned_Employee_ID"] = None
                            tasks_df.at[i, "task_status"] = "waiting_resource"
                            tasks_df.at[i, "allocation_attempt_id"] = None

                            affected_tasks.append({
                                "task_id": task_id_val,
                                "existed_originally": existed_orig,
                                "assigned_role": target_role,
                                "time_days": None,
                                "hours": 0.0,
                                "original_assigned_employee": orig_assigned_emp,
                                "original_assigned_employee_id": orig_assigned_id or None,
                                "original_task_status": orig_task_stat,
                                "original_allocation_attempt_id": orig_att_id or None,
                                "intended_assigned_employee": "No Resource Available",
                                "intended_assigned_employee_id": None,
                                "intended_task_status": "waiting_resource",
                                "intended_allocation_attempt_id": None,
                            })
                            continue

                        hours_needed = valid_days * working_hours
                        tasks_df.at[i, "time_days"] = valid_days

                        candidates = employees_df[
                            (employees_df["Role"].astype(str).str.strip().str.lower() == target_role.strip().lower())
                            & (employees_df["Free_Hours"] >= hours_needed)
                        ]

                        if not candidates.empty:
                            emp_idx = candidates["Allocated_Hours"].idxmin()
                            emp_name = employees_df.loc[emp_idx, "Employee_Name"]
                            emp_id_val = _normalize_id(employees_df.loc[emp_idx, id_col]) if id_col else ""
                            emp_key = emp_id_val if emp_id_val else f"name:{str(emp_name).strip().lower()}"

                            if emp_key not in affected_employees:
                                orig_free = float(employees_df.at[emp_idx, "Free_Hours"])
                                orig_alloc = float(employees_df.at[emp_idx, "Allocated_Hours"])
                                orig_proj = _clean_str(employees_df.at[emp_idx, "Current_Project"])
                                affected_employees[emp_key] = {
                                    "employee_key": emp_key,
                                    "employee_id": emp_id_val or None,
                                    "employee_name": emp_name,
                                    "hours_delta": 0.0,
                                    "original_free_hours": orig_free,
                                    "intended_free_hours": orig_free,
                                    "original_allocated_hours": orig_alloc,
                                    "intended_allocated_hours": orig_alloc,
                                    "original_current_project": orig_proj,
                                    "intended_current_project": orig_proj,
                                }

                            tasks_df.at[i, "Assigned_Employee"] = emp_name
                            if id_col:
                                tasks_df.at[i, "Assigned_Employee_ID"] = emp_id_val if emp_id_val else None
                            tasks_df.at[i, "task_status"] = "assigned"
                            tasks_df.at[i, "allocation_attempt_id"] = attempt_id

                            employees_df.at[emp_idx, "Allocated_Hours"] += hours_needed
                            employees_df.at[emp_idx, "Free_Hours"] -= hours_needed
                            new_proj = _update_current_project_add(employees_df.at[emp_idx, "Current_Project"], project_name)
                            employees_df.at[emp_idx, "Current_Project"] = new_proj

                            affected_employees[emp_key]["hours_delta"] += hours_needed
                            affected_employees[emp_key]["intended_free_hours"] -= hours_needed
                            affected_employees[emp_key]["intended_allocated_hours"] += hours_needed
                            affected_employees[emp_key]["intended_current_project"] = new_proj

                            total_allocated_hours += hours_needed

                            affected_tasks.append({
                                "task_id": task_id_val,
                                "existed_originally": existed_orig,
                                "assigned_role": target_role,
                                "time_days": valid_days,
                                "hours": hours_needed,
                                "original_assigned_employee": orig_assigned_emp,
                                "original_assigned_employee_id": orig_assigned_id or None,
                                "original_task_status": orig_task_stat,
                                "original_allocation_attempt_id": orig_att_id or None,
                                "intended_assigned_employee": emp_name,
                                "intended_assigned_employee_id": emp_id_val if emp_id_val else None,
                                "intended_task_status": "assigned",
                                "intended_allocation_attempt_id": attempt_id,
                            })

                        else:
                            tasks_df.at[i, "Assigned_Employee"] = "No Resource Available"
                            if "Assigned_Employee_ID" in tasks_df.columns:
                                tasks_df.at[i, "Assigned_Employee_ID"] = None
                            tasks_df.at[i, "task_status"] = "waiting_resource"
                            tasks_df.at[i, "allocation_attempt_id"] = None

                            affected_tasks.append({
                                "task_id": task_id_val,
                                "existed_originally": existed_orig,
                                "assigned_role": target_role,
                                "time_days": valid_days,
                                "hours": 0.0,
                                "original_assigned_employee": orig_assigned_emp,
                                "original_assigned_employee_id": orig_assigned_id or None,
                                "original_task_status": orig_task_stat,
                                "original_allocation_attempt_id": orig_att_id or None,
                                "intended_assigned_employee": "No Resource Available",
                                "intended_assigned_employee_id": None,
                                "intended_task_status": "waiting_resource",
                                "intended_allocation_attempt_id": None,
                            })

                    tasks_df = tasks_df.where(pd.notnull(tasks_df), None)

                    # If no changes at all (all tasks already assigned and no new tasks), return directly
                    if not affected_employees and not affected_tasks:
                        resource_blocked = any(
                            _clean_str(row.get("task_status", "")) == "waiting_resource"
                            for row in tasks_df.to_dict(orient="records")
                        )
                        return {
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "tasks": tasks_df.to_dict(orient="records"),
                            "resource_blocked": resource_blocked,
                            "allocated_hours": 0.0,
                        }

                    # ── STAGE 1: Record PREPARED transaction in journal ───────
                    tx_record = {
                        "attempt_id": attempt_id,
                        "project_name": project_name,
                        "transaction_type": "ALLOCATION",
                        "state": STATE_PREPARED,
                        "details": {
                            "employees": affected_employees,
                            "tasks": affected_tasks,
                        },
                        "notes": "Transaction prepared; awaiting application.",
                    }
                    self._record_transaction(tx_record)

                    # Stages 2–4 protected: if failure occurs after PREPARED, attempt immediate recovery
                    try:
                        # ── STAGE 2: Write employees (if affected) and advance to EMPLOYEES_APPLIED
                        if affected_employees:
                            self.backend.write("employees", employees_df)
                            expected_employees_verif = {
                                k: {
                                    "employee_id": v.get("employee_id"),
                                    "employee_name": v.get("employee_name"),
                                    "expected_free_hours": v["intended_free_hours"],
                                    "expected_allocated_hours": v["intended_allocated_hours"],
                                    "expected_current_project": v["intended_current_project"],
                                }
                                for k, v in affected_employees.items()
                            }
                            self._verify_employees_write(expected_employees_verif)
                        self._update_transaction_state(project_name, attempt_id, STATE_EMPLOYEES_APPLIED)

                        # ── STAGE 3: Write assignments and advance to ASSIGNMENTS_APPLIED
                        self.backend.write(assigned_table, tasks_df)
                        expected_tasks_verif = [
                            {
                                "task_id": t["task_id"],
                                "expected_assigned_employee": t["intended_assigned_employee"],
                                "expected_assigned_employee_id": t["intended_assigned_employee_id"],
                                "expected_task_status": t["intended_task_status"],
                                "expected_attempt_id": t["intended_allocation_attempt_id"],
                            }
                            for t in affected_tasks
                        ]
                        self._verify_assignments_write(assigned_table, expected_tasks_verif)
                        self._update_transaction_state(project_name, attempt_id, STATE_ASSIGNMENTS_APPLIED)

                        # ── STAGE 4: Finalize to COMMITTED ────────────────────
                        self._update_transaction_state(
                            project_name,
                            attempt_id,
                            STATE_COMMITTED,
                            notes="Allocation committed successfully.",
                        )
                    except Exception as exc:
                        failure_note = f"Allocation failed after PREPARED: {exc}"
                        try:
                            rec_res = self.recover_transaction(
                                attempt_id,
                                project_name=project_name,
                                extra_notes=failure_note,
                            )
                            if rec_res.get("state") == STATE_NEEDS_REVIEW:
                                raise RuntimeError(
                                    f"{failure_note}; immediate recovery stopped with NEEDS_REVIEW: {rec_res.get('notes')}"
                                ) from exc
                        except Exception as rec_exc:
                            if rec_exc is exc:
                                raise
                            raise RuntimeError(
                                f"{failure_note}; immediate recovery also failed: {rec_exc}"
                            ) from exc
                        raise

                    resource_blocked = any(
                        _clean_str(row.get("task_status", "")) == "waiting_resource"
                        for row in tasks_df.to_dict(orient="records")
                    )

                    return {
                        "project_name": project_name,
                        "attempt_id": attempt_id,
                        "tasks": tasks_df.to_dict(orient="records"),
                        "resource_blocked": resource_blocked,
                        "allocated_hours": float(total_allocated_hours),
                    }

    # ------------------------------------------------------------------
    # Rollback Operation
    # ------------------------------------------------------------------

    def rollback(self, project_name: str, attempt_id: str) -> Dict[str, Any]:
        """
        Coordinate idempotent rollback under transaction processor.
        """
        if not attempt_id or not str(attempt_id).strip():
            return {
                "status": "error",
                "message": "Valid attempt_id required for rollback",
                "project_name": project_name,
                "attempt_id": attempt_id,
                "rolled_back_hours": 0.0,
                "records_rolled_back": 0,
            }

        attempt_id = str(attempt_id).strip()
        project_name = str(project_name).strip()
        assigned_table = f"{project_name}_Assigned"

        with self.backend.transaction(JOURNAL_TABLE):
            with self.backend.transaction("employees"):
                with self.backend.transaction(assigned_table):
                    # Check and recover any prior unfinished transactions before accepting rollback
                    self.check_and_recover_unfinished()

                    tx = self._get_transaction(attempt_id, project_name)
                    if not tx:
                        return {
                            "status": "not_found",
                            "message": f"No transaction record found for ({project_name}, '{attempt_id}')",
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    if tx.get("corrupted"):
                        res = self._mark_needs_review(
                            project_name,
                            attempt_id,
                            f"Corrupted journal details: {tx.get('corruption_error')}",
                        )
                        return {
                            "status": "error",
                            "message": res["notes"],
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    state = tx.get("state")
                    if state == STATE_ROLLED_BACK:
                        return {
                            "status": "already_rolled_back",
                            "message": f"Transaction '{attempt_id}' was already rolled back",
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    if state == STATE_NEEDS_REVIEW:
                        return {
                            "status": "error",
                            "message": f"Transaction '{attempt_id}' is marked NEEDS_REVIEW. Manual review required.",
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    # Check for newer committed transactions before rolling back
                    conflict = self._check_newer_committed_conflicts(tx)
                    if conflict:
                        reason = f"Rollback blocked to avoid overwriting newer committed changes: {conflict}"
                        self._mark_needs_review(project_name, attempt_id, reason)
                        return {
                            "status": "error",
                            "message": reason,
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    if state == STATE_COMMITTED:
                        self._update_transaction_state(
                            project_name,
                            attempt_id,
                            STATE_ROLLBACK_REQUESTED,
                            notes="Rollback requested by caller.",
                        )

                    # Execute rollback via recovery logic (ensures identical crash-safety & verification)
                    res = self.recover_transaction(attempt_id, project_name=project_name)
                    if res.get("state") != STATE_ROLLED_BACK:
                        # recover_transaction did not reach ROLLED_BACK — it may be
                        # NEEDS_REVIEW, not_found, or some other incomplete state.
                        return {
                            "status": "error",
                            "message": (
                                f"Rollback did not complete. Final state: {res.get('state')}. "
                                f"{res.get('notes', '')}"
                            ),
                            "project_name": project_name,
                            "attempt_id": attempt_id,
                            "rolled_back_hours": 0.0,
                            "records_rolled_back": 0,
                        }

                    details = tx["details"]
                    total_hours = sum(
                        float(emp.get("hours_delta", 0.0))
                        for emp in details.get("employees", {}).values()
                    )
                    count_records = len(details.get("tasks", []))

                    return {
                        "status": "rolled_back",
                        "message": f"Successfully rolled back allocations for attempt {attempt_id}",
                        "project_name": project_name,
                        "attempt_id": attempt_id,
                        "rolled_back_hours": float(total_hours),
                        "records_rolled_back": int(count_records),
                    }

    # ------------------------------------------------------------------
    # Query API Compatibility
    # ------------------------------------------------------------------

    def get_allocation_records(
        self,
        project_name: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Flatten transaction details into a DataFrame for query compatibility.
        Raises ValueError if any matching journal record has corrupted details_json.
        """
        with self.backend.transaction(JOURNAL_TABLE):
            journal_df = self._load_journal()

        if journal_df.empty:
            return pd.DataFrame(
                columns=[
                    "allocation_id",
                    "project_name",
                    "attempt_id",
                    "task_id",
                    "employee_id",
                    "employee_name",
                    "hours",
                    "status",
                    "created_at",
                    "rolled_back_at",
                    "notes",
                ]
            )

        rows = []
        for _, tx_row in journal_df.iterrows():
            proj = str(tx_row.get("project_name", ""))
            att = str(tx_row.get("attempt_id", ""))
            st = str(tx_row.get("state", ""))
            created_at = str(tx_row.get("created_at", ""))
            updated_at = str(tx_row.get("updated_at", ""))
            rolled_back_at = updated_at if st == STATE_ROLLED_BACK else None

            if project_name is not None and proj != project_name:
                continue
            if attempt_id is not None and att != attempt_id:
                continue

            details = _parse_and_validate_details(tx_row.get("details_json"))
            tasks = details["tasks"]
            for t in tasks:
                rows.append({
                    "allocation_id": f"alloc_{att}_{t.get('task_id')}",
                    "project_name": proj,
                    "attempt_id": att,
                    "task_id": t.get("task_id"),
                    "employee_id": t.get("intended_assigned_employee_id"),
                    "employee_name": t.get("intended_assigned_employee"),
                    "hours": t.get("hours", 0.0),
                    "status": st,
                    "created_at": created_at,
                    "rolled_back_at": rolled_back_at,
                    "notes": str(tx_row.get("notes", "")),
                })

        return pd.DataFrame(rows)
