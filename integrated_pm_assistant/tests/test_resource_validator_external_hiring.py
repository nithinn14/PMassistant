
import unittest

from tools.resources.resource_validator import validate_resources


class TestExternalHiringResourceValidation(unittest.TestCase):

    def test_no_resource_available_is_detected(self):
        tasks = [
            {
                "task_id": "TASK-001",
                "task_name": "Build API",
                "assigned_role": "Backend Developer",
                "Assigned_Employee": "No Resource Available",
            }
        ]

        result = validate_resources("TestProject", tasks)

        self.assertEqual(result["total_shortages"], 1)
        self.assertEqual(result["shortages"][0]["task_id"], "TASK-001")

    def test_waiting_resource_is_detected(self):
        tasks = [
            {
                "task_id": "TASK-002",
                "task_name": "Build UI",
                "assigned_role": "Frontend Developer",
                "Assigned_Employee": "",
                "task_status": "waiting_resource",
            }
        ]

        result = validate_resources("TestProject", tasks)

        self.assertEqual(result["total_shortages"], 1)
        self.assertEqual(result["shortages"][0]["task_id"], "TASK-002")

    def test_approved_external_hire_is_not_a_shortage(self):
        tasks = [
            {
                "task_id": "TASK-003",
                "task_name": "Build Database",
                "assigned_role": "Database Developer",
                "Assigned_Employee": "No Resource Available",
                "task_status": "NEEDS_EXTERNAL_HIRE",
            }
        ]

        result = validate_resources("TestProject", tasks)

        self.assertEqual(result["total_shortages"], 0)
        self.assertEqual(result["shortages"], [])

    def test_mixed_tasks_only_reports_unresolved_shortages(self):
        tasks = [
            {
                "task_id": "TASK-001",
                "Assigned_Employee": "Alice",
            },
            {
                "task_id": "TASK-002",
                "Assigned_Employee": "No Resource Available",
            },
            {
                "task_id": "TASK-003",
                "Assigned_Employee": "No Resource Available",
                "task_status": "NEEDS_EXTERNAL_HIRE",
            },
            {
                "task_id": "TASK-004",
                "Assigned_Employee": "",
                "task_status": "waiting_resource",
            },
        ]

        result = validate_resources("TestProject", tasks)

        shortage_ids = {
            item["task_id"] for item in result["shortages"]
        }

        self.assertEqual(shortage_ids, {"TASK-002", "TASK-004"})
        self.assertEqual(result["total_shortages"], 2)

    def test_missing_task_id_uses_na_fallback(self):
        tasks = [
            {
                "task_name": "Build API",
                "Assigned_Employee": "No Resource Available",
            }
        ]

        result = validate_resources("TestProject", tasks)

        self.assertEqual(result["total_shortages"], 1)
        self.assertEqual(result["shortages"][0]["task_id"], "N/A")


if __name__ == "__main__":
    unittest.main()
