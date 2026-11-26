
import unittest

from agents.resource_validation_agent import (
    _mark_external_hire_tasks,
)


class TestExternalHireTaskMarking(unittest.TestCase):

    def test_only_shortage_tasks_are_marked(self):
        tasks = [
            {"task_id": "T1", "Assigned_Employee": "Alice"},
            {"task_id": "T2", "Assigned_Employee": "No Resource Available"},
            {"task_id": "T3", "Assigned_Employee": "Bob"},
        ]
        shortages = [{"task_id": "T2"}]

        _mark_external_hire_tasks(tasks, shortages)

        self.assertNotIn("task_status", tasks[0])
        self.assertEqual(tasks[1]["task_status"], "NEEDS_EXTERNAL_HIRE")
        self.assertNotIn("task_status", tasks[2])

    def test_employee_assignment_marker_is_preserved(self):
        tasks = [
            {
                "task_id": "T1",
                "Assigned_Employee": "No Resource Available",
            }
        ]

        _mark_external_hire_tasks(tasks, [{"task_id": "T1"}])

        self.assertEqual(
            tasks[0]["Assigned_Employee"],
            "No Resource Available",
        )

    def test_missing_or_na_ids_are_not_used(self):
        tasks = [
            {"task_name": "Task A"},
            {"task_id": "N/A", "task_name": "Task B"},
            {"task_id": "T3", "task_name": "Task C"},
        ]
        shortages = [
            {"task_id": "N/A"},
            {"task_id": None},
            {"task_id": ""},
        ]

        _mark_external_hire_tasks(tasks, shortages)

        for task in tasks:
            self.assertNotIn("task_status", task)

    def test_multiple_shortages_are_marked(self):
        tasks = [
            {"task_id": "T1"},
            {"task_id": "T2"},
            {"task_id": "T3"},
        ]

        _mark_external_hire_tasks(
            tasks,
            [{"task_id": "T1"}, {"task_id": "T3"}],
        )

        self.assertEqual(tasks[0]["task_status"], "NEEDS_EXTERNAL_HIRE")
        self.assertNotIn("task_status", tasks[1])
        self.assertEqual(tasks[2]["task_status"], "NEEDS_EXTERNAL_HIRE")


if __name__ == "__main__":
    unittest.main()
