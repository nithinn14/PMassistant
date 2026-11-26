
import json
import unittest
from unittest.mock import patch

from agents.orchestrator_agent import _resource_loop_guard


class TestResourceLoopGuard(unittest.TestCase):

    def _make_input(self, tasks):
        return json.dumps({
            "project_name": "Test Project",
            "tasks": tasks,
        })

    def test_all_assigned_tasks_allow_pipeline_to_continue(self):
        input_text = self._make_input([
            {
                "task_id": "T1",
                "Assigned_Employee": "Alice",
                "task_status": "assigned",
                "assigned_role": "Developer",
            }
        ])

        with patch(
            "agents.orchestrator_agent.update_workflow_state"
        ) as update_state:
            result = _resource_loop_guard(input_text)

        self.assertEqual(result, input_text)
        update_state.assert_not_called()

    def test_external_hire_tasks_allow_pipeline_to_continue(self):
        # Test both supported locations for the external-hire marker.
        external_hire_tasks = [
            {
                "task_id": "T1",
                "Assigned_Employee": "NEEDS_EXTERNAL_HIRE",
                "task_status": "pending",
                "assigned_role": "Developer",
            },
            {
                "task_id": "T2",
                "Assigned_Employee": "No Resource Available",
                "task_status": "NEEDS_EXTERNAL_HIRE",
                "assigned_role": "Designer",
            },
        ]

        for task in external_hire_tasks:
            with self.subTest(task=task):
                input_text = self._make_input([task])

                with patch(
                    "agents.orchestrator_agent.update_workflow_state"
                ) as update_state:
                    result = _resource_loop_guard(input_text)

                self.assertEqual(result, input_text)
                update_state.assert_not_called()

    def test_unresolved_shortage_marks_workflow_blocked_and_raises(self):
        input_text = self._make_input([
            {
                "task_id": "T1",
                "Assigned_Employee": "No Resource Available",
                "task_status": "waiting_resource",
                "assigned_role": "Developer",
            },
            {
                "task_id": "T2",
                "Assigned_Employee": "",
                "task_status": "pending",
                "assigned_role": "Designer",
            },
        ])

        workflow_state = {
            "resolution": "PENDING",
            "diagnostic_file": "resource_diagnostic.json",
        }

        with (
            patch(
                "agents.orchestrator_agent.check_workflow_resolution",
                return_value=workflow_state,
            ),
            patch(
                "agents.orchestrator_agent.update_workflow_state"
            ) as update_state,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "unresolved shortages"
            ):
                _resource_loop_guard(input_text)

        update_state.assert_called_once_with(
            project_name="Test Project",
            status="RESOURCE_BLOCKED",
            missing_roles={
                "Developer": 1,
                "Designer": 1,
            },
            diagnostic_path="resource_diagnostic.json",
            resolution="PENDING",
        )


if __name__ == "__main__":
    unittest.main()
