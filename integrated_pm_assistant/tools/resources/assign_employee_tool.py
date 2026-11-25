"""
assign_employee_tool.py
-----------------------
Incremental resource assignment tool coordinating with the dedicated
AllocationTransactionProcessor.

Key behaviours:
  1. PRESERVE existing assignments — reads {project}_Assigned.xlsx on every run
     and keeps already-assigned rows unchanged.
  2. PROCESS ONLY waiting tasks — a task is eligible for (re-)assignment when:
       • Assigned_Employee is empty / NaN, OR
       • Assigned_Employee == "No Resource Available", OR
       • task_status == "waiting_resource"
  3. BALANCED selection — picks the employee with the LOWEST Allocated_Hours
     among eligible candidates (those with Free_Hours >= hours_needed), not the highest.
  4. SHORTAGE marking — when no candidate is found, sets task_status to
     "waiting_resource" so the ValidationAgent can detect and report it.
  5. DURATION validation — tasks with missing, zero, negative, or non-finite durations
     are marked "waiting_resource" without allocating an employee.
  6. TRANSACTIONAL DELEGATION — delegates all atomic state modifications, durability,
     verification, and crash-recovery to AllocationTransactionProcessor.
"""

from typing import List, Dict, Any, Optional
import pandas as pd
from google.adk.tools.function_tool import FunctionTool

from data_backend import get_backend
from tools.resources.allocation_transaction_processor import (
    AllocationTransactionProcessor,
    _is_unassigned,
    _validate_duration,
    _get_working_hours_per_day,
    _normalize_id,
    _find_employee_id_col,
    _load_existing_assignments,
    _merge_tasks,
)


def assign_resources(
    project_name: str,
    tasks: List[Dict[str, Any]],
    attempt_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Incrementally assign employees to tasks with durable, verified transaction processing.

    Args:
        project_name: Used to locate the existing Assigned file and to
                      update employees' Current_Project field.
        tasks:        Full task list from the TaskAgent (or LoopAgent retry).
        attempt_id:   Unique identifier for this allocation attempt. If omitted,
                      a unique ID is generated automatically.

    Returns:
        {
            "project_name": str,
            "attempt_id": str,
            "tasks": list,
            "resource_blocked": bool,
            "allocated_hours": float,
        }
    """
    processor = AllocationTransactionProcessor(backend=get_backend())
    return processor.allocate(project_name=project_name, tasks=tasks, attempt_id=attempt_id)


def rollback_allocations(
    project_name: str,
    attempt_id: str,
    backend=None,
) -> Dict[str, Any]:
    """
    Idempotently and crash-safely roll back employee-hour deductions for a specific attempt_id.

    Delegates to AllocationTransactionProcessor, which inspects actual file contents against
    recorded original and intended states and verifies each write.
    """
    processor = AllocationTransactionProcessor(backend=backend or get_backend())
    return processor.rollback(project_name=project_name, attempt_id=attempt_id)


def get_allocation_records(
    project_name: Optional[str] = None,
    attempt_id: Optional[str] = None,
    backend=None,
) -> pd.DataFrame:
    """Return allocation records from transaction journal for query compatibility."""
    processor = AllocationTransactionProcessor(backend=backend or get_backend())
    return processor.get_allocation_records(project_name=project_name, attempt_id=attempt_id)


# Register as an ADK FunctionTool
assign_employee_tool = FunctionTool(assign_resources)
