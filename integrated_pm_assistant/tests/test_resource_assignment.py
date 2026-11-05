import unittest
import math
from typing import List, Dict, Any, Optional
import pandas as pd

from data_backend.base import DataBackend
from data_backend.registry import bind_backend, reset_bound_backend
from tools.assign_employee_tool import assign_resources


class InMemoryBackend(DataBackend):
    def __init__(self, initial_data=None):
        self.storage = {}
        if initial_data:
            for k, v in initial_data.items():
                self.storage[k] = v.copy(deep=True) if isinstance(v, pd.DataFrame) else v

    def read(self, table_name: str, filters: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        if table_name in self.storage:
            return self.storage[table_name].copy(deep=True)
        return pd.DataFrame()

    def write(self, table_name: str, df: pd.DataFrame, mode: str = "replace") -> None:
        self.storage[table_name] = df.copy(deep=True)

    def append(self, table_name: str, df: pd.DataFrame) -> None:
        if table_name in self.storage:
            self.storage[table_name] = pd.concat([self.storage[table_name], df], ignore_index=True)
        else:
            self.storage[table_name] = df.copy(deep=True)

    def exists(self, table_name: str) -> bool:
        return table_name in self.storage

    def update(self, table_name: str, df: pd.DataFrame, key_columns: List[str]) -> None:
        self.storage[table_name] = df.copy(deep=True)


class TestResourceAssignment(unittest.TestCase):
    def setUp(self):
        self.initial_employees = pd.DataFrame([
            {
                "Employee_Name": "Alice Developer",
                "Role": "Backend Developer",
                "Free_Hours": 8.0,
                "Allocated_Hours": 0.0,
                "Current_Project": "",
            },
            {
                "Employee_Name": "Bob Developer",
                "Role": "Backend Developer",
                "Free_Hours": 16.0,
                "Allocated_Hours": 10.0,
                "Current_Project": "Existing Project",
            },
            {
                "Employee_Name": "Charlie Designer",
                "Role": "UI/UX Designer",
                "Free_Hours": 40.0,
                "Allocated_Hours": 0.0,
                "Current_Project": "",
            },
        ])
        self.backend = InMemoryBackend({"employees": self.initial_employees})
        self.token = bind_backend(self.backend)

    def tearDown(self):
        if self.token is not None:
            reset_bound_backend(self.token)

    def test_insufficient_free_hours(self):
        """Task requires more hours than any matching employee has available."""
        # Alice has 8h, Bob has 16h. Task requires 3 days = 24h.
        tasks = [{
            "task_id": "T1",
            "task_name": "Heavy Backend Task",
            "assigned_role": "Backend Developer",
            "time_days": 3.0,
        }]

        result = assign_resources("TestProject", tasks)

        self.assertTrue(result["resource_blocked"])
        assigned_tasks = result["tasks"]
        self.assertEqual(len(assigned_tasks), 1)
        self.assertEqual(assigned_tasks[0]["Assigned_Employee"], "No Resource Available")
        self.assertEqual(assigned_tasks[0]["task_status"], "waiting_resource")

        # Verify no employee hours were modified
        emp_df = self.backend.read("employees")
        alice = emp_df[emp_df["Employee_Name"] == "Alice Developer"].iloc[0]
        bob = emp_df[emp_df["Employee_Name"] == "Bob Developer"].iloc[0]
        self.assertEqual(alice["Free_Hours"], 8.0)
        self.assertEqual(alice["Allocated_Hours"], 0.0)
        self.assertEqual(bob["Free_Hours"], 16.0)
        self.assertEqual(bob["Allocated_Hours"], 10.0)

    def test_exactly_sufficient_free_hours(self):
        """Employee with Free_Hours == hours_needed is eligible and Free_Hours becomes 0."""
        # Alice has exactly 8h free. Task requires 1 day = 8h.
        tasks = [{
            "task_id": "T1",
            "task_name": "Standard Backend Task",
            "assigned_role": "Backend Developer",
            "time_days": 1.0,
        }]

        result = assign_resources("TestProject", tasks)

        self.assertFalse(result["resource_blocked"])
        assigned_tasks = result["tasks"]
        self.assertEqual(assigned_tasks[0]["Assigned_Employee"], "Alice Developer")
        self.assertEqual(assigned_tasks[0]["task_status"], "assigned")

        emp_df = self.backend.read("employees")
        alice = emp_df[emp_df["Employee_Name"] == "Alice Developer"].iloc[0]
        self.assertEqual(alice["Free_Hours"], 0.0)
        self.assertEqual(alice["Allocated_Hours"], 8.0)
        self.assertEqual(alice["Current_Project"], "TestProject")

    def test_more_than_sufficient_free_hours(self):
        """Employee with Free_Hours > hours_needed is successfully assigned and remaining hours are correct."""
        # Bob has 16h free. Task requires 1 day = 8h.
        # Set Alice to 0h so Bob is the only eligible candidate.
        emp_df = self.backend.read("employees")
        emp_df.loc[emp_df["Employee_Name"] == "Alice Developer", "Free_Hours"] = 0.0
        self.backend.write("employees", emp_df)

        tasks = [{
            "task_id": "T1",
            "task_name": "Task For Bob",
            "assigned_role": "Backend Developer",
            "time_days": 1.0,
        }]

        result = assign_resources("TestProject", tasks)

        self.assertFalse(result["resource_blocked"])
        assigned_tasks = result["tasks"]
        self.assertEqual(assigned_tasks[0]["Assigned_Employee"], "Bob Developer")
        self.assertEqual(assigned_tasks[0]["task_status"], "assigned")

        updated_emp = self.backend.read("employees")
        bob = updated_emp[updated_emp["Employee_Name"] == "Bob Developer"].iloc[0]
        self.assertEqual(bob["Free_Hours"], 8.0)  # 16.0 - 8.0
        self.assertEqual(bob["Allocated_Hours"], 18.0)  # 10.0 + 8.0
        self.assertIn("TestProject", bob["Current_Project"])

    def test_lowest_allocated_hours_selection_strategy(self):
        """When multiple employees have sufficient capacity, choose the one with lowest Allocated_Hours."""
        # Alice: Free 16, Allocated 10
        # Bob: Free 16, Allocated 2
        emp_df = self.backend.read("employees")
        emp_df.loc[emp_df["Employee_Name"] == "Alice Developer", "Free_Hours"] = 16.0
        emp_df.loc[emp_df["Employee_Name"] == "Alice Developer", "Allocated_Hours"] = 10.0
        emp_df.loc[emp_df["Employee_Name"] == "Bob Developer", "Free_Hours"] = 16.0
        emp_df.loc[emp_df["Employee_Name"] == "Bob Developer", "Allocated_Hours"] = 2.0
        self.backend.write("employees", emp_df)

        tasks = [{
            "task_id": "T1",
            "task_name": "Balanced Task",
            "assigned_role": "Backend Developer",
            "time_days": 1.0,
        }]

        result = assign_resources("TestProject", tasks)
        self.assertFalse(result["resource_blocked"])
        # Bob should be chosen because 2.0 < 10.0
        self.assertEqual(result["tasks"][0]["Assigned_Employee"], "Bob Developer")

    def test_no_eligible_employee_for_role(self):
        """Role has no matching candidates in the employee pool."""
        tasks = [{
            "task_id": "T1",
            "task_name": "DevOps Setup",
            "assigned_role": "DevOps Engineer",
            "time_days": 1.0,
        }]

        result = assign_resources("TestProject", tasks)

        self.assertTrue(result["resource_blocked"])
        self.assertEqual(result["tasks"][0]["Assigned_Employee"], "No Resource Available")
        self.assertEqual(result["tasks"][0]["task_status"], "waiting_resource")

    def test_existing_assignments_preserved_during_retry(self):
        """Run 1 assigns Task 1. Retry preserves Task 1 assignment without deducting hours again."""
        tasks = [
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "assigned_role": "Backend Developer",
                "time_days": 1.0,  # 8h -> Alice
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "assigned_role": "Backend Developer",
                "time_days": 3.0,  # 24h -> neither Alice (0h left) nor Bob (16h left) can take it
            },
        ]

        # Run 1
        res1 = assign_resources("TestProject", tasks)
        self.assertTrue(res1["resource_blocked"])
        self.assertEqual(res1["tasks"][0]["Assigned_Employee"], "Alice Developer")
        self.assertEqual(res1["tasks"][0]["task_status"], "assigned")
        self.assertEqual(res1["tasks"][1]["Assigned_Employee"], "No Resource Available")
        self.assertEqual(res1["tasks"][1]["task_status"], "waiting_resource")

        emp_after_run1 = self.backend.read("employees")
        alice_r1 = emp_after_run1[emp_after_run1["Employee_Name"] == "Alice Developer"].iloc[0]
        self.assertEqual(alice_r1["Free_Hours"], 0.0)
        self.assertEqual(alice_r1["Allocated_Hours"], 8.0)

        # Run 2 (retry without capacity change)
        res2 = assign_resources("TestProject", tasks)
        self.assertTrue(res2["resource_blocked"])
        # Task 1 must remain Alice
        self.assertEqual(res2["tasks"][0]["Assigned_Employee"], "Alice Developer")
        self.assertEqual(res2["tasks"][0]["task_status"], "assigned")

        # Alice's hours must NOT be deducted a second time
        emp_after_run2 = self.backend.read("employees")
        alice_r2 = emp_after_run2[emp_after_run2["Employee_Name"] == "Alice Developer"].iloc[0]
        self.assertEqual(alice_r2["Free_Hours"], 0.0)
        self.assertEqual(alice_r2["Allocated_Hours"], 8.0)

    def test_waiting_tasks_successfully_assigned_after_additional_capacity(self):
        """Waiting task becomes assigned on retry after employee Free_Hours is increased."""
        tasks = [
            {
                "task_id": "T1",
                "task_name": "Task 1",
                "assigned_role": "Backend Developer",
                "time_days": 1.0,
            },
            {
                "task_id": "T2",
                "task_name": "Task 2",
                "assigned_role": "Backend Developer",
                "time_days": 3.0,  # 24h
            },
        ]

        # Run 1: T2 cannot be assigned
        assign_resources("TestProject", tasks)

        # Add capacity: Give Bob 40 Free_Hours
        emp_df = self.backend.read("employees")
        emp_df.loc[emp_df["Employee_Name"] == "Bob Developer", "Free_Hours"] = 40.0
        self.backend.write("employees", emp_df)

        # Run 2 (retry)
        res2 = assign_resources("TestProject", tasks)
        self.assertFalse(res2["resource_blocked"])

        # T1 is still Alice
        self.assertEqual(res2["tasks"][0]["Assigned_Employee"], "Alice Developer")
        self.assertEqual(res2["tasks"][0]["task_status"], "assigned")

        # T2 is now Bob
        self.assertEqual(res2["tasks"][1]["Assigned_Employee"], "Bob Developer")
        self.assertEqual(res2["tasks"][1]["task_status"], "assigned")

        emp_final = self.backend.read("employees")
        bob_final = emp_final[emp_final["Employee_Name"] == "Bob Developer"].iloc[0]
        self.assertEqual(bob_final["Free_Hours"], 16.0)  # 40.0 - 24.0
        self.assertEqual(bob_final["Allocated_Hours"], 34.0)  # 10.0 + 24.0

    def test_invalid_task_durations_are_not_assigned(self):
        """Zero, negative, missing, non-finite, or invalid string durations must not become zero-hour assignments."""
        invalid_cases = [
            {"task_id": "T_ZERO", "task_name": "Zero Task", "assigned_role": "UI/UX Designer", "time_days": 0},
            {"task_id": "T_ZERO_FLOAT", "task_name": "Zero Float", "assigned_role": "UI/UX Designer", "time_days": 0.0},
            {"task_id": "T_NEG", "task_name": "Negative Task", "assigned_role": "UI/UX Designer", "time_days": -2.0},
            {"task_id": "T_NONE", "task_name": "None Task", "assigned_role": "UI/UX Designer", "time_days": None},
            {"task_id": "T_NAN", "task_name": "NaN Task", "assigned_role": "UI/UX Designer", "time_days": float("nan")},
            {"task_id": "T_INF", "task_name": "Inf Task", "assigned_role": "UI/UX Designer", "time_days": float("inf")},
            {"task_id": "T_STR", "task_name": "String Task", "assigned_role": "UI/UX Designer", "time_days": "not_a_number"},
        ]

        # Charlie Designer has 40.0 Free_Hours.
        result = assign_resources("TestProject", invalid_cases)
        self.assertTrue(result["resource_blocked"])

        for task in result["tasks"]:
            self.assertEqual(
                task["Assigned_Employee"],
                "No Resource Available",
                f"Task {task['task_id']} was unexpectedly assigned to {task.get('Assigned_Employee')}"
            )
            self.assertEqual(task["task_status"], "waiting_resource")

        # Charlie's hours must remain completely untouched
        emp_df = self.backend.read("employees")
        charlie = emp_df[emp_df["Employee_Name"] == "Charlie Designer"].iloc[0]
        self.assertEqual(charlie["Free_Hours"], 40.0)
        self.assertEqual(charlie["Allocated_Hours"], 0.0)

    def test_fractional_duration_effort_calculation(self):
        """0.5 days must require exactly 4 hours, and preserve 0.5 in time_days."""
        tasks = [{
            "task_id": "T_HALF",
            "task_name": "Half Day Task",
            "assigned_role": "UI/UX Designer",
            "time_days": 0.5,
        }]

        result = assign_resources("TestProject", tasks)
        self.assertFalse(result["resource_blocked"])

        assigned_task = result["tasks"][0]
        self.assertEqual(assigned_task["Assigned_Employee"], "Charlie Designer")
        self.assertEqual(assigned_task["task_status"], "assigned")
        self.assertEqual(assigned_task["time_days"], 0.5)

        # Charlie should have exactly 4 hours deducted
        emp_df = self.backend.read("employees")
        charlie = emp_df[emp_df["Employee_Name"] == "Charlie Designer"].iloc[0]
        self.assertEqual(charlie["Free_Hours"], 36.0)  # 40.0 - 4.0
        self.assertEqual(charlie["Allocated_Hours"], 4.0)

    def test_intra_batch_capacity_exhaustion(self):
        """
        Multiple tasks compete for the same employee's capacity in a single batch.
        Tasks are assigned sequentially until capacity is exhausted; subsequent tasks
        become waiting_resource without deducting additional hours.
        """
        emp_df = self.backend.read("employees")
        emp_df.loc[emp_df["Employee_Name"] == "Alice Developer", "Free_Hours"] = 0.0
        self.backend.write("employees", emp_df)

        tasks = [
            {"task_id": "T1", "task_name": "Task 1", "assigned_role": "Backend Developer", "time_days": 1.0},
            {"task_id": "T2", "task_name": "Task 2", "assigned_role": "Backend Developer", "time_days": 1.0},
            {"task_id": "T3", "task_name": "Task 3", "assigned_role": "Backend Developer", "time_days": 1.0},
        ]

        result = assign_resources("TestBatchProject", tasks)
        self.assertTrue(result["resource_blocked"])

        assigned_tasks = result["tasks"]
        self.assertEqual(len(assigned_tasks), 3)

        # Task 1 assigned to Bob
        self.assertEqual(assigned_tasks[0]["task_id"], "T1")
        self.assertEqual(assigned_tasks[0]["Assigned_Employee"], "Bob Developer")
        self.assertEqual(assigned_tasks[0]["task_status"], "assigned")

        # Task 2 assigned to Bob
        self.assertEqual(assigned_tasks[1]["task_id"], "T2")
        self.assertEqual(assigned_tasks[1]["Assigned_Employee"], "Bob Developer")
        self.assertEqual(assigned_tasks[1]["task_status"], "assigned")

        # Task 3 becomes waiting_resource / No Resource Available
        self.assertEqual(assigned_tasks[2]["task_id"], "T3")
        self.assertEqual(assigned_tasks[2]["Assigned_Employee"], "No Resource Available")
        self.assertEqual(assigned_tasks[2]["task_status"], "waiting_resource")

        # Check Bob's final hours: 16 -> 0 Free_Hours, 10 -> 26 Allocated_Hours
        emp_final = self.backend.read("employees")
        bob = emp_final[emp_final["Employee_Name"] == "Bob Developer"].iloc[0]
        self.assertEqual(bob["Free_Hours"], 0.0)
        self.assertEqual(bob["Allocated_Hours"], 26.0)  # 10.0 + 16.0


if __name__ == "__main__":
    unittest.main()
