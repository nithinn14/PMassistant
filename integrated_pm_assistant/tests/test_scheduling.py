import unittest
from datetime import date, datetime
import pandas as pd

from tools.save_schedule_tool import add_working_days
from tools.gantt_tool import _apply_dependency_scheduling, _work_days, _add_work_days

import io
from contextlib import redirect_stdout

class TestSchedulingRegressions(unittest.TestCase):
    """
    Test suite for task duration and dependency scheduling rules:
      - Task duration represents working days of effort.
      - Start and end dates are inclusive.
      - A 1-day task starting Monday ends Monday.
      - A 2-day task starting Monday ends Tuesday.
      - Weekends are excluded.
      - Dependent tasks start on the next working day after their predecessors finish.
      - The same employee cannot work on overlapping tasks.
      - Different employees / unassigned tasks can work in parallel.
      - Fractional durations use ceiling rounding with a minimum of 1 reserved working day.
      - Original fractional time_days are preserved.
      - Repeated scheduling is idempotent.
      - Circular or invalid dependencies do not silently schedule at project start.
    """

    def setUp(self):
        # 2026-11-02 is a Monday
        self.monday = date(2026, 11, 2)
        self.tuesday = date(2026, 11, 3)
        self.thursday = date(2026, 11, 5)
        self.friday = date(2026, 11, 6)
        self.saturday = date(2026, 11, 7)
        self.sunday = date(2026, 11, 8)
        self.next_monday = date(2026, 11, 9)

    # 1. 1-working-day task starting Monday
    def test_01_one_day_task_starting_monday_ends_monday(self):
        # Direct helper check
        end_date = add_working_days(self.monday, 1)
        self.assertEqual(end_date, self.monday)

        # Full dependency scheduling check
        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 1,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])
        res = _apply_dependency_scheduling(df, self.monday)
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        self.assertEqual(_work_days(res.iloc[0]["start_date"], res.iloc[0]["end_date"]), 1)

    # 2. 2-working-day task starting Monday
    def test_02_two_day_task_starting_monday_ends_tuesday(self):
        end_date = add_working_days(self.monday, 2)
        self.assertEqual(end_date, self.tuesday)

        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 2,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])
        res = _apply_dependency_scheduling(df, self.monday)
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.tuesday)
        self.assertEqual(_work_days(res.iloc[0]["start_date"], res.iloc[0]["end_date"]), 2)

    # 3. 2-working-day task starting Friday (weekend skipped)
    def test_03_two_day_task_starting_friday_skips_weekend(self):
        end_date = add_working_days(self.friday, 2)
        self.assertEqual(end_date, self.next_monday)

        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 2,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])
        res = _apply_dependency_scheduling(df, self.friday)
        self.assertEqual(res.iloc[0]["start_date"].date(), self.friday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.next_monday)
        self.assertEqual(_work_days(res.iloc[0]["start_date"], res.iloc[0]["end_date"]), 2)

    # 4. Dependent task chain (Two sequential 1-day tasks complete in 2 working days)
    def test_04_dependent_task_chain_starts_next_working_day(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": ["T1"],
                "assigned_empl": "Bob",
            },
        ])
        res = _apply_dependency_scheduling(df, self.monday)
        # Task 1: Monday -> Monday
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        # Task 2: starts Tuesday -> ends Tuesday
        self.assertEqual(res.iloc[1]["start_date"].date(), self.tuesday)
        self.assertEqual(res.iloc[1]["end_date"].date(), self.tuesday)

    # 5. Single employee concurrency: non-overlapping execution
    def test_05_single_employee_tasks_cannot_overlap(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": [],  # no explicit dependency, but same worker
                "assigned_empl": "Alice",
            },
        ])
        res = _apply_dependency_scheduling(df, self.monday)
        # Alice does T1 on Monday
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        # Alice is busy Monday, so T2 must sequence to Tuesday
        self.assertEqual(res.iloc[1]["start_date"].date(), self.tuesday)
        self.assertEqual(res.iloc[1]["end_date"].date(), self.tuesday)

    # 6. Weekend project start rolls forward to Monday
    def test_06_weekend_project_start_rolls_to_monday(self):
        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 1,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])
        # Start on Saturday
        res_sat = _apply_dependency_scheduling(df, self.saturday)
        self.assertEqual(res_sat.iloc[0]["start_date"].date(), self.next_monday)
        self.assertEqual(res_sat.iloc[0]["end_date"].date(), self.next_monday)

        # Start on Sunday
        res_sun = _apply_dependency_scheduling(df, self.sunday)
        self.assertEqual(res_sun.iloc[0]["start_date"].date(), self.next_monday)
        self.assertEqual(res_sun.iloc[0]["end_date"].date(), self.next_monday)

    # 7. Multi-day task crossing weekend
    def test_07_three_day_task_crossing_weekend(self):
        # Starts Thursday 2026-11-05: Thu (1), Fri (2), skips Sat/Sun, Mon (3)
        end_date = add_working_days(self.thursday, 3)
        self.assertEqual(end_date, self.next_monday)

        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 3,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])
        res = _apply_dependency_scheduling(df, self.thursday)
        self.assertEqual(res.iloc[0]["start_date"].date(), self.thursday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.next_monday)
        self.assertEqual(_work_days(res.iloc[0]["start_date"], res.iloc[0]["end_date"]), 3)

    # 8. Dependency across weekend (Predecessor ends Friday, successor starts Monday)
    def test_08_dependency_across_weekend(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": ["T1"],
                "assigned_empl": "Bob",
            },
        ])
        # Project starts Friday: T1 is Friday. T2 depends on T1 -> starts Monday
        res = _apply_dependency_scheduling(df, self.friday)
        self.assertEqual(res.iloc[0]["start_date"].date(), self.friday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.friday)
        self.assertEqual(res.iloc[1]["start_date"].date(), self.next_monday)
        self.assertEqual(res.iloc[1]["end_date"].date(), self.next_monday)

    # 9. Employee availability across weekend
    def test_09_employee_availability_across_weekend(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 2,  # Thu + Fri
                "dependencies": [],
                "assigned_empl": "Charlie",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": [],  # independent, but Charlie is busy through Friday
                "assigned_empl": "Charlie",
            },
        ])
        res = _apply_dependency_scheduling(df, self.thursday)
        # T1 runs Thursday and Friday
        self.assertEqual(res.iloc[0]["start_date"].date(), self.thursday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.friday)
        # Charlie is next free on Monday
        self.assertEqual(res.iloc[1]["start_date"].date(), self.next_monday)
        self.assertEqual(res.iloc[1]["end_date"].date(), self.next_monday)

    # 10. Repeated scheduling idempotency
    def test_10_repeated_scheduling_is_idempotent(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 2,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": ["T1"],
                "assigned_empl": "Alice",
            },
        ])
        res1 = _apply_dependency_scheduling(df, self.monday)
        res2 = _apply_dependency_scheduling(res1, self.monday)

        for col in ["start_date", "end_date", "time_days"]:
            self.assertTrue(res1[col].equals(res2[col]), f"Mismatch on {col} in repeated scheduling")

    # 11. Fractional duration ceiling rounding & preserving time_days
    def test_11_fractional_durations_use_ceiling_and_preserve_time_days(self):
        # 0.5 days -> 1 working day [Mon, Mon]
        end_05 = add_working_days(self.monday, 0.5)
        self.assertEqual(end_05, self.monday)

        # 1.5 days -> 2 working days [Mon, Tue]
        end_15 = add_working_days(self.monday, 1.5)
        self.assertEqual(end_15, self.tuesday)

        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 0.5,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1.5,
                "dependencies": ["T1"],
                "assigned_empl": "Bob",
            },
        ])
        res = _apply_dependency_scheduling(df, self.monday)

        # T1: ceiling(0.5) = 1 working day -> [Mon, Mon]
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        # Original fractional time_days is preserved
        self.assertEqual(res.iloc[0]["time_days"], 0.5)

        # T2: ceiling(1.5) = 2 working days -> [Tue, Wed]
        wednesday = date(2026, 11, 4)
        self.assertEqual(res.iloc[1]["start_date"].date(), self.tuesday)
        self.assertEqual(res.iloc[1]["end_date"].date(), wednesday)
        self.assertEqual(res.iloc[1]["time_days"], 1.5)

    # 12. Parallel tasks for different employees / unassigned
    def test_12_parallel_tasks_different_workers_and_unassigned(self):
        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "Alice",
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "Bob",
            },
            {
                "task_id": "T3",
                "task_name": "Task 3",
                "time_days": 1,
                "dependencies": [],
                "assigned_empl": "No Resource Available",
            },
        ])
        res = _apply_dependency_scheduling(df, self.monday)

        # All three independent tasks run in parallel on Monday
        for i in range(3):
            self.assertEqual(res.iloc[i]["start_date"].date(), self.monday)
            self.assertEqual(res.iloc[i]["end_date"].date(), self.monday)

    # 13. Unresolved / circular dependency fallback
    def test_13_unresolved_and_circular_dependencies_do_not_schedule_at_project_start(self):
        initial_start = pd.Timestamp("2026-11-20")
        initial_end = pd.Timestamp("2026-11-20")

        df = pd.DataFrame([
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "time_days": 1,
                "dependencies": ["TASK-NONEXISTENT"],
                "assigned_empl": "Alice",
                "start_date": initial_start,
                "end_date": initial_end,
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "time_days": 1,
                "dependencies": ["T3"],
                "assigned_empl": "Bob",
                "start_date": initial_start,
                "end_date": initial_end,
            },
            {
                "task_id": "T3",
                "task_name": "Task 3",
                "time_days": 1,
                "dependencies": ["T2"],
                "assigned_empl": "Charlie",
                "start_date": initial_start,
                "end_date": initial_end,
            },
        ])

        res = _apply_dependency_scheduling(df, self.monday)

        # Tasks with unresolved/circular deps must NOT be scheduled at self.monday
        for i in range(3):
            self.assertNotEqual(res.iloc[i]["start_date"].date(), self.monday)
            # They preserve their initial dates
            self.assertEqual(res.iloc[i]["start_date"], initial_start)
            self.assertEqual(res.iloc[i]["end_date"], initial_end)

        # 14. Zero duration produces a warning and uses safe fallback
    def test_14_zero_duration_warns_and_uses_one_day_minimum(self):
        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": 0,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])

        output = io.StringIO()
        with redirect_stdout(output):
            res = _apply_dependency_scheduling(df, self.monday)

        self.assertIn("Warning:", output.getvalue())
        self.assertIn("time_days=", output.getvalue())
        self.assertIn("invalid", output.getvalue())
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["time_days"], 0)

    # 15. Invalid duration produces a warning and uses safe fallback
    def test_15_invalid_duration_warns_and_uses_one_day_minimum(self):
        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": "invalid",
            "dependencies": [],
            "assigned_empl": "Alice",
        }])

        output = io.StringIO()
        with redirect_stdout(output):
            res = _apply_dependency_scheduling(df, self.monday)

        self.assertIn("Warning:", output.getvalue())
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["time_days"], "invalid")

    # 16. Negative duration produces a warning
    def test_16_negative_duration_warns_and_uses_one_day_minimum(self):
        df = pd.DataFrame([{
            "task_id": "T1",
            "task_name": "Task 1",
            "time_days": -1,
            "dependencies": [],
            "assigned_empl": "Alice",
        }])

        output = io.StringIO()
        with redirect_stdout(output):
            res = _apply_dependency_scheduling(df, self.monday)

        self.assertIn("Warning:", output.getvalue())
        self.assertEqual(res.iloc[0]["start_date"].date(), self.monday)
        self.assertEqual(res.iloc[0]["end_date"].date(), self.monday)


if __name__ == "__main__":
    unittest.main()

