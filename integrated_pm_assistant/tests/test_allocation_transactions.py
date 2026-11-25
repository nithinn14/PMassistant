"""
test_allocation_transactions.py
================================
Tests for AllocationTransactionProcessor.

Coverage:
  1. Successful allocation — employee balances, task assignment, journal COMMITTED.
  2. Full rollback — balance restoration, Current_Project reset, task state reset,
     journal ROLLED_BACK.
  3. Repeated rollback idempotency — already_rolled_back status, no balance change.
  4. Crash-safe recovery after employee write (journal stays in PREPARED).
  5. Crash-safe recovery after assignment write (journal stays in EMPLOYEES_APPLIED).
  6. Conflict protection — newer COMMITTED transaction blocks rollback of older one,
     older marked NEEDS_REVIEW, newer allocation preserved.

Design notes
------------
* Uses an in-memory DataBackend test-double with copy-on-read/write semantics.
* load_config is patched at the module level for the duration of each test so the
  tests run without a real config.yaml or pyyaml installation in the environment.
* AllocationTransactionProcessor is constructed with backend= directly, so no
  context-var binding is needed.
* Crash injection uses WriteBombBackend, which writes data to storage *before*
  raising _Bomb (a BaseException subclass).  BaseException bypasses the
  `except Exception` handler inside allocate(), so the recovery path is exercised.
* The transaction() context-manager on InMemoryBackend is the base-class no-op
  (@contextmanager yield), which correctly propagates BaseException through
  __exit__ without suppressing it.
"""

import unittest
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pandas as pd

from data_backend.base import DataBackend
from tools.resources.allocation_transaction_processor import (
    AllocationTransactionProcessor,
    JOURNAL_TABLE,
    STATE_COMMITTED,
    STATE_EMPLOYEES_APPLIED,
    STATE_NEEDS_REVIEW,
    STATE_PREPARED,
    STATE_ROLLED_BACK,
)

# ---------------------------------------------------------------------------
# Patch target & mock config
# ---------------------------------------------------------------------------

_CONFIG_PATCH_TARGET = "tools.resources.allocation_transaction_processor.load_config"

_MOCK_CONFIG = {
    "project_defaults": {"working_hours_per_day": 8.0},
    "role_map": {},
}


# ---------------------------------------------------------------------------
# In-memory DataBackend test double
# ---------------------------------------------------------------------------


class InMemoryBackend(DataBackend):
    """
    Lightweight DataBackend backed by a plain dict of DataFrames.

    * read()  returns a deep copy so callers cannot mutate stored state.
    * write() stores a deep copy so stored state is not aliased to the caller's df.
    * transaction() is inherited as a no-op @contextmanager from DataBackend; it
      propagates BaseException correctly through __exit__.
    """

    def __init__(self, initial_data: Optional[Dict[str, pd.DataFrame]] = None):
        self.storage: Dict[str, pd.DataFrame] = {}
        if initial_data:
            for k, v in initial_data.items():
                self.storage[k] = v.copy(deep=True)

    def read(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        if table_name in self.storage:
            return self.storage[table_name].copy(deep=True)
        return pd.DataFrame()

    def write(
        self,
        table_name: str,
        df: pd.DataFrame,
        mode: str = "replace",
    ) -> None:
        self.storage[table_name] = df.copy(deep=True)

    def append(self, table_name: str, df: pd.DataFrame) -> None:
        if table_name in self.storage:
            self.storage[table_name] = pd.concat(
                [self.storage[table_name], df], ignore_index=True
            )
        else:
            self.storage[table_name] = df.copy(deep=True)

    def exists(self, table_name: str) -> bool:
        return table_name in self.storage

    def update(
        self,
        table_name: str,
        df: pd.DataFrame,
        key_columns: List[str],
    ) -> None:
        self.storage[table_name] = df.copy(deep=True)


# ---------------------------------------------------------------------------
# Crash-injection helpers
# ---------------------------------------------------------------------------


class _Bomb(BaseException):
    """
    BaseException subclass that bypasses `except Exception` in allocate().

    By subclassing BaseException (not Exception), the raise propagates through
    the `except Exception as exc:` recovery block inside AllocationTransactionProcessor
    .allocate() unimpeded, simulating a process crash (SIGKILL, KeyboardInterrupt, etc.)
    """


class WriteBombBackend(InMemoryBackend):
    """
    Writes data to storage then raises _Bomb on the Nth write call.

    Data is persisted BEFORE the bomb fires, accurately modelling a crash
    that occurs just after the OS has flushed a file write to disk.

    Parameters
    ----------
    initial_data : dict of {table_name: DataFrame}
    bomb_on : int
        Raise _Bomb immediately after the bomb_on-th call to write().
        Subsequent writes (from recovery) proceed normally.
    """

    def __init__(
        self,
        initial_data: Optional[Dict[str, pd.DataFrame]],
        bomb_on: int,
    ):
        super().__init__(initial_data)
        self._write_count = 0
        self._bomb_on = bomb_on

    def write(self, table_name: str, df: pd.DataFrame, mode: str = "replace") -> None:
        self._write_count += 1
        super().write(table_name, df, mode)          # data persisted FIRST
        if self._write_count == self._bomb_on:       # bomb fires AFTER write
            raise _Bomb(
                f"Simulated crash on write #{self._write_count} to '{table_name}'"
            )


# ---------------------------------------------------------------------------
# Employee fixture builders
# ---------------------------------------------------------------------------


def _make_two_employees() -> pd.DataFrame:
    """
    Two-employee DataFrame with stable Employee_IDs.
    Alice (E001) and Bob (E002) both start with 40 free hours.
    """
    return pd.DataFrame(
        [
            {
                "Employee_ID": "E001",
                "Employee_Name": "Alice Dev",
                "Role": "developer",
                "Free_Hours": 40.0,
                "Allocated_Hours": 0.0,
                "Current_Project": "",
            },
            {
                "Employee_ID": "E002",
                "Employee_Name": "Bob Dev",
                "Role": "developer",
                "Free_Hours": 40.0,
                "Allocated_Hours": 0.0,
                "Current_Project": "",
            },
        ]
    )


def _make_single_employee() -> pd.DataFrame:
    """
    Single-employee DataFrame.
    Used where the test must force both allocations onto the same person
    regardless of the idxmin() selection strategy.
    """
    return pd.DataFrame(
        [
            {
                "Employee_ID": "E001",
                "Employee_Name": "Alice Dev",
                "Role": "developer",
                "Free_Hours": 40.0,
                "Allocated_Hours": 0.0,
                "Current_Project": "",
            }
        ]
    )


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _get_emp(backend: InMemoryBackend, emp_id: str) -> pd.Series:
    """Return the employee row for *emp_id* or raise AssertionError."""
    df = backend.read("employees")
    rows = df[df["Employee_ID"].astype(str) == emp_id]
    if rows.empty:
        raise AssertionError(f"Employee {emp_id!r} not found in employees table")
    return rows.iloc[0]


def _journal_state(
    backend: InMemoryBackend,
    attempt_id: str,
    project: str,
) -> str:
    """Return the journal `state` for the given (project, attempt_id) pair."""
    df = backend.read(JOURNAL_TABLE)
    if df.empty:
        raise AssertionError(f"Journal table is empty; no record for ({project!r}, {attempt_id!r})")
    mask = (df["attempt_id"].astype(str) == attempt_id) & (
        df["project_name"].astype(str) == project
    )
    rows = df[mask]
    if rows.empty:
        raise AssertionError(
            f"No journal record found for ({project!r}, {attempt_id!r})"
        )
    return str(rows.iloc[0]["state"])


def _get_assigned_task(
    backend: InMemoryBackend,
    project: str,
    task_id: str,
) -> pd.Series:
    """Return the assignment row for *task_id* in *project*_Assigned."""
    table = f"{project}_Assigned"
    df = backend.read(table)
    if df.empty:
        raise AssertionError(f"Assignments table '{table}' is empty")
    rows = df[df["task_id"].astype(str) == task_id]
    if rows.empty:
        raise AssertionError(f"Task '{task_id}' not found in '{table}'")
    return rows.iloc[0]


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestAllocationTransactionProcessor(unittest.TestCase):
    """
    End-to-end tests for AllocationTransactionProcessor using an in-memory backend.
    """

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def setUp(self):
        # Patch load_config for every test; avoids yaml / config.yaml dependency.
        self._lc_patcher = patch(_CONFIG_PATCH_TARGET, return_value=_MOCK_CONFIG)
        self._lc_patcher.start()

        self.backend = InMemoryBackend({"employees": _make_two_employees()})
        self.proc = AllocationTransactionProcessor(backend=self.backend)

    def tearDown(self):
        self._lc_patcher.stop()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _task(task_id: str, days: float = 1.0, role: str = "developer") -> List[Dict]:
        return [{"task_id": task_id, "assigned_role": role, "time_days": days}]

    # ------------------------------------------------------------------
    # Test 1 — Successful allocation
    # ------------------------------------------------------------------

    def test_01_allocation_deducts_hours_assigns_task_commits_journal(self):
        """
        allocate() must:
          - deduct hours from the employee (Free_Hours -= 8, Allocated_Hours += 8),
          - add the project to Current_Project,
          - write the task with Assigned_Employee / task_status=assigned /
            allocation_attempt_id set, and
          - leave the journal in COMMITTED state.

        With two employees both starting at Allocated_Hours=0 and
        working_hours_per_day=8, a 1-day task costs 8 hours.
        idxmin() on Allocated_Hours selects Alice (index 0, the first tied row).
        """
        result = self.proc.allocate(
            project_name="Alpha",
            tasks=self._task("T-001", days=1.0),
            attempt_id="att-01",
        )

        # Return value
        self.assertFalse(result.get("resource_blocked"), "Task should be assigned")
        self.assertEqual(result["attempt_id"], "att-01")
        self.assertAlmostEqual(result["allocated_hours"], 8.0)

        # Employee balances (Alice: E001)
        alice = _get_emp(self.backend, "E001")
        self.assertAlmostEqual(alice["Free_Hours"], 32.0, places=4)   # 40 - 8
        self.assertAlmostEqual(alice["Allocated_Hours"], 8.0, places=4)
        self.assertIn("Alpha", alice["Current_Project"])

        # Bob must be untouched
        bob = _get_emp(self.backend, "E002")
        self.assertAlmostEqual(bob["Free_Hours"], 40.0, places=4)
        self.assertAlmostEqual(bob["Allocated_Hours"], 0.0, places=4)

        # Task assignment
        t = _get_assigned_task(self.backend, "Alpha", "T-001")
        self.assertEqual(t["Assigned_Employee"], "Alice Dev")
        self.assertEqual(str(t["task_status"]).lower(), "assigned")
        self.assertEqual(str(t["allocation_attempt_id"]), "att-01")

        # Journal
        self.assertEqual(
            _journal_state(self.backend, "att-01", "Alpha"),
            STATE_COMMITTED,
        )

    # ------------------------------------------------------------------
    # Test 2 — Rollback restores employee and task state
    # ------------------------------------------------------------------

    def test_02_rollback_restores_hours_project_and_task_status(self):
        """
        rollback() must:
          - restore Free_Hours and Allocated_Hours to pre-allocation values,
          - remove the project from Current_Project,
          - reset the task row to waiting_resource / No Resource Available, and
          - advance the journal to ROLLED_BACK.

        A 2-day task costs 16 hours (2 × 8), so after rollback
        Alice must be back to Free_Hours=40, Allocated_Hours=0.
        """
        self.proc.allocate(
            project_name="Beta",
            tasks=self._task("T-002", days=2.0),
            attempt_id="att-02",
        )

        # Confirm deduction before rollback
        alice_mid = _get_emp(self.backend, "E001")
        self.assertAlmostEqual(alice_mid["Free_Hours"], 24.0, places=4)   # 40 - 16
        self.assertAlmostEqual(alice_mid["Allocated_Hours"], 16.0, places=4)

        rb = self.proc.rollback(project_name="Beta", attempt_id="att-02")

        self.assertEqual(rb["status"], "rolled_back")
        self.assertAlmostEqual(rb["rolled_back_hours"], 16.0, places=4)

        # Employee balances restored
        alice_rb = _get_emp(self.backend, "E001")
        self.assertAlmostEqual(alice_rb["Free_Hours"], 40.0, places=4)
        self.assertAlmostEqual(alice_rb["Allocated_Hours"], 0.0, places=4)
        # Current_Project must no longer contain "Beta"
        self.assertNotIn("Beta", alice_rb["Current_Project"])

        # Task assignment reset
        t = _get_assigned_task(self.backend, "Beta", "T-002")
        self.assertEqual(str(t["task_status"]).lower(), "waiting_resource")
        self.assertEqual(
            str(t["Assigned_Employee"]).lower(),
            "no resource available",
        )

        # Journal
        self.assertEqual(
            _journal_state(self.backend, "att-02", "Beta"),
            STATE_ROLLED_BACK,
        )

    # ------------------------------------------------------------------
    # Test 3 — Repeated rollback is idempotent
    # ------------------------------------------------------------------

    def test_03_repeated_rollback_returns_already_rolled_back(self):
        """
        A second rollback() on a ROLLED_BACK attempt must return
        status='already_rolled_back' and must not alter employee balances.
        """
        self.proc.allocate(
            project_name="Gamma",
            tasks=self._task("T-003", days=1.0),
            attempt_id="att-03",
        )
        first = self.proc.rollback(project_name="Gamma", attempt_id="att-03")
        self.assertEqual(first["status"], "rolled_back")

        alice_after_first = _get_emp(self.backend, "E001")
        self.assertAlmostEqual(alice_after_first["Free_Hours"], 40.0, places=4)

        # Second rollback
        second = self.proc.rollback(project_name="Gamma", attempt_id="att-03")
        self.assertEqual(second["status"], "already_rolled_back")

        # Balances must be identical — no double-restoration
        alice_after_second = _get_emp(self.backend, "E001")
        self.assertAlmostEqual(alice_after_second["Free_Hours"], 40.0, places=4)
        self.assertAlmostEqual(alice_after_second["Allocated_Hours"], 0.0, places=4)

    # ------------------------------------------------------------------
    # Test 4 — Crash after employee write (journal in PREPARED)
    # ------------------------------------------------------------------

    def test_04_crash_after_employee_write_recovers_via_check_and_recover(self):
        """
        Crash scenario: process dies after employees.xlsx is written (write #2)
        but before the journal advances to EMPLOYEES_APPLIED (write #3).

        Write sequence inside allocate() for one employee / one task:
          #1  journal PREPARED
          #2  employees                    ← _Bomb fires here (data already persisted)
          #3  journal EMPLOYEES_APPLIED    ← never reached
          #4  assignments                  ← never reached

        A fresh processor on the same backend storage must detect the PREPARED
        journal record, recognise that employees were written (actual values match
        intended values), and restore them to their original state.
        Recovery must leave the journal in ROLLED_BACK.
        """
        ATTEMPT_ID = "att-04-crash-emp"
        PROJECT = "Delta"

        bomb_backend = WriteBombBackend(
            initial_data={"employees": _make_two_employees()},
            bomb_on=2,   # crash immediately after employees write
        )
        proc_crash = AllocationTransactionProcessor(backend=bomb_backend)

        # The _Bomb bypasses `except Exception` inside allocate()
        with self.assertRaises(_Bomb):
            proc_crash.allocate(
                project_name=PROJECT,
                tasks=self._task("T-004", days=1.0),
                attempt_id=ATTEMPT_ID,
            )

        # --- Post-crash assertions (before recovery) ---

        # Employees must be persisted: hours should be deducted
        alice_mid = _get_emp(bomb_backend, "E001")
        self.assertAlmostEqual(
            alice_mid["Free_Hours"],
            32.0,
            places=4,
            msg="Employee write must have been persisted before crash",
        )

        # Journal must be in PREPARED (write #3 for EMPLOYEES_APPLIED never ran)
        self.assertEqual(
            _journal_state(bomb_backend, ATTEMPT_ID, PROJECT),
            STATE_PREPARED,
        )

        # Assignments table must NOT exist (write #4 never ran)
        self.assertFalse(
            bomb_backend.exists(f"{PROJECT}_Assigned"),
            "Assignment table must not exist — crash happened before assignment write",
        )

        # --- Recovery ---
        # Simulate a process restart: fresh processor on the same backend storage.
        # Subsequent writes during recovery do not re-trigger the bomb
        # (bomb_on=2, _write_count is already at 2).
        fresh_proc = AllocationTransactionProcessor(backend=bomb_backend)
        fresh_proc.check_and_recover_unfinished()

        # --- Post-recovery assertions ---

        alice_recovered = _get_emp(bomb_backend, "E001")
        self.assertAlmostEqual(
            alice_recovered["Free_Hours"],
            40.0,
            places=4,
            msg="Free_Hours must be restored to original value",
        )
        self.assertAlmostEqual(
            alice_recovered["Allocated_Hours"],
            0.0,
            places=4,
            msg="Allocated_Hours must be restored to original value",
        )
        self.assertNotIn(
            PROJECT,
            alice_recovered["Current_Project"],
            msg="Current_Project must not contain the crashed project",
        )

        # Journal must reach ROLLED_BACK
        self.assertEqual(
            _journal_state(bomb_backend, ATTEMPT_ID, PROJECT),
            STATE_ROLLED_BACK,
        )

    # ------------------------------------------------------------------
    # Test 5 — Crash after assignment write (journal in EMPLOYEES_APPLIED)
    # ------------------------------------------------------------------

    def test_05_crash_after_assignment_write_recovers_employees_and_tasks(self):
        """
        Crash scenario: process dies after {project}_Assigned.xlsx is written
        (write #4) but before the journal advances to ASSIGNMENTS_APPLIED (write #5).

        Write sequence inside allocate() for one employee / one task:
          #1  journal PREPARED
          #2  employees
          #3  journal EMPLOYEES_APPLIED
          #4  assignments                  ← _Bomb fires here (data already persisted)
          #5  journal ASSIGNMENTS_APPLIED  ← never reached

        A fresh processor must detect the EMPLOYEES_APPLIED journal record,
        restore both employee hours and the task assignment row, and leave the
        journal in ROLLED_BACK.
        """
        ATTEMPT_ID = "att-05-crash-asgn"
        PROJECT = "Epsilon"

        bomb_backend = WriteBombBackend(
            initial_data={"employees": _make_two_employees()},
            bomb_on=4,   # crash immediately after assignments write
        )
        proc_crash = AllocationTransactionProcessor(backend=bomb_backend)

        with self.assertRaises(_Bomb):
            proc_crash.allocate(
                project_name=PROJECT,
                tasks=self._task("T-005", days=1.0),
                attempt_id=ATTEMPT_ID,
            )

        # --- Post-crash assertions (before recovery) ---

        # Employees must be deducted
        alice_mid = _get_emp(bomb_backend, "E001")
        self.assertAlmostEqual(
            alice_mid["Free_Hours"],
            32.0,
            places=4,
            msg="Employee write must have been persisted before crash",
        )

        # Assignments must be written (write #4 succeeded)
        self.assertTrue(
            bomb_backend.exists(f"{PROJECT}_Assigned"),
            "Assignment table must exist — crash happened after assignment write",
        )
        t5_mid = _get_assigned_task(bomb_backend, PROJECT, "T-005")
        self.assertEqual(
            str(t5_mid["task_status"]).lower(),
            "assigned",
            msg="Assignment row must show 'assigned' before recovery",
        )

        # Journal must be in EMPLOYEES_APPLIED (write #3 was last journal write)
        self.assertEqual(
            _journal_state(bomb_backend, ATTEMPT_ID, PROJECT),
            STATE_EMPLOYEES_APPLIED,
        )

        # --- Recovery ---
        fresh_proc = AllocationTransactionProcessor(backend=bomb_backend)
        fresh_proc.check_and_recover_unfinished()

        # --- Post-recovery assertions ---

        # Employee hours restored
        alice_recovered = _get_emp(bomb_backend, "E001")
        self.assertAlmostEqual(
            alice_recovered["Free_Hours"],
            40.0,
            places=4,
            msg="Free_Hours must be restored after recovery",
        )
        self.assertAlmostEqual(
            alice_recovered["Allocated_Hours"],
            0.0,
            places=4,
            msg="Allocated_Hours must be restored after recovery",
        )
        self.assertNotIn(PROJECT, alice_recovered["Current_Project"])

        # Task assignment reset to unassigned
        t5_recovered = _get_assigned_task(bomb_backend, PROJECT, "T-005")
        self.assertEqual(
            str(t5_recovered["task_status"]).lower(),
            "waiting_resource",
            msg="task_status must be reset to waiting_resource after recovery",
        )
        self.assertEqual(
            str(t5_recovered["Assigned_Employee"]).lower(),
            "no resource available",
            msg="Assigned_Employee must be reset to 'No Resource Available' after recovery",
        )

        # Journal must reach ROLLED_BACK
        self.assertEqual(
            _journal_state(bomb_backend, ATTEMPT_ID, PROJECT),
            STATE_ROLLED_BACK,
        )

    # ------------------------------------------------------------------
    # Test 6 — Conflict protection: newer committed transaction blocks rollback
    # ------------------------------------------------------------------

    def test_06_rollback_blocked_when_newer_committed_transaction_overlaps(self):
        """
        When a newer COMMITTED transaction has modified the same employee,
        rolling back the older transaction must be blocked to prevent
        overwriting the newer allocation.

        Setup:
          att-older  allocates T-6a to Alice  → COMMITTED (first)
          att-newer  allocates T-6b to Alice  → COMMITTED (second)
          rollback(att-older) → conflict detected → error returned, att-older
            is marked NEEDS_REVIEW, att-newer remains COMMITTED, Alice's hours
            are unchanged.

        A single-employee backend is used to guarantee both allocations go to
        the same person regardless of the idxmin() tie-breaking behaviour.
        """
        # Use a single-employee backend: only Alice, so both allocations must
        # go to her regardless of Allocated_Hours ordering.
        backend = InMemoryBackend({"employees": _make_single_employee()})
        proc = AllocationTransactionProcessor(backend=backend)

        # First allocation — att-older → Alice: Free 40→32, Alloc 0→8
        proc.allocate(
            project_name="Zeta",
            tasks=self._task("T-6a", days=1.0),
            attempt_id="att-older",
        )
        self.assertEqual(_journal_state(backend, "att-older", "Zeta"), STATE_COMMITTED)

        # Second allocation — att-newer → Alice: Free 32→24, Alloc 8→16
        proc.allocate(
            project_name="Zeta",
            tasks=self._task("T-6b", days=1.0),
            attempt_id="att-newer",
        )
        self.assertEqual(_journal_state(backend, "att-newer", "Zeta"), STATE_COMMITTED)

        alice_pre = _get_emp(backend, "E001")
        self.assertAlmostEqual(alice_pre["Free_Hours"], 24.0, places=4)
        self.assertAlmostEqual(alice_pre["Allocated_Hours"], 16.0, places=4)

        # Attempt to roll back the older transaction — must be blocked
        rb = proc.rollback(project_name="Zeta", attempt_id="att-older")

        self.assertEqual(
            rb["status"],
            "error",
            "Rollback of older attempt must be blocked when newer committed tx exists",
        )

        # att-older must be NEEDS_REVIEW (conflict-blocked)
        self.assertEqual(
            _journal_state(backend, "att-older", "Zeta"),
            STATE_NEEDS_REVIEW,
        )

        # att-newer must remain COMMITTED (untouched by the blocked rollback)
        self.assertEqual(
            _journal_state(backend, "att-newer", "Zeta"),
            STATE_COMMITTED,
        )

        # Alice's balances must be completely unchanged — the newer allocation
        # must still be in force
        alice_post = _get_emp(backend, "E001")
        self.assertAlmostEqual(
            alice_post["Free_Hours"],
            24.0,
            places=4,
            msg="Alice's Free_Hours must not change after blocked rollback",
        )
        self.assertAlmostEqual(
            alice_post["Allocated_Hours"],
            16.0,
            places=4,
            msg="Alice's Allocated_Hours must not change after blocked rollback",
        )


if __name__ == "__main__":
    unittest.main()
