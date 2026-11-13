import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.scheduling.gantt_tool import generate_gantt_chart
from tools.resources.resource_validator import (
    check_workflow_resolution,
    record_notification_issues,
    save_diagnostic_report,
    update_workflow_state,
)


class BasePathTestCase(unittest.TestCase):
    """Base test case providing stdout redirection for Windows cp1252 emoji compatibility."""

    def setUp(self):
        self._stdout_trap = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self._stdout_trap)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)


class TestResourceValidatorOutputPaths(BasePathTestCase):
    """Regression tests verifying tools.resource_validator uses DEFAULT_OUTPUT_DIR."""

    def test_save_diagnostic_report_creates_file_in_output_dir(self):
        """save_diagnostic_report creates the report in DEFAULT_OUTPUT_DIR and returns its path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_name = "AlphaProject"
            validation_result = {
                "total_shortages": 2,
                "missing_roles": {"Backend Developer": 1, "QA Engineer": 1},
                "shortages": [
                    {"role": "Backend Developer", "needed": 1, "available": 0},
                    {"role": "QA Engineer", "needed": 1, "available": 0},
                ],
            }

            with patch("tools.resources.resource_validator.DEFAULT_OUTPUT_DIR", tmp_path):
                returned_path = save_diagnostic_report(project_name, validation_result)

                expected_file = tmp_path / f"{project_name}_resource_diagnostic.json"
                self.assertTrue(expected_file.exists(), f"Expected file not found at {expected_file}")
                self.assertEqual(Path(returned_path).resolve(), expected_file.resolve())

                with open(expected_file, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)

                self.assertEqual(saved_data["project"], project_name)
                self.assertEqual(saved_data["total_shortages"], 2)
                self.assertEqual(saved_data["missing_roles"], {"Backend Developer": 1, "QA Engineer": 1})
                self.assertEqual(len(saved_data["shortage_details"]), 2)

    def test_record_notification_issues_creates_file_in_output_dir(self):
        """record_notification_issues writes issues to DEFAULT_OUTPUT_DIR and returns its path."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_name = "BetaProject"
            issues = [
                {"employee": "John Doe", "channel": "telegram", "error": "Chat ID not registered"},
                {"employee": "Jane Smith", "channel": "email", "error": "SMTP connection timed out"},
            ]

            with patch("tools.resources.resource_validator.DEFAULT_OUTPUT_DIR", tmp_path):
                returned_path = record_notification_issues(project_name, issues)

                expected_file = tmp_path / f"{project_name}_notification_issues.json"
                self.assertTrue(expected_file.exists(), f"Expected file not found at {expected_file}")
                self.assertEqual(Path(returned_path).resolve(), expected_file.resolve())

                with open(expected_file, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)

                self.assertEqual(saved_data["project"], project_name)
                self.assertEqual(saved_data["total_issues"], 2)
                self.assertEqual(saved_data["notification_issues"], issues)

    def test_record_notification_issues_synchronizes_existing_diagnostic_report(self):
        """record_notification_issues updates the existing diagnostic report in DEFAULT_OUTPUT_DIR."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_name = "GammaProject"
            validation_result = {
                "total_shortages": 1,
                "missing_roles": {"DevOps Engineer": 1},
                "shortages": [{"role": "DevOps Engineer", "needed": 1, "available": 0}],
            }
            issues = [
                {"employee": "DevOps Lead", "channel": "email", "error": "Bounced"},
            ]

            with patch("tools.resources.resource_validator.DEFAULT_OUTPUT_DIR", tmp_path):
                # First create the diagnostic report in the temporary directory
                diag_path_str = save_diagnostic_report(project_name, validation_result)
                diag_file = Path(diag_path_str)
                self.assertTrue(diag_file.exists())

                # Now record notification issues; it should sync into the diagnostic file
                record_notification_issues(project_name, issues)

                with open(diag_file, "r", encoding="utf-8") as f:
                    updated_diag = json.load(f)

                self.assertIn("notification_issues", updated_diag)
                self.assertEqual(updated_diag["notification_issues"], issues)

    def test_update_and_check_workflow_state_roundtrip(self):
        """update_workflow_state writes and check_workflow_resolution reads from DEFAULT_OUTPUT_DIR."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_name = "DeltaProject"
            missing_roles = {"Full Stack Developer": 1}
            diag_path = str(tmp_path / f"{project_name}_resource_diagnostic.json")
            notification_issues = [{"employee": "Bob", "error": "Network unreachable"}]

            with patch("tools.resources.resource_validator.DEFAULT_OUTPUT_DIR", tmp_path):
                # Verify nonexistent project state returns NO_STATE_FILE
                empty_state = check_workflow_resolution("NonexistentProject")
                self.assertEqual(empty_state.get("resolution"), "NO_STATE_FILE")
                self.assertEqual(empty_state.get("status"), "UNKNOWN")

                # Write workflow state
                returned_path = update_workflow_state(
                    project_name=project_name,
                    status="RESOURCE_BLOCKED",
                    missing_roles=missing_roles,
                    diagnostic_path=diag_path,
                    resolution="PENDING",
                    notification_issues=notification_issues,
                )

                expected_file = tmp_path / f"{project_name}_workflow_state.yaml"
                self.assertTrue(expected_file.exists(), f"Expected file not found at {expected_file}")
                self.assertEqual(Path(returned_path).resolve(), expected_file.resolve())

                # Read back using check_workflow_resolution
                state = check_workflow_resolution(project_name)

                self.assertEqual(state["project"], project_name)
                self.assertEqual(state["status"], "RESOURCE_BLOCKED")
                self.assertEqual(state["resolution"], "PENDING")
                self.assertEqual(state["diagnostic_file"], diag_path)
                self.assertEqual(state["missing_roles"], missing_roles)
                self.assertEqual(state["notification_issues"], notification_issues)

    def test_resource_validator_output_independent_of_cwd(self):
        """Resource validator functions write to DEFAULT_OUTPUT_DIR regardless of process working directory."""
        output_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            output_path = Path(output_temp.name)
            work_path = Path(work_temp.name)

            # Change process working directory to a separate temporary directory
            os.chdir(work_path)

            project_name = "CwdIsolatedProject"
            validation_result = {
                "total_shortages": 1,
                "missing_roles": {"Tester": 1},
                "shortages": [{"role": "Tester", "needed": 1, "available": 0}],
            }
            issues = [{"employee": "Sam", "channel": "email", "error": "Timeout"}]

            with patch("tools.resources.resource_validator.DEFAULT_OUTPUT_DIR", output_path):
                diag_path = save_diagnostic_report(project_name, validation_result)
                issues_path = record_notification_issues(project_name, issues)
                wf_path = update_workflow_state(
                    project_name=project_name,
                    status="RESOURCE_BLOCKED",
                    missing_roles={"Tester": 1},
                    diagnostic_path=diag_path,
                )
                state = check_workflow_resolution(project_name)

                # Verify files were created in DEFAULT_OUTPUT_DIR
                self.assertTrue((output_path / f"{project_name}_resource_diagnostic.json").exists())
                self.assertTrue((output_path / f"{project_name}_notification_issues.json").exists())
                self.assertTrue((output_path / f"{project_name}_workflow_state.yaml").exists())

                # Verify paths returned point into output_path
                self.assertEqual(Path(diag_path).parent.resolve(), output_path.resolve())
                self.assertEqual(Path(issues_path).parent.resolve(), output_path.resolve())
                self.assertEqual(Path(wf_path).parent.resolve(), output_path.resolve())

                # Verify resolution read returns correct state from output_path
                self.assertEqual(state.get("status"), "RESOURCE_BLOCKED")

                # Verify nothing was written to the process working directory
                self.assertFalse((work_path / f"{project_name}_resource_diagnostic.json").exists())
                self.assertFalse((work_path / f"{project_name}_notification_issues.json").exists())
                self.assertFalse((work_path / f"{project_name}_workflow_state.yaml").exists())
                self.assertFalse((work_path / "output").exists())
        finally:
            os.chdir(orig_cwd)
            output_temp.cleanup()
            work_temp.cleanup()


class TestGanttToolOutputPaths(BasePathTestCase):
    """Regression tests verifying tools.gantt_tool uses DEFAULT_OUTPUT_DIR."""

    def test_generate_gantt_chart_writes_to_default_output_dir(self):
        """generate_gantt_chart directs HTML and PNG output to DEFAULT_OUTPUT_DIR."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_name = "EpsilonProject"

            payload = {
                "project_name": project_name,
                "tasks": [
                    {
                        "task_id": "TASK-001",
                        "task_name": "Requirements Analysis",
                        "assigned_empl": "Alice Smith",
                        "assigned_email": "alice@example.com",
                        "project_manager": "pm@example.com",
                        "RACI": "A",
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-05",
                        "time_days": 5,
                        "dependencies": [],
                    },
                    {
                        "task_id": "TASK-002",
                        "task_name": "Architecture Design",
                        "assigned_empl": "Bob Jones",
                        "assigned_email": "bob@example.com",
                        "project_manager": "pm@example.com",
                        "RACI": "R",
                        "start_date": "2026-06-08",
                        "end_date": "2026-06-12",
                        "time_days": 5,
                        "dependencies": ["TASK-001"],
                    },
                ],
            }

            expected_html = tmp_path / f"{project_name}_Gantt.html"
            expected_png = tmp_path / f"{project_name}_Gantt.png"

            mock_fig = MagicMock()

            with patch("tools.scheduling.gantt_tool.DEFAULT_OUTPUT_DIR", tmp_path), \
                 patch("tools.scheduling.gantt_tool.px.timeline", return_value=mock_fig) as mock_timeline:

                result_json_str = generate_gantt_chart(json.dumps(payload))
                result = json.loads(result_json_str)

                # Verify success response
                self.assertEqual(result.get("status"), "Success", f"Unexpected failure: {result}")

                # Verify timeline figure calls
                mock_timeline.assert_called_once()
                mock_fig.update_yaxes.assert_called_once_with(autorange="reversed")
                mock_fig.write_html.assert_called_once_with(str(expected_html))
                mock_fig.write_image.assert_called_once_with(str(expected_png))

                # Verify returned paths in JSON point to the temporary directory
                self.assertEqual(Path(result["html_path"]).resolve(), expected_html.resolve())
                self.assertEqual(Path(result["png_path"]).resolve(), expected_png.resolve())
                self.assertEqual(Path(result["html_path"]).parent.resolve(), tmp_path.resolve())
                self.assertEqual(Path(result["png_path"]).parent.resolve(), tmp_path.resolve())

    def test_gantt_chart_output_independent_of_cwd(self):
        """generate_gantt_chart writes to DEFAULT_OUTPUT_DIR regardless of process working directory."""
        output_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            output_path = Path(output_temp.name)
            work_path = Path(work_temp.name)

            # Change process working directory to a separate temporary directory
            os.chdir(work_path)

            project_name = "CwdGanttProject"
            payload = {
                "project_name": project_name,
                "tasks": [
                    {
                        "task_id": "T-1",
                        "task_name": "Task 1",
                        "assigned_empl": "Dev One",
                        "assigned_email": "dev1@example.com",
                        "project_manager": "pm@example.com",
                        "RACI": "R",
                        "start_date": "2026-07-01",
                        "end_date": "2026-07-03",
                        "time_days": 3,
                        "dependencies": [],
                    }
                ],
            }

            expected_html = output_path / f"{project_name}_Gantt.html"
            expected_png = output_path / f"{project_name}_Gantt.png"

            mock_fig = MagicMock()

            with patch("tools.scheduling.gantt_tool.DEFAULT_OUTPUT_DIR", output_path), \
                 patch("tools.scheduling.gantt_tool.px.timeline", return_value=mock_fig) as mock_timeline:

                result_json_str = generate_gantt_chart(json.dumps(payload))
                result = json.loads(result_json_str)

                self.assertEqual(result.get("status"), "Success", f"Unexpected failure: {result}")
                mock_timeline.assert_called_once()
                mock_fig.write_html.assert_called_once_with(str(expected_html))
                mock_fig.write_image.assert_called_once_with(str(expected_png))

                # Verify paths point to output_path and not working directory
                self.assertEqual(Path(result["html_path"]).resolve(), expected_html.resolve())
                self.assertEqual(Path(result["png_path"]).resolve(), expected_png.resolve())
                self.assertEqual(Path(result["html_path"]).parent.resolve(), output_path.resolve())
                self.assertEqual(Path(result["png_path"]).parent.resolve(), output_path.resolve())
                self.assertNotEqual(Path(result["html_path"]).parent.resolve(), work_path.resolve())
                self.assertFalse((work_path / f"{project_name}_Gantt.html").exists())
                self.assertFalse((work_path / "output").exists())
        finally:
            os.chdir(orig_cwd)
            output_temp.cleanup()
            work_temp.cleanup()


if __name__ == "__main__":
    unittest.main()
