"""
Regression tests for Dashboard and project-data API in api_server.py:
1. resolve_project_name():
   - Exact match
   - Case-insensitive match
   - Partial substring single match
   - Ambiguous substring matches
   - Nonexistent project
   - Blank / whitespace query
   - Renamed job original_project_name lookup
2. GET /api/project-results/<project_name>:
   - Nonexistent project returns 404
   - Ambiguous matches returns 200 with ambiguous: True and match list
   - Complete payload assembly (PRD, Tasks, Assigned, Schedule, Notification Status, Meetings, Participants, RSVP summary)
   - NaN sanitization in Excel data
   - RSVP summary calculation (filtering by Event_ID, matching RSVP_Status and Response, capitalization)
   - Partial output files (graceful degradation when optional files are missing)
3. GET /api/projects:
   - Empty output directory returns empty list
   - Projects sorted by modification time (mtime) descending
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

import api_server


class BaseDashboardTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_jobs = dict(api_server.jobs)
        self._orig_output_dir = api_server.OUTPUT_DIR
        api_server.jobs.clear()
        api_server.app.config["TESTING"] = True
        self.client = api_server.app.test_client()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_output_dir = Path(self.tmp_dir.name)
        api_server.OUTPUT_DIR = self.test_output_dir

    def tearDown(self):
        self.tmp_dir.cleanup()
        api_server.jobs.clear()
        api_server.jobs.update(self._orig_jobs)
        api_server.OUTPUT_DIR = self._orig_output_dir


class TestResolveProjectName(BaseDashboardTestCase):
    """Tests for resolve_project_name() resolution logic."""

    def test_resolve_exact_match(self):
        """Exact case-sensitive match returns canonical name and empty match list."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")
        (self.test_output_dir / "Beta Project_PRD.json").write_text("{}", encoding="utf-8")

        canonical, matches = api_server.resolve_project_name("Alpha Project")
        self.assertEqual(canonical, "Alpha Project")
        self.assertEqual(matches, [])

    def test_resolve_case_insensitive_match(self):
        """Case-insensitive exact match resolves to canonical name."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")

        canonical, matches = api_server.resolve_project_name("alpha project")
        self.assertEqual(canonical, "Alpha Project")
        self.assertEqual(matches, [])

    def test_resolve_partial_substring_single_match(self):
        """Single substring match resolves to canonical name."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")
        (self.test_output_dir / "Beta Project_PRD.json").write_text("{}", encoding="utf-8")

        canonical, matches = api_server.resolve_project_name("Alpha")
        self.assertEqual(canonical, "Alpha Project")
        self.assertEqual(matches, [])

    def test_resolve_ambiguous_matches(self):
        """Multiple substring matches return None for canonical name and the full match list."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")
        (self.test_output_dir / "Beta Project_PRD.json").write_text("{}", encoding="utf-8")

        canonical, matches = api_server.resolve_project_name("Project")
        self.assertIsNone(canonical)
        self.assertEqual(sorted(matches), ["Alpha Project", "Beta Project"])

    def test_resolve_nonexistent_project(self):
        """Nonexistent project query returns None and an empty match list."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")

        canonical, matches = api_server.resolve_project_name("Gamma Project")
        self.assertIsNone(canonical)
        self.assertEqual(matches, [])

    def test_resolve_blank_and_whitespace_query(self):
        """Empty, whitespace-only, or None-like query returns None and an empty match list."""
        (self.test_output_dir / "Alpha Project_PRD.json").write_text("{}", encoding="utf-8")

        for blank in ["", "   ", "\t\n", None]:
            if blank is None:
                canonical, matches = api_server.resolve_project_name("")
            else:
                canonical, matches = api_server.resolve_project_name(blank)
            self.assertIsNone(canonical)
            self.assertEqual(matches, [])

    def test_resolve_renamed_job_original_name_lookup(self):
        """When an in-memory job was renamed, querying original_project_name resolves actual_project_name."""
        (self.test_output_dir / "Smart App Final_PRD.json").write_text("{}", encoding="utf-8")
        api_server.jobs["job-renamed-1"] = {
            "original_project_name": "Smart App Draft",
            "actual_project_name": "Smart App Final",
        }

        canonical, matches = api_server.resolve_project_name("Smart App Draft")
        self.assertEqual(canonical, "Smart App Final")
        self.assertEqual(matches, [])


class TestProjectResultsEndpoint(BaseDashboardTestCase):
    """Tests for GET /api/project-results/<project_name>."""

    def test_project_results_nonexistent_returns_404(self):
        """Nonexistent project returns 404 with error message."""
        response = self.client.get("/api/project-results/NonExistentProject")
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIn("error", data)
        self.assertIn("No project found matching", data["error"])

    def test_project_results_ambiguous_returns_200_with_matches(self):
        """Ambiguous query returns 200 with ambiguous=True and match candidates."""
        (self.test_output_dir / "Smart Tasks One_PRD.json").write_text("{}", encoding="utf-8")
        (self.test_output_dir / "Smart Tasks Two_PRD.json").write_text("{}", encoding="utf-8")

        response = self.client.get("/api/project-results/Smart%20Tasks")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("ambiguous"))
        self.assertEqual(data.get("query"), "Smart Tasks")
        self.assertEqual(sorted(data.get("matches", [])), ["Smart Tasks One", "Smart Tasks Two"])

    def test_project_results_complete_payload_and_rsvp_summary(self):
        """
        Complete project with PRD, Tasks, Assigned, Scheduled, Meetings, Participants,
        and Notification Status returns fully populated dictionary with correct RSVP metrics.
        """
        pname = "Test Complete Project"

        # 1. PRD JSON
        prd_data = {
            "project_name": pname,
            "executive_summary": "Synthetic PRD overview for regression test.",
            "phases": ["Phase 1", "Phase 2"],
        }
        (self.test_output_dir / f"{pname}_PRD.json").write_text(json.dumps(prd_data), encoding="utf-8")

        # 2. Tasks Excel
        tasks_df = pd.DataFrame([
            {"task_id": "T1", "task_name": "Task 1", "assigned_role": "Backend Developer", "time_days": 2.0},
            {"task_id": "T2", "task_name": "Task 2", "assigned_role": "UI/UX Designer", "time_days": 1.0},
        ])
        tasks_df.to_excel(self.test_output_dir / f"{pname}_Tasks.xlsx", index=False)

        # 3. Assigned Excel
        assigned_df = pd.DataFrame([
            {"task_id": "T1", "task_name": "Task 1", "Assigned_Employee": "Alice", "task_status": "assigned"},
            {"task_id": "T2", "task_name": "Task 2", "Assigned_Employee": "Bob", "task_status": "assigned"},
        ])
        assigned_df.to_excel(self.test_output_dir / f"{pname}_Assigned.xlsx", index=False)

        # 4. Scheduled Excel
        sched_df = pd.DataFrame([
            {"task_id": "T1", "start_date": "2026-11-02", "end_date": "2026-11-03", "time_days": 2.0},
            {"task_id": "T2", "start_date": "2026-11-04", "end_date": "2026-11-04", "time_days": 1.0},
        ])
        sched_df.to_excel(self.test_output_dir / f"{pname}_Scheduled.xlsx", index=False)

        # 5. Notification Status JSON
        notif_status = {
            "Alice": {"telegram_sent": True, "email_sent": True},
            "Bob": {"telegram_sent": True, "email_sent": False},
            "Charlie": {"telegram_sent": False, "email_sent": True},
            "Dave": {"telegram_sent": False, "email_sent": False},
        }
        (self.test_output_dir / f"{pname}_notification_status.json").write_text(
            json.dumps(notif_status), encoding="utf-8"
        )

        # 6. Meetings Excel (multiple meetings to verify latest is selected)
        meetings_df = pd.DataFrame([
            {"Event_ID": "evt-initial", "Meeting_Type": "Planning", "Status": "Completed"},
            {"Event_ID": "evt-kickoff", "Meeting_Type": "Kickoff", "Status": "Scheduled"},
        ])
        meetings_df.to_excel(self.test_output_dir / f"{pname}_Meetings.xlsx", index=False)

        # 7. Participants Excel (verifies Event_ID scoping and RSVP response mapping)
        parts_df = pd.DataFrame([
            # Prior meeting participant (should be excluded from latest meeting summary)
            {"Meeting_ID": "evt-initial", "Email": "prior@example.com", "RSVP_Status": "accepted"},
            # Latest meeting participants
            {"Meeting_ID": "evt-kickoff", "Email": "user1@example.com", "RSVP_Status": "accepted"},
            {"Meeting_ID": "evt-kickoff", "Email": "user2@example.com", "RSVP_Status": "declined"},
            {"Meeting_ID": "evt-kickoff", "Email": "user3@example.com", "Response": "tentative"},
            {"Meeting_ID": "evt-kickoff", "Email": "user4@example.com", "RSVP_Status": "needsAction"},
            {"Meeting_ID": "evt-kickoff", "Email": "user5@example.com", "RSVP_Status": None},
        ])
        parts_df.to_excel(self.test_output_dir / f"{pname}_Participants.xlsx", index=False)

        # Fetch results
        response = self.client.get(f"/api/project-results/{pname}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        # Validate top-level keys
        self.assertEqual(data["project_name"], pname)
        self.assertIn("prd", data)
        self.assertEqual(data["prd"]["project_name"], pname)
        self.assertEqual(data["prd_json_url"], f"/api/download/{pname}_PRD.json")
        self.assertEqual(data["prd_pdf_url"], f"/api/download/{pname}_PRD.pdf")

        # Validate tasks, assigned, schedule tables
        self.assertEqual(len(data["tasks"]), 2)
        self.assertEqual(len(data["assigned"]), 2)
        self.assertEqual(len(data["schedule"]), 2)

        # Validate notification status metrics
        ns = data["notification_status"]
        self.assertEqual(ns["total"], 4)
        self.assertEqual(ns["notified"], 3)
        self.assertEqual(ns["telegram_count"], 2)
        self.assertEqual(ns["email_count"], 2)
        self.assertEqual(ns["both"], ["Alice"])
        self.assertEqual(ns["telegram_only"], ["Bob"])
        self.assertEqual(ns["email_only"], ["Charlie"])
        self.assertEqual(ns["not_reached"], ["Dave"])

        # Validate meeting summary and RSVP counts
        summary = data["meeting_summary"]
        self.assertEqual(summary["event_id"], "evt-kickoff")
        self.assertEqual(summary["meeting_type"], "Kickoff")
        self.assertEqual(summary["status"], "Scheduled")
        self.assertEqual(summary["Accepted"], 1)
        self.assertEqual(summary["Declined"], 1)
        self.assertEqual(summary["Tentative"], 1)
        self.assertEqual(summary["Awaiting"], 2)  # needsAction + None

    def test_project_results_nan_sanitization(self):
        """Spreadsheet cells with NaN / Inf must be sanitized to None in JSON response."""
        pname = "NaN Sanitization Project"

        (self.test_output_dir / f"{pname}_PRD.json").write_text("{}", encoding="utf-8")

        # Excel with blank cells / NaNs
        tasks_df = pd.DataFrame([
            {"task_id": "T1", "notes": np.nan, "time_days": 1.0},
            {"task_id": "T2", "notes": None, "time_days": 2.0},
        ])
        tasks_df.to_excel(self.test_output_dir / f"{pname}_Tasks.xlsx", index=False)

        response = self.client.get(f"/api/project-results/{pname}")
        self.assertEqual(response.status_code, 200)

        # Confirm JSON parser accepts response without strict NaN errors
        parsed = json.loads(response.data.decode("utf-8"))
        tasks = parsed["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertIsNone(tasks[0]["notes"])
        self.assertIsNone(tasks[1]["notes"])

    def test_project_results_partial_files_graceful_degradation(self):
        """When optional project files are missing, project_results returns available data without error."""
        pname = "Partial Project"

        (self.test_output_dir / f"{pname}_PRD.json").write_text(
            json.dumps({"project_name": pname}), encoding="utf-8"
        )
        tasks_df = pd.DataFrame([{"task_id": "T1", "time_days": 1.0}])
        tasks_df.to_excel(self.test_output_dir / f"{pname}_Tasks.xlsx", index=False)

        response = self.client.get(f"/api/project-results/{pname}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["project_name"], pname)
        self.assertIn("prd", data)
        self.assertIn("tasks", data)
        self.assertNotIn("schedule", data)
        self.assertNotIn("meetings", data)
        self.assertNotIn("meeting_summary", data)
        self.assertNotIn("notification_status", data)


class TestListProjectsEndpoint(BaseDashboardTestCase):
    """Tests for GET /api/projects."""

    def test_list_projects_empty_output_dir(self):
        """Empty output directory returns an empty projects array."""
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data, {"projects": []})

    def test_list_projects_sorted_by_modification_time(self):
        """Projects are returned in descending order of PRD modification time."""
        now = time.time()

        # Older project: Project Alpha
        alpha_prd = self.test_output_dir / "Project Alpha_PRD.json"
        alpha_prd.write_text("{}", encoding="utf-8")
        os.utime(alpha_prd, (now - 200, now - 200))

        # Newer project: Project Beta
        beta_prd = self.test_output_dir / "Project Beta_PRD.json"
        beta_prd.write_text("{}", encoding="utf-8")
        os.utime(beta_prd, (now - 10, now - 10))

        # Add tasks file to Project Alpha to verify file count
        alpha_tasks = self.test_output_dir / "Project Alpha_Tasks.xlsx"
        pd.DataFrame([{"t": 1}]).to_excel(alpha_tasks, index=False)

        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        projects = data.get("projects", [])
        self.assertEqual(len(projects), 2)
        # Beta is newer -> must appear first
        self.assertEqual(projects[0]["name"], "Project Beta")
        self.assertEqual(projects[1]["name"], "Project Alpha")

        # Verify expected fields
        self.assertIn("files", projects[0])
        self.assertIn("modified", projects[0])
        self.assertGreaterEqual(projects[1]["files"], 2)


if __name__ == "__main__":
    unittest.main()

