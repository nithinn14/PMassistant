import contextlib
import importlib.util
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

import pandas as pd

import telegram_bot
import sync_meetings
from tools import real_meet
from tools import meeting_tools
from tools import calendar_rsvp_reader
from tools import gmail_proposal_reader

from data_backend.registry import bind_backend, reset_bound_backend
from tools.real_meet import notify_project_level_meetings
from tools.save_prd_tool import save_prd
from tools.save_schedule_tool import save_schedule


class BasePathTestCase(unittest.TestCase):
    """Base test case providing stdout redirection for Windows cp1252 emoji compatibility."""

    def setUp(self):
        self._stdout_trap = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self._stdout_trap)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)


class TestSavePrdToolPaths(BasePathTestCase):
    """Regression tests verifying tools.save_prd_tool file operations remain independent of CWD."""

    def test_save_prd_writes_to_file_anchored_output_dir_independent_of_cwd(self):
        """save_prd writes PRD JSON to output dir anchored to __file__, regardless of process CWD."""
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)

            # Fake module path: <app_path>/tools/save_prd_tool.py
            fake_tool_path = app_path / "tools" / "save_prd_tool.py"
            fake_tool_path.parent.mkdir(parents=True, exist_ok=True)

            expected_output_dir = app_path / "output"
            product_name = "IsolatedPrdProject"
            expected_json = expected_output_dir / f"{product_name}_PRD.json"

            # Change process working directory to separate work directory
            os.chdir(work_path)

            with patch("tools.save_prd_tool.__file__", str(fake_tool_path)), \
                 patch("tools.save_prd_tool.save_prd_pdf", return_value=str(expected_output_dir / "test.pdf")) as mock_pdf:

                result = save_prd(
                    product_name=product_name,
                    problem_statement="Verify CWD independence.",
                    goals=["Goal A"],
                    user_personas=["Persona A"],
                    functional_requirements=["Requirement A"],
                    non_functional_requirements=["NFR A"],
                    constraints=["Constraint A"],
                    assumptions=["Assumption A"],
                    out_of_scope=["OOS A"],
                )

                # Verify function return and mock call
                self.assertEqual(result, {"project_name": product_name})
                mock_pdf.assert_called_once()

                # Verify file was written to the __file__-derived output directory
                self.assertTrue(expected_json.exists(), f"PRD JSON not found at {expected_json}")

                with open(expected_json, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)

                self.assertEqual(saved_data["product_name"], product_name)
                self.assertEqual(saved_data["problem_statement"], "Verify CWD independence.")
                self.assertEqual(saved_data["goals"], ["Goal A"])

                # Verify no files were created under process working directory
                self.assertFalse((work_path / f"{product_name}_PRD.json").exists())
                self.assertFalse((work_path / "output").exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()


class TestSaveScheduleToolPaths(BasePathTestCase):
    """Regression tests verifying tools.save_schedule_tool file operations remain independent of CWD."""

    def test_save_schedule_writes_status_to_file_anchored_output_dir_independent_of_cwd(self):
        """save_schedule writes notification status to output dir anchored to __file__, regardless of CWD."""
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()
        token = None

        mock_backend = MagicMock()
        employees_df = pd.DataFrame([
            {
                "Employee_Name": "Manager Mary",
                "Role": "Project Manager",
                "Email": "mary@example.com",
                "telegram_chat_id": "111",
            },
            {
                "Employee_Name": "Dev Dan",
                "Role": "Frontend Developer",
                "Email": "dan@example.com",
                "telegram_chat_id": "222",
            },
        ])
        mock_backend.read.return_value = employees_df

        try:
            token = bind_backend(mock_backend)

            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)

            # Fake module path: <app_path>/tools/save_schedule_tool.py
            # Line 218 uses Path(__file__).resolve().parents[1] -> app_path
            fake_tool_path = app_path / "tools" / "save_schedule_tool.py"
            fake_tool_path.parent.mkdir(parents=True, exist_ok=True)

            expected_output_dir = app_path / "output"
            project_name = "IsolatedSchedProject"
            expected_status_file = expected_output_dir / f"{project_name}_notification_status.json"

            # Change process working directory to separate work directory
            os.chdir(work_path)

            mock_cfg = {
                "telegram": {"enabled": False},
                "smtp": {
                    "email_env": "SENDER_EMAIL",
                    "password_env": "SENDER_PASSWORD",
                    "host": "smtp.test.com",
                    "port": 465,
                },
                "workflow": {"poll_interval_seconds": 30},
            }

            payload = json.dumps({
                "project_name": project_name,
                "tasks": [
                    {
                        "task_id": "T1",
                        "task_name": "Build UI",
                        "assigned_empl": "Dev Dan",
                        "time_days": 2.0,
                        "dependencies": [],
                    }
                ],
            })

            with patch("tools.save_schedule_tool.__file__", str(fake_tool_path)), \
                 patch("tools.save_schedule_tool.load_config", return_value=mock_cfg), \
                 patch("agents.communication.factory.build_email_notifier") as mock_notifier:

                # Disable email delivery by returning False from send
                mock_mailer = MagicMock()
                mock_mailer.send.return_value = False
                mock_notifier.return_value = mock_mailer

                result_json_str = save_schedule(payload)

                result = json.loads(result_json_str)
                self.assertEqual(result["project_name"], project_name)

                # Verify backend was invoked to save the schedule dataset
                mock_backend.write.assert_called_once()
                saved_dataset_name, saved_df = mock_backend.write.call_args[0]
                self.assertEqual(saved_dataset_name, f"{project_name}_Scheduled")
                self.assertEqual(len(saved_df), 1)

                # Verify notification delivery status JSON was saved in app_path / output
                self.assertTrue(
                    expected_status_file.exists(),
                    f"Notification status JSON not found at {expected_status_file}"
                )

                with open(expected_status_file, "r", encoding="utf-8") as f:
                    status_data = json.load(f)

                self.assertIn("Dev Dan", status_data)
                self.assertEqual(status_data["Dev Dan"]["telegram_sent"], False)
                self.assertEqual(status_data["Dev Dan"]["email_sent"], False)

                # Verify no files were created under process working directory
                self.assertFalse((work_path / f"{project_name}_notification_status.json").exists())
                self.assertFalse((work_path / "output").exists())

        finally:
            if token is not None:
                reset_bound_backend(token)
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()


class TestRealMeetPaths(BasePathTestCase):
    """Regression tests verifying tools.real_meet path anchors remain independent of CWD."""

    def test_real_meet_constants_anchored_to_source_file_location(self):
        """tools.real_meet path constants are initialized from __file__ location, not CWD."""
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name).resolve()
            work_path = Path(work_temp.name).resolve()

            # Set up an isolated module source file under app_path/tools/real_meet.py
            fake_tools_dir = app_path / "tools"
            fake_tools_dir.mkdir(parents=True, exist_ok=True)
            fake_real_meet_file = fake_tools_dir / "real_meet.py"

            # Copy actual real_meet.py source code to the isolated app structure
            import tools.real_meet as rm_src
            real_source_code = Path(rm_src.__file__).read_text(encoding="utf-8")
            fake_real_meet_file.write_text(real_source_code, encoding="utf-8")

            # Change process working directory to separate work directory before loading
            os.chdir(work_path)

            # Load the module in isolation from the temporary source location
            spec = importlib.util.spec_from_file_location("isolated_real_meet", str(fake_real_meet_file))
            isolated_rm = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated_rm)

            # BASE_DIR must be absolute and anchored to app_path (fake_real_meet_file.parents[1])
            self.assertTrue(isolated_rm.BASE_DIR.is_absolute())
            self.assertEqual(isolated_rm.BASE_DIR, app_path)
            self.assertNotEqual(isolated_rm.BASE_DIR, work_path)

            # Derived path constants must be anchored to app_path
            expected_output_dir = app_path / "output"
            self.assertEqual(isolated_rm.OUTPUT_DIR, expected_output_dir)
            self.assertEqual(isolated_rm.CREDENTIALS_PATH, app_path / "credentials.json")
            self.assertEqual(isolated_rm.TOKEN_PATH, app_path / "token.json")

            # Importing the module should not create the output directory.
            self.assertFalse(expected_output_dir.exists())
            self.assertFalse((work_path / "output").exists())

            # Verify none of the paths reside within or relative to the changed CWD
            self.assertFalse(str(isolated_rm.BASE_DIR).startswith(str(work_path)))
            self.assertFalse(str(isolated_rm.OUTPUT_DIR).startswith(str(work_path)))
            self.assertFalse(str(isolated_rm.CREDENTIALS_PATH).startswith(str(work_path)))
            self.assertFalse(str(isolated_rm.TOKEN_PATH).startswith(str(work_path)))

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()

    def test_notify_project_level_meetings_writes_to_output_dir_independent_of_cwd(self):
        """notify_project_level_meetings writes meeting files to OUTPUT_DIR regardless of CWD."""
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name).resolve()
            work_path = Path(work_temp.name).resolve()

            # Create synthetic input tasks.xlsx in app_path
            tasks_excel = app_path / "tasks.xlsx"
            task_df = pd.DataFrame([
                {
                    "task_id": "TASK-1",
                    "task_name": "Architecture Review",
                    "assigned_empl": "Dev User",
                    "assigned_email": "dev.user@example.com",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-05",
                    "RACI": "A",
                }
            ])
            task_df.to_excel(tasks_excel, index=False)

            # Change process working directory to separate work directory
            os.chdir(work_path)

            project_name = "MeetingIsolationProject"
            expected_output_dir = app_path / "output"
            expected_output_dir.mkdir(parents=True, exist_ok=True)
            expected_master_file = expected_output_dir / f"{project_name}_Meetings.xlsx"
            expected_parts_file = expected_output_dir / f"{project_name}_Participants.xlsx"

            mock_event = {
                "meet_link": "https://meet.google.com/test-meet-link",
                "event_id": "evt_test_12345",
                "start": "2026-08-01T10:00:00Z",
                "end": "2026-08-01T10:45:00Z",
            }

            mock_cfg = {
                "telegram": {"enabled": False},
                "smtp": {
                    "email_env": "SENDER_EMAIL",
                    "password_env": "SENDER_PASSWORD",
                    "host": "smtp.test.com",
                    "port": 465,
                },
            }

            with patch("tools.real_meet.OUTPUT_DIR", expected_output_dir), \
                 patch("tools.real_meet.BASE_DIR", app_path), \
                 patch("tools.real_meet.create_calendar_event", return_value=mock_event) as mock_cal, \
                 patch("config_loader.load_config", return_value=mock_cfg), \
                 patch("agents.communication.factory.build_email_notifier") as mock_mailer_factory:

                mock_mailer = MagicMock()
                mock_mailer.send.return_value = False
                mock_mailer_factory.return_value = mock_mailer

                result = notify_project_level_meetings(
                    task_excel_path=str(tasks_excel),
                    project_name=project_name,
                    meeting_type="Kickoff",
                )

                # Verify calendar event was created
                mock_cal.assert_called_once()

                # Verify meeting and participant Excel files exist in expected_output_dir
                self.assertTrue(
                    expected_master_file.exists(),
                    f"Meetings file not found at {expected_master_file}"
                )
                self.assertTrue(
                    expected_parts_file.exists(),
                    f"Participants file not found at {expected_parts_file}"
                )

                # Read created files and verify content
                master_df = pd.read_excel(expected_master_file)
                self.assertEqual(len(master_df), 1)
                self.assertEqual(master_df.iloc[0]["Project"], project_name)
                self.assertEqual(master_df.iloc[0]["Meeting_Type"], "Kickoff")
                self.assertEqual(master_df.iloc[0]["Event_ID"], "evt_test_12345")

                parts_df = pd.read_excel(expected_parts_file)
                self.assertEqual(len(parts_df), 1)
                self.assertEqual(parts_df.iloc[0]["Meeting_ID"], "evt_test_12345")
                self.assertEqual(parts_df.iloc[0]["Email"], "dev.user@example.com")

                # Verify no files were created under the process working directory
                self.assertFalse((work_path / f"{project_name}_Meetings.xlsx").exists())
                self.assertFalse((work_path / f"{project_name}_Participants.xlsx").exists())
                self.assertFalse((work_path / "output").exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()

    def test_import_does_not_create_output_directory(self):
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)
            fake_module_path = app_path / "tools" / "real_meet.py"
            fake_module_path.parent.mkdir(parents=True)
            output_path = app_path / "output"

            source = Path(real_meet.__file__).read_text(encoding="utf-8")
            fake_module_path.write_text(source, encoding="utf-8")

            os.chdir(work_path)

            spec = importlib.util.spec_from_file_location(
                "isolated_real_meet_no_output",
                fake_module_path,
            )
            module = importlib.util.module_from_spec(spec)

            with contextlib.redirect_stdout(io.StringIO()):
                spec.loader.exec_module(module)

            self.assertFalse(output_path.exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()

class TestTelegramBotPaths(BasePathTestCase):
    """Regression tests for Telegram bot output path resolution."""

    def test_output_dir_is_independent_of_cwd(self):
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)

            os.chdir(work_path)

            config = {
                "data_backend": {
                    "excel": {
                        "root_dir": ".",
                        "output_dir": "output",
                    }
                }
            }

            with patch("telegram_bot.APP_ROOT", app_path), \
                 patch("telegram_bot.load_config", return_value=config):
                result = telegram_bot._output_dir()

            self.assertEqual(result, app_path / "output")
            self.assertTrue(result.is_dir())
            self.assertFalse((work_path / "output").exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()

    def test_output_dir_respects_configured_relative_root(self):
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)

            config = {
                "data_backend": {
                    "excel": {
                        "root_dir": "storage",
                        "output_dir": "generated",
                    }
                }
            }

            os.chdir(work_path)

            with patch("telegram_bot.APP_ROOT", app_path), \
                 patch("telegram_bot.load_config", return_value=config):
                result = telegram_bot._output_dir()

            self.assertEqual(result, app_path / "storage" / "generated")
            self.assertTrue(result.is_dir())
            self.assertFalse((work_path / "storage").exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()



class TestSyncMeetingsPaths(BasePathTestCase):
    """Regression tests for meeting sync output paths."""

    def test_meeting_files_are_found_independent_of_cwd(self):
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)
            output_path = app_path / "output"
            output_path.mkdir()

            # Create a meeting file in the application output directory.
            (output_path / "TestProject_Meetings.xlsx").touch()

            os.chdir(work_path)

            with (
                patch("sync_meetings.DEFAULT_OUTPUT_DIR", output_path),
                patch("sync_meetings.load_dotenv"),
                patch("builtins.input", return_value="TestProject"),
                patch(
                    "sync_meetings.check_rsvp",
                    return_value=json.dumps({
                        "counts": {
                            "accepted": 1,
                            "declined": 0,
                            "awaiting": 0,
                        }
                    }),
                ),
                patch(
                    "sync_meetings.reschedule_meeting",
                    return_value=json.dumps({"status": "unchanged"}),
                ),
                patch("sync_meetings.send_reminder") as mock_reminder,
            ):
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    sync_meetings.main()

            output = captured.getvalue()

            self.assertIn("TestProject", output)
            self.assertIn(str(output_path / "TestProject_Meetings.xlsx"), output)
            self.assertFalse((work_path / "output").exists())
            mock_reminder.assert_not_called()

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()


class TestMeetingToolsPaths(BasePathTestCase):
    def test_output_dir_respects_configured_root_independent_of_cwd(self):
        app_temp = tempfile.TemporaryDirectory()
        work_temp = tempfile.TemporaryDirectory()
        orig_cwd = os.getcwd()

        try:
            app_path = Path(app_temp.name)
            work_path = Path(work_temp.name)

            config = {
                "data_backend": {
                    "excel": {
                        "root_dir": "storage",
                        "output_dir": "generated",
                    }
                }
            }

            os.chdir(work_path)

            with (
                patch("tools.meeting_tools.APP_ROOT", app_path),
                patch("tools.meeting_tools.load_config", return_value=config),
            ):
                result = meeting_tools._output_dir()

            expected = app_path / "storage" / "generated"
            self.assertEqual(result, expected)
            self.assertTrue(expected.is_dir())
            self.assertFalse((work_path / "storage").exists())

        finally:
            os.chdir(orig_cwd)
            app_temp.cleanup()
            work_temp.cleanup()

    def test_token_path_uses_central_token_file(self):
        expected = Path("temporary_app") / "token.json"

        with patch("tools.meeting_tools.TOKEN_FILE", expected):
            self.assertEqual(meeting_tools._token_path(), expected)


class TestCalendarRsvpReaderPaths(BasePathTestCase):
    def test_token_path_uses_central_token_file(self):
        expected = Path("temporary_app") / "token.json"

        with patch("tools.calendar_rsvp_reader.TOKEN_FILE", expected):
            self.assertEqual(calendar_rsvp_reader.TOKEN_FILE, expected)

    def test_missing_token_returns_none_without_authentication(self):
        missing_token = Path("temporary_app") / "missing_token.json"

        with (
            patch("tools.calendar_rsvp_reader.TOKEN_FILE", missing_token),
            patch("tools.calendar_rsvp_reader.Credentials.from_authorized_user_file") as mock_credentials,
        ):
            result = calendar_rsvp_reader.get_event_attendee_status("test_event")

        self.assertIsNone(result)
        mock_credentials.assert_not_called()


class TestGmailProposalReaderPaths(BasePathTestCase):
    def test_module_loads_env_from_central_path(self):
        with patch("dotenv.load_dotenv") as mock_load_dotenv:
            importlib.reload(gmail_proposal_reader)

            mock_load_dotenv.assert_called_once_with(
                gmail_proposal_reader.ENV_PATH
            )

        # Restore the module's normal load_dotenv reference.
        importlib.reload(gmail_proposal_reader)



if __name__ == "__main__":
    unittest.main()
