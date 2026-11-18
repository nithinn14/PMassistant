"""
Regression tests for API validation, job status, and employee-file upload in api_server.py:

A. POST /api/start-project (5 tests):
   1. test_start_project_missing_name: Missing or blank project_name returns 400.
   2. test_start_project_missing_description: Missing or blank project_description returns 400.
   3. test_start_project_unsupported_backend: Unsupported data_backend returns 400.
   4. test_start_project_valid_spawns_job: Valid request returns job_id and running status (thread mocked).
   5. test_start_project_omitted_backend_defaults_to_excel: Omitted data_backend defaults to excel.

B. GET /api/job-status/<job_id> (4 tests):
   6. test_job_status_unknown: Unknown job ID returns 404.
   7. test_job_status_running: Running job returns expected fields.
   8. test_job_status_completed: Completed job returns expected status.
   9. test_job_status_failed: Failed job returns its error message.

C. Employee upload and smart merge (6 tests):
   10. test_upload_unsupported_extension: Unsupported extension returns 400.
   11. test_upload_missing_file_part: Missing file part returns 400.
   12. test_upload_empty_filename: Empty filename returns 400.
   13. test_upload_ordinary_file_no_merge: Ordinary file (tasks.xlsx) uploads without triggering employee merging.
   14. test_smart_merge_preserves_existing_workload: Preserves historical workload when upload has blank fields.
   15. test_smart_merge_retains_new_employee: New employee in upload is retained with uploaded values.

D. POST /api/sync-meetings (5 tests):
   16. test_sync_meetings_missing_or_blank_project_name: Missing/blank project_name returns 400.
   17. test_sync_meetings_rsvp_error_returns_400: check_rsvp error returns 400.
   18. test_sync_meetings_orchestration_with_declined_participants: Declined count > 0 triggers send_reminder and reschedule.
   19. test_sync_meetings_orchestration_without_declined_participants: Declined count == 0 skips reminder, calls reschedule.
   20. test_sync_meetings_unexpected_exception_returns_500: Unexpected exception returns 500.

E. GET /api/ping (1 test):
   21. test_ping_success_and_iso_timestamp: GET /api/ping returns 200 with status 'ok' and valid ISO timestamp.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd
from fastapi.testclient import TestClient
import api_server
import api.services.files


class BaseApiValidationTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_jobs = dict(api_server.jobs)
        self._orig_base_dir = api_server.BASE_DIR
        self._orig_output_dir = api_server.OUTPUT_DIR
        self._orig_files_base_dir = api.services.files.BASE_DIR
        self._orig_files_output_dir = api.services.files.OUTPUT_DIR
        api_server.jobs.clear()
        self.client = TestClient(api_server.app)
        self.client.__enter__()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_base_dir = Path(self.tmp_dir.name)
        api_server.BASE_DIR = self.test_base_dir
        api_server.OUTPUT_DIR = self.test_base_dir / "output"
        api.services.files.BASE_DIR = self.test_base_dir
        api.services.files.OUTPUT_DIR = self.test_base_dir / "output"

    def tearDown(self):
        if hasattr(self, "client"):
            self.client.__exit__(None, None, None)
        self.tmp_dir.cleanup()
        api_server.jobs.clear()
        api_server.jobs.update(self._orig_jobs)
        api_server.BASE_DIR = self._orig_base_dir
        api_server.OUTPUT_DIR = self._orig_output_dir
        api.services.files.BASE_DIR = self._orig_files_base_dir
        api.services.files.OUTPUT_DIR = self._orig_files_output_dir


# ==============================================================================
# A. POST /api/start-project
# ==============================================================================
class TestStartProjectValidation(BaseApiValidationTestCase):
    """Validation and job initialization tests for POST /api/start-project."""

    def test_start_project_missing_name(self):
        """Missing or blank project_name returns 400."""
        # Empty string
        res = self.client.post(
            "/api/start-project",
            json={"project_name": "   ", "project_description": "Valid description"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("project_name", data["error"])

        # Key completely missing
        res2 = self.client.post(
            "/api/start-project",
            json={"project_description": "Valid description"},
        )
        self.assertEqual(res2.status_code, 400)
        data2 = res2.json()
        self.assertIn("error", data2)

    def test_start_project_missing_description(self):
        """Missing or blank project_description returns 400."""
        # Blank string
        res = self.client.post(
            "/api/start-project",
            json={"project_name": "Valid Name", "project_description": "  \t "},
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("project_description", data["error"])

        # Key completely missing
        res2 = self.client.post(
            "/api/start-project",
            json={"project_name": "Valid Name"},
        )
        self.assertEqual(res2.status_code, 400)
        data2 = res2.json()
        self.assertIn("error", data2)

    def test_start_project_unsupported_backend(self):
        """Unsupported data_backend returns 400."""
        res = self.client.post(
            "/api/start-project",
            json={
                "project_name": "Valid Name",
                "project_description": "Valid description",
                "data_backend": "unsupported_backend_xyz",
            },
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("data_backend must be one of", data["error"])

    @patch("api.routes.jobs.threading.Thread")
    def test_start_project_valid_spawns_job(self, mock_thread_cls):
        """Valid request returns job_id and running status without running the real pipeline."""
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        res = self.client.post(
            "/api/start-project",
            json={
                "project_name": "Regression Test Project",
                "project_description": "Regression test project description",
                "data_backend": "excel",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("job_id", data)
        self.assertEqual(data["status"], "running")

        job_id = data["job_id"]
        self.assertIn(job_id, api_server.jobs)
        self.assertEqual(api_server.jobs[job_id]["project_name"], "Regression Test Project")
        self.assertEqual(api_server.jobs[job_id]["status"], "running")

        # Confirm background thread was instantiated and started with daemon=True
        mock_thread_cls.assert_called_once()
        self.assertTrue(mock_thread_instance.daemon)
        mock_thread_instance.start.assert_called_once()

    @patch("api.routes.jobs.threading.Thread")
    def test_start_project_omitted_backend_defaults_to_excel(self, mock_thread_cls):
        """When data_backend is omitted, it defaults to 'excel' and spawns the job."""
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        res = self.client.post(
            "/api/start-project",
            json={
                "project_name": "Default Backend Project",
                "project_description": "Valid description without data_backend field",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("job_id", data)
        self.assertEqual(data["status"], "running")

        # Verify thread was called with backend_choice="excel"
        mock_thread_cls.assert_called_once()
        thread_kwargs = mock_thread_cls.call_args.kwargs

        self.assertIs(api_server._run_pipeline, thread_kwargs["target"])
        self.assertEqual(thread_kwargs["args"][3], "excel")

# ==============================================================================
# B. GET /api/job-status/<job_id>
# ==============================================================================
class TestJobStatusApi(BaseApiValidationTestCase):
    """Status retrieval tests for GET /api/job-status/<job_id>."""

    def test_job_status_unknown(self):
        """Unknown job ID returns 404."""
        res = self.client.get("/api/job-status/unknown-uuid-00000000")
        self.assertEqual(res.status_code, 404)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Job not found")

    def test_job_status_running(self):
        """Running job returns the expected status fields."""
        job_id = "test-job-running-id"
        api_server.jobs[job_id] = {
            "id": job_id,
            "project_name": "Active Project",
            "original_project_name": "Active Project",
            "actual_project_name": "Active Project",
            "status": "running",
            "current_step": "Generating PRD...",
            "completed_steps": ["Initializing..."],
            "logs": ["Starting pipeline..."],
            "error": None,
            "started_at": "2026-11-04T10:00:00",
        }

        res = self.client.get(f"/api/job-status/{job_id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["current_step"], "Generating PRD...")
        self.assertEqual(data["completed_steps"], ["Initializing..."])
        self.assertIsNone(data["error"])
        self.assertEqual(data["project_name"], "Active Project")
        self.assertEqual(data["original_project_name"], "Active Project")
        self.assertEqual(data["actual_project_name"], "Active Project")

    def test_job_status_completed(self):
        """Completed job returns the expected completed status."""
        job_id = "test-job-completed-id"
        api_server.jobs[job_id] = {
            "id": job_id,
            "project_name": "Finished Project",
            "original_project_name": "Finished Project",
            "actual_project_name": "Finished Project",
            "status": "completed",
            "current_step": "Execution Completed",
            "completed_steps": ["PRD", "Tasks", "Assigned", "Scheduled"],
            "logs": [],
            "error": None,
        }

        res = self.client.get(f"/api/job-status/{job_id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["current_step"], "Execution Completed")
        self.assertIsNone(data["error"])

    def test_job_status_failed(self):
        """Failed job returns its error message."""
        job_id = "test-job-failed-id"
        api_server.jobs[job_id] = {
            "id": job_id,
            "project_name": "Failed Project",
            "original_project_name": "Failed Project",
            "actual_project_name": "Failed Project",
            "status": "failed",
            "current_step": "Assigning Resources...",
            "completed_steps": ["PRD", "Tasks"],
            "logs": [],
            "error": "Resource allocation failed: no available developers",
        }

        res = self.client.get(f"/api/job-status/{job_id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["error"], "Resource allocation failed: no available developers")


# ==============================================================================
# C. Employee upload and smart merge
# ==============================================================================
class TestEmployeeUploadAndMerge(BaseApiValidationTestCase):
    """File upload validation and smart-merge tests for POST /api/upload-file."""

    def test_upload_unsupported_extension(self):
        """Unsupported file extension returns 400."""
        res = self.client.post(
            "/api/upload-file",
            files={"file": ("document.pdf", io.BytesIO(b"%PDF-1.4 dummy pdf content"), "application/pdf")},
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertIn("Only .xlsx, .csv, .json files are accepted", data["error"])

    def test_upload_missing_file_part(self):
        """Missing file part in multipart form returns 400."""
        res = self.client.post("/api/upload-file", data={})
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "No file provided")

    def test_upload_empty_filename(self):
        """Empty filename in uploaded file returns 400."""
        res = self.client.post(
            "/api/upload-file",
            files={"file": ("", io.BytesIO(b"some content"), "application/octet-stream")},
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Empty filename")

    def test_upload_ordinary_file_no_merge(self):
        """Ordinary file such as tasks.xlsx uploads without triggering employee merging."""
        df = pd.DataFrame([{"Task_ID": "T1", "Task_Name": "Initial Architecture"}])
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)

        res = self.client.post(
            "/api/upload-file",
            files={"file": ("tasks.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["saved_as"], "tasks.xlsx")
        self.assertNotIn("merged", data)

        saved_path = self.test_base_dir / "tasks.xlsx"
        self.assertTrue(saved_path.exists())
        saved_df = pd.read_excel(saved_path)
        self.assertEqual(len(saved_df), 1)
        self.assertEqual(saved_df.iloc[0]["Task_ID"], "T1")

    def test_smart_merge_preserves_existing_workload(self):
        """Existing employee workload is preserved when uploaded record has blank workload fields."""
        # 1. Populate initial synthetic employees.xlsx in the test base dir
        initial_data = [
            {
                "Employee_ID": "E101",
                "Employee_Name": "Alice Smith",
                "Email": "alice@example.com",
                "Role": "Backend Developer",
                "Current_Project": "Project Alpha",
                "Allocated_Hours": 32,
                "Free_Hours": 8,
            }
        ]
        initial_df = pd.DataFrame(initial_data)
        initial_path = self.test_base_dir / "employees.xlsx"
        initial_df.to_excel(initial_path, index=False)

        # 2. Uploaded data has updated role but blank/NaN workload fields
        upload_data = [
            {
                "Employee_ID": "E101",
                "Employee_Name": "Alice Smith",
                "Email": "alice@example.com",
                "Role": "Senior Backend Developer",
                "Current_Project": "",
                "Allocated_Hours": None,
                "Free_Hours": None,
            }
        ]
        upload_df = pd.DataFrame(upload_data)
        buf = io.BytesIO()
        upload_df.to_excel(buf, index=False)
        buf.seek(0)

        # Suppress console print to prevent Windows cp1252 emoji encoding issues
        with contextlib.redirect_stdout(io.StringIO()):
            res = self.client.post(
                "/api/upload-file",
                files={"file": ("employees.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("merged"))
        self.assertEqual(data.get("key_used"), "Email")

        # 3. Read back merged file and verify workload preservation and role update
        merged_df = pd.read_excel(initial_path)
        self.assertEqual(len(merged_df), 1)
        row = merged_df.iloc[0]
        self.assertEqual(row["Role"], "Senior Backend Developer")
        self.assertEqual(row["Current_Project"], "Project Alpha")
        self.assertEqual(row["Allocated_Hours"], 32)
        self.assertEqual(row["Free_Hours"], 8)

    def test_smart_merge_retains_new_employee(self):
        """A new employee in the upload is retained with the uploaded values."""
        # 1. Populate initial synthetic employees.xlsx with only Alice
        initial_data = [
            {
                "Employee_ID": "E101",
                "Employee_Name": "Alice Smith",
                "Email": "alice@example.com",
                "Role": "Backend Developer",
                "Current_Project": "Project Alpha",
                "Allocated_Hours": 24,
                "Free_Hours": 16,
            }
        ]
        initial_df = pd.DataFrame(initial_data)
        initial_path = self.test_base_dir / "employees.xlsx"
        initial_df.to_excel(initial_path, index=False)

        # 2. Uploaded file contains Alice (with empty workload) AND Bob (a new hire with 40 free hours)
        upload_data = [
            {
                "Employee_ID": "E101",
                "Employee_Name": "Alice Smith",
                "Email": "alice@example.com",
                "Role": "Backend Developer",
                "Current_Project": None,
                "Allocated_Hours": None,
                "Free_Hours": None,
            },
            {
                "Employee_ID": "E102",
                "Employee_Name": "Bob Jones",
                "Email": "bob@example.com",
                "Role": "Frontend Developer",
                "Current_Project": "Onboarding",
                "Allocated_Hours": 0,
                "Free_Hours": 40,
            },
        ]
        upload_df = pd.DataFrame(upload_data)
        buf = io.BytesIO()
        upload_df.to_excel(buf, index=False)
        buf.seek(0)

        with contextlib.redirect_stdout(io.StringIO()):
            res = self.client.post(
                "/api/upload-file",
                files={"file": ("employees.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("merged"))

        # 3. Read back merged file and verify both employees
        merged_df = pd.read_excel(initial_path)
        self.assertEqual(len(merged_df), 2)

        alice_row = merged_df[merged_df["Email"] == "alice@example.com"].iloc[0]
        self.assertEqual(alice_row["Current_Project"], "Project Alpha")
        self.assertEqual(alice_row["Allocated_Hours"], 24)
        self.assertEqual(alice_row["Free_Hours"], 16)

        bob_row = merged_df[merged_df["Email"] == "bob@example.com"].iloc[0]
        self.assertEqual(bob_row["Role"], "Frontend Developer")
        self.assertEqual(bob_row["Current_Project"], "Onboarding")
        self.assertEqual(bob_row["Allocated_Hours"], 0)
        self.assertEqual(bob_row["Free_Hours"], 40)


# ==============================================================================
# D. POST /api/sync-meetings
# ==============================================================================
class TestSyncMeetingsApi(BaseApiValidationTestCase):
    """Validation and orchestration tests for POST /api/sync-meetings."""

    def test_sync_meetings_missing_or_blank_project_name(self):
        """Missing or blank project_name returns 400 and exact error JSON."""
        # Blank project_name
        res = self.client.post("/api/sync-meetings", json={"project_name": "   "})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"error": "project_name is required"})

        # Missing project_name key
        res2 = self.client.post("/api/sync-meetings", json={})
        self.assertEqual(res2.status_code, 400)
        self.assertEqual(res2.json(), {"error": "project_name is required"})

    @patch("api.routes.meetings.check_rsvp")
    def test_sync_meetings_rsvp_error_returns_400(self, mock_check_rsvp):
        """When check_rsvp returns an error payload, returns 400 and exact error JSON."""
        mock_check_rsvp.return_value = json.dumps({"error": "No meetings file found for 'Test Project'"})

        res = self.client.post("/api/sync-meetings", json={"project_name": "Test Project"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"error": "No meetings file found for 'Test Project'"})

    @patch("api.routes.meetings.project_results")
    @patch("api.routes.meetings.reschedule_meeting")
    @patch("api.routes.meetings.send_reminder")
    @patch("api.routes.meetings.check_rsvp")
    def test_sync_meetings_orchestration_with_declined_participants(
        self, mock_check_rsvp, mock_send_reminder, mock_reschedule, mock_project_results
    ):
        """When declined count > 0, send_reminder and reschedule_meeting are both called."""
        mock_check_rsvp.return_value = json.dumps({
            "status": "success",
            "counts": {"accepted": 1, "declined": 2, "tentative": 0, "awaiting": 0}
        })
        mock_project_results.return_value = api_server._safe_jsonify({
            "project_name": "Test Project",
            "status": "refreshed"
        })

        res = self.client.post(
            "/api/sync-meetings",
            json={"project_name": "Test Project", "meeting_type": "Kickoff"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"project_name": "Test Project", "status": "refreshed"})

        mock_check_rsvp.assert_called_once_with("Test Project", "Kickoff")
        mock_send_reminder.assert_called_once_with("Test Project", "Kickoff")
        mock_reschedule.assert_called_once_with("Test Project", "Kickoff")
        mock_project_results.assert_called_once_with("Test Project")

    @patch("api.routes.meetings.project_results")
    @patch("api.routes.meetings.reschedule_meeting")
    @patch("api.routes.meetings.send_reminder")
    @patch("api.routes.meetings.check_rsvp")
    def test_sync_meetings_orchestration_without_declined_participants(
        self, mock_check_rsvp, mock_send_reminder, mock_reschedule, mock_project_results
    ):
        """When declined count == 0, send_reminder is NOT called, but reschedule_meeting is called."""
        mock_check_rsvp.return_value = json.dumps({
            "status": "success",
            "counts": {"accepted": 3, "declined": 0, "tentative": 1, "awaiting": 0}
        })
        mock_project_results.return_value = api_server._safe_jsonify({
            "project_name": "Test Project",
            "status": "refreshed"
        })

        res = self.client.post(
            "/api/sync-meetings",
            json={"project_name": "Test Project", "meeting_type": "Kickoff"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"project_name": "Test Project", "status": "refreshed"})

        mock_check_rsvp.assert_called_once_with("Test Project", "Kickoff")
        mock_send_reminder.assert_not_called()
        mock_reschedule.assert_called_once_with("Test Project", "Kickoff")
        mock_project_results.assert_called_once_with("Test Project")

    @patch("api.routes.meetings.check_rsvp")
    def test_sync_meetings_unexpected_exception_returns_500(self, mock_check_rsvp):
        """Unexpected exception in sync flow returns 500 and exact error JSON."""
        mock_check_rsvp.side_effect = RuntimeError("Google Calendar API connection refused")

        res = self.client.post("/api/sync-meetings", json={"project_name": "Test Project"})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json(), {"error": "Google Calendar API connection refused"})


# ==============================================================================
# E. GET /api/ping
# ==============================================================================
class TestPingApi(BaseApiValidationTestCase):
    """Tests for GET /api/ping."""

    def test_ping_success_and_iso_timestamp(self):
        """GET /api/ping returns 200, status 'ok', and a valid ISO timestamp."""
        from datetime import datetime

        res = self.client.get("/api/ping")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("time", data)

        # Validate that 'time' parses as an ISO-formatted datetime
        parsed_dt = datetime.fromisoformat(data["time"])
        self.assertIsInstance(parsed_dt, datetime)


if __name__ == "__main__":
    unittest.main()

