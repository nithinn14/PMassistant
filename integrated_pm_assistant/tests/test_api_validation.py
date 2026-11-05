"""
Regression tests for API validation, job status, and employee-file upload in api_server.py:

A. POST /api/start-project (4 tests):
   1. test_start_project_missing_name: Missing or blank project_name returns 400.
   2. test_start_project_missing_description: Missing or blank project_description returns 400.
   3. test_start_project_unsupported_backend: Unsupported data_backend returns 400.
   4. test_start_project_valid_spawns_job: Valid request returns job_id and running status (thread mocked).

B. GET /api/job-status/<job_id> (4 tests):
   5. test_job_status_unknown: Unknown job ID returns 404.
   6. test_job_status_running: Running job returns expected fields.
   7. test_job_status_completed: Completed job returns expected status.
   8. test_job_status_failed: Failed job returns its error message.

C. Employee upload and smart merge (6 tests):
   9. test_upload_unsupported_extension: Unsupported extension returns 400.
   10. test_upload_missing_file_part: Missing file part returns 400.
   11. test_upload_empty_filename: Empty filename returns 400.
   12. test_upload_ordinary_file_no_merge: Ordinary file (tasks.xlsx) uploads without triggering employee merging.
   13. test_smart_merge_preserves_existing_workload: Preserves historical workload when upload has blank fields.
   14. test_smart_merge_retains_new_employee: New employee in upload is retained with uploaded values.
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
import api_server


class BaseApiValidationTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_jobs = dict(api_server.jobs)
        self._orig_base_dir = api_server.BASE_DIR
        self._orig_output_dir = api_server.OUTPUT_DIR
        api_server.jobs.clear()
        api_server.app.config["TESTING"] = True
        self.client = api_server.app.test_client()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_base_dir = Path(self.tmp_dir.name)
        api_server.BASE_DIR = self.test_base_dir
        api_server.OUTPUT_DIR = self.test_base_dir / "output"

    def tearDown(self):
        self.tmp_dir.cleanup()
        api_server.jobs.clear()
        api_server.jobs.update(self._orig_jobs)
        api_server.BASE_DIR = self._orig_base_dir
        api_server.OUTPUT_DIR = self._orig_output_dir


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
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("project_name", data["error"])

        # Key completely missing
        res2 = self.client.post(
            "/api/start-project",
            json={"project_description": "Valid description"},
        )
        self.assertEqual(res2.status_code, 400)
        data2 = res2.get_json()
        self.assertIn("error", data2)

    def test_start_project_missing_description(self):
        """Missing or blank project_description returns 400."""
        # Blank string
        res = self.client.post(
            "/api/start-project",
            json={"project_name": "Valid Name", "project_description": "  \t "},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("project_description", data["error"])

        # Key completely missing
        res2 = self.client.post(
            "/api/start-project",
            json={"project_name": "Valid Name"},
        )
        self.assertEqual(res2.status_code, 400)
        data2 = res2.get_json()
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
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("data_backend must be one of", data["error"])

    @patch("api_server.threading.Thread")
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
        data = res.get_json()
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


# ==============================================================================
# B. GET /api/job-status/<job_id>
# ==============================================================================
class TestJobStatusApi(BaseApiValidationTestCase):
    """Status retrieval tests for GET /api/job-status/<job_id>."""

    def test_job_status_unknown(self):
        """Unknown job ID returns 404."""
        res = self.client.get("/api/job-status/unknown-uuid-00000000")
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
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
        data = res.get_json()
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
        data = res.get_json()
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
        data = res.get_json()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["error"], "Resource allocation failed: no available developers")


# ==============================================================================
# C. Employee upload and smart merge
# ==============================================================================
class TestEmployeeUploadAndMerge(BaseApiValidationTestCase):
    """File upload validation and smart-merge tests for POST /api/upload-file."""

    def test_upload_unsupported_extension(self):
        """Unsupported file extension returns 400."""
        payload = {
            "file": (io.BytesIO(b"%PDF-1.4 dummy pdf content"), "document.pdf")
        }
        res = self.client.post("/api/upload-file", data=payload, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("Only .xlsx, .csv, .json files are accepted", data["error"])

    def test_upload_missing_file_part(self):
        """Missing file part in multipart form returns 400."""
        res = self.client.post("/api/upload-file", data={}, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "No file provided")

    def test_upload_empty_filename(self):
        """Empty filename in uploaded file returns 400."""
        payload = {
            "file": (io.BytesIO(b"some content"), "")
        }
        res = self.client.post("/api/upload-file", data=payload, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Empty filename")

    def test_upload_ordinary_file_no_merge(self):
        """Ordinary file such as tasks.xlsx uploads without triggering employee merging."""
        df = pd.DataFrame([{"Task_ID": "T1", "Task_Name": "Initial Architecture"}])
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)

        payload = {
            "file": (buf, "tasks.xlsx")
        }
        res = self.client.post("/api/upload-file", data=payload, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
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

        payload = {
            "file": (buf, "employees.xlsx")
        }

        # Suppress console print to prevent Windows cp1252 emoji encoding issues
        with contextlib.redirect_stdout(io.StringIO()):
            res = self.client.post("/api/upload-file", data=payload, content_type="multipart/form-data")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
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

        payload = {
            "file": (buf, "employees.xlsx")
        }

        with contextlib.redirect_stdout(io.StringIO()):
            res = self.client.post("/api/upload-file", data=payload, content_type="multipart/form-data")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
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


if __name__ == "__main__":
    unittest.main()

