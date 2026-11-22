"""
test_integration_checkboxes.py
------------------------------
Focused regression tests for Phase 1.1: Setup integration checkboxes.

Verifies:
1. All integrations enabled.
2. All integrations disabled.
3. Each integration independently enabled/disabled:
   - google_calendar: Calendar event creation, RSVP checks, reminders, rescheduling.
   - gmail: task assignment emails, shortage alert emails, meeting invite emails, reminders, consensus.
   - telegram: task assignment DMs, shortage alert Telegrams, meeting invite Telegrams, listener task.
   - gantt: Gantt chart generation only (schedule persistence still works).
4. Missing integrations field preserves default all-enabled behavior.
5. Disabled channels do not construct notifiers or send messages.
6. Concurrent project runs maintain isolation without sharing or overwriting integration settings.
7. Validation of integrations schema: unknown keys rejected, partial settings retain defaults.
8. Reminder delivery outcomes: both disabled, email failure with telegram disabled,
   email failure with telegram success, email success with telegram failure.
"""

import unittest
from unittest.mock import patch, MagicMock, ANY
import json
import os
from pathlib import Path
import pandas as pd
from pydantic import ValidationError

from api.schemas import StartProjectRequest
from api.routes.jobs import start_project
from agents.orchestrator_agent import build_orchestrator
from agents.communication.communication_agent import build_communication_agent, _meeting_creator_logic
from meetings.services.reminder_service import ReminderService
from meetings.services.consensus_service import ConsensusService
from meetings.services.meeting_rescheduler import MeetingRescheduler
from tools.scheduling.save_schedule_tool import save_schedule
from meetings.integrations.real_meet import notify_project_level_meetings
from communication.factory import build_notification_agent
from agents.communication.notification_agent import NotificationAgent


class TestIntegrationCheckboxes(unittest.TestCase):

    def test_schema_accepts_integrations(self):
        """StartProjectRequest schema parses integrations dict correctly."""
        req = StartProjectRequest(
            project_name="TestProj",
            project_description="TestDesc",
            integrations={
                "google_calendar": False,
                "gmail": False,
                "telegram": True,
                "gantt": False,
            }
        )
        self.assertEqual(req.integrations["google_calendar"], False)
        self.assertEqual(req.integrations["gmail"], False)
        self.assertEqual(req.integrations["telegram"], True)
        self.assertEqual(req.integrations["gantt"], False)

    def test_schema_defaults_integrations_all_enabled(self):
        """StartProjectRequest without integrations defaults to all True (backward compatible)."""
        req = StartProjectRequest(
            project_name="TestProj",
            project_description="TestDesc",
        )
        self.assertEqual(
            req.integrations,
            {"google_calendar": True, "gmail": True, "telegram": True, "gantt": True}
        )

    def test_schema_rejects_unknown_keys(self):
        """StartProjectRequest rejects any unknown integration keys."""
        with self.assertRaises(ValidationError) as ctx:
            StartProjectRequest(
                project_name="TestProj",
                project_description="TestDesc",
                integrations={"unknown_key": True, "gmail": False}
            )
        self.assertIn("Unknown integration key(s): unknown_key", str(ctx.exception))

    def test_schema_partial_settings_retain_defaults(self):
        """StartProjectRequest with partial settings fills omitted keys with True."""
        req = StartProjectRequest(
            project_name="TestProj",
            project_description="TestDesc",
            integrations={"gmail": False}
        )
        self.assertEqual(req.integrations["gmail"], False)
        self.assertEqual(req.integrations["google_calendar"], True)
        self.assertEqual(req.integrations["telegram"], True)
        self.assertEqual(req.integrations["gantt"], True)

    @patch("api.routes.jobs._run_pipeline")
    def test_start_project_passes_integrations_to_pipeline(self, mock_pipeline):
        """start_project endpoint extracts integrations and forwards to _run_pipeline."""
        custom_integrations = {
            "google_calendar": False,
            "gmail": True,
            "telegram": False,
            "gantt": False,
        }
        req = StartProjectRequest(
            project_name="TestProj",
            project_description="TestDesc",
            data_backend="excel",
            integrations=custom_integrations,
        )
        res = start_project(req)
        self.assertEqual(res.status_code, 200)
        mock_pipeline.assert_called_once()
        args, kwargs = mock_pipeline.call_args
        # args: (job_id, project_name, project_description, backend_choice, integrations)
        self.assertEqual(args[1], "TestProj")
        self.assertEqual(args[2], "TestDesc")
        self.assertEqual(args[3], "excel")
        self.assertEqual(args[4], custom_integrations)

    @patch("api.routes.jobs._run_pipeline")
    def test_start_project_default_integrations_forwarded(self, mock_pipeline):
        """start_project endpoint without explicit integrations forwards default all-enabled dict."""
        req = StartProjectRequest(
            project_name="TestProj",
            project_description="TestDesc",
        )
        res = start_project(req)
        self.assertEqual(res.status_code, 200)
        mock_pipeline.assert_called_once()
        args, kwargs = mock_pipeline.call_args
        self.assertEqual(
            args[4],
            {"google_calendar": True, "gmail": True, "telegram": True, "gantt": True}
        )

    def test_orchestrator_builds_with_default_integrations(self):
        """build_orchestrator without integrations applies all-true defaults."""
        orch = build_orchestrator()
        self.assertIsNotNone(orch)
        names = [sub.name for sub in orch.sub_agents]
        self.assertIn("communication_agent", names)
        comm_agent = next(s for s in orch.sub_agents if s.name == "communication_agent")
        comm_sub_names = [s.name for s in comm_agent.sub_agents]
        self.assertIn("meeting_sync_agent", comm_sub_names)

    def test_orchestrator_calendar_disabled_skips_meeting_sync_agent(self):
        """When google_calendar=False, communication_agent omits meeting_sync_agent."""
        orch = build_orchestrator(integrations={"google_calendar": False})
        comm_agent = next(s for s in orch.sub_agents if s.name == "communication_agent")
        comm_sub_names = [s.name for s in comm_agent.sub_agents]
        self.assertNotIn("meeting_sync_agent", comm_sub_names)
        self.assertIn("meeting_creator", comm_sub_names)

    @patch("meetings.tools.meeting_tools.create_meeting")
    def test_meeting_creator_calendar_disabled_skips_creation(self, mock_create):
        """When google_calendar=False, _meeting_creator_logic does not call create_meeting."""
        with patch("agents.communication.communication_agent.get_backend") as mock_backend:
            mock_be = MagicMock()
            mock_be.read.return_value = pd.DataFrame([{"assigned_empl": "Dev"}])
            mock_backend.return_value = mock_be

            res_str = _meeting_creator_logic(
                json.dumps({"project_name": "CalOffProj"}),
                integrations={"google_calendar": False}
            )
            mock_create.assert_not_called()
            data = json.loads(res_str)
            self.assertEqual(data["kickoff_result"]["status"], "skipped")
            self.assertIn("Google Calendar disabled", data["kickoff_result"]["reason"])

    def test_concurrent_project_isolation(self):
        """Invoke scoped tools from two communication agents with different settings; verify no cross-contamination."""
        integrations_a = {"google_calendar": True, "gmail": False, "telegram": False}
        integrations_b = {"google_calendar": True, "gmail": True, "telegram": True}

        comm_a = build_communication_agent(integrations=integrations_a)
        comm_b = build_communication_agent(integrations=integrations_b)

        sync_a = next(s for s in comm_a.sub_agents if s.name == "meeting_sync_agent")
        sync_b = next(s for s in comm_b.sub_agents if s.name == "meeting_sync_agent")

        tool_remind_a = next(t for t in sync_a.tools if t.name == "_scoped_send_reminder")
        tool_remind_b = next(t for t in sync_b.tools if t.name == "_scoped_send_reminder")

        tool_resched_a = next(t for t in sync_a.tools if t.name == "_scoped_reschedule_meeting")
        tool_resched_b = next(t for t in sync_b.tools if t.name == "_scoped_reschedule_meeting")

        with patch("agents.communication.communication_agent.send_reminder") as mock_remind, \
             patch("agents.communication.communication_agent.reschedule_meeting") as mock_resched:

            tool_remind_a.func("ProjectA", "Kickoff")
            mock_remind.assert_called_with(
                project_name="ProjectA",
                meeting_type="Kickoff",
                integrations=integrations_a,
            )

            tool_remind_b.func("ProjectB", "Kickoff")
            mock_remind.assert_called_with(
                project_name="ProjectB",
                meeting_type="Kickoff",
                integrations=integrations_b,
            )

            tool_resched_a.func("ProjectA", "Kickoff")
            mock_resched.assert_called_with(
                project_name="ProjectA",
                meeting_type="Kickoff",
                integrations=integrations_a,
            )

            tool_resched_b.func("ProjectB", "Kickoff")
            mock_resched.assert_called_with(
                project_name="ProjectB",
                meeting_type="Kickoff",
                integrations=integrations_b,
            )

    @patch("communication.factory.build_email_notifier")
    def test_save_schedule_disabled_notifications(self, mock_build_email):
        """When gmail=False and telegram=False, save_schedule sends no emails or Telegram messages."""
        input_data = {
            "project_name": "TestSchedProj",
            "tasks": [
                {
                    "task_id": "T1",
                    "task_name": "Task 1",
                    "assigned_empl": "Test Dev",
                    "time_days": 1,
                    "assigned_role": "Developer"
                }
            ]
        }
        with patch("tools.scheduling.save_schedule_tool.get_backend") as mock_backend, \
             patch("communication.telegram.telegram_notifier.TelegramNotifier") as mock_tg:

            mock_be_instance = MagicMock()
            mock_be_instance.read.return_value = pd.DataFrame([
                {"Employee_Name": "Test Dev", "Role": "Project Manager", "Email": "dev@test.com", "telegram_chat_id": "12345"}
            ])
            mock_backend.return_value = mock_be_instance

            save_schedule(json.dumps(input_data), integrations={"gmail": False, "telegram": False})

            mock_build_email.assert_not_called()
            mock_tg.assert_not_called()

    @patch("communication.factory.build_email_notifier")
    def test_save_schedule_disabled_gmail_only(self, mock_build_email):
        """When gmail=False but telegram=True, email notifier is not called."""
        input_data = {
            "project_name": "TestSchedProj2",
            "tasks": [
                {
                    "task_id": "T1",
                    "task_name": "Task 1",
                    "assigned_empl": "Test Dev",
                    "time_days": 1,
                    "assigned_role": "Developer"
                }
            ]
        }
        with patch("tools.scheduling.save_schedule_tool.get_backend") as mock_backend, \
             patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock_token"}):

            mock_be_instance = MagicMock()
            mock_be_instance.read.return_value = pd.DataFrame([
                {"Employee_Name": "Test Dev", "Role": "Project Manager", "Email": "dev@test.com"}
            ])
            mock_backend.return_value = mock_be_instance

            save_schedule(json.dumps(input_data), integrations={"gmail": False, "telegram": True})
            mock_build_email.assert_not_called()

    @patch("tools.scheduling.gantt_tool.generate_gantt_chart")
    def test_scheduler_agent_gantt_disabled(self, mock_gantt):
        """When gantt=False, scheduler_logic skips generate_gantt_chart."""
        from agents.scheduler_agent import scheduler_logic

        with patch("agents.scheduler_agent.save_schedule") as mock_save:
            mock_save.return_value = json.dumps({"project_name": "P1", "tasks": []})

            scheduler_logic("{}", integrations={"gantt": False})

            mock_save.assert_called_once_with(input_json="{}", integrations={"gantt": False})
            mock_gantt.assert_not_called()

    @patch("agents.scheduler_agent.generate_gantt_chart")
    def test_scheduler_agent_gantt_enabled(self, mock_gantt):
        """When gantt=True, scheduler_logic invokes generate_gantt_chart."""
        from agents.scheduler_agent import scheduler_logic

        with patch("agents.scheduler_agent.save_schedule") as mock_save:
            mock_save.return_value = json.dumps({"project_name": "P1", "tasks": []})

            scheduler_logic("{}", integrations={"gantt": True})

            mock_save.assert_called_once_with(input_json="{}", integrations={"gantt": True})
            mock_gantt.assert_called_once_with(input_json=mock_save.return_value)

    # -------------------------------------------------------------------------
    # Reminder Delivery Outcomes
    # -------------------------------------------------------------------------

    def _setup_reminder_dfs(self):
        meetings_df = pd.DataFrame([{
            "Project": "TestProject",
            "Meeting_Type": "Kickoff",
            "Event_ID": "evt-123",
            "Status": "Scheduled",
        }])
        participants_df = pd.DataFrame([{
            "Meeting_ID": "evt-123",
            "Email": "declined@example.com",
            "Role": "R",
            "Template_Sent": "",
            "Reason": "",
            "Suggested_Time": "",
        }])
        employees_df = pd.DataFrame([{
            "Employee_Name": "Declined Dev",
            "Email": "declined@example.com",
            "telegram_chat_id": "999888",
        }])
        rsvp_status = {
            "declined@example.com": {"response": "declined"}
        }
        return meetings_df, participants_df, employees_df, rsvp_status

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_both_channels_disabled(self, mock_rsvp, mock_read, mock_to_excel):
        """When both channels disabled (notifiers are None), Template_Sent is not set."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=None, telegram_notifier=None)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], [])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "")
        mock_to_excel.assert_not_called()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_email_fail_telegram_disabled(self, mock_rsvp, mock_read, mock_to_excel):
        """When email fails and Telegram is disabled, Template_Sent is not set."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.side_effect = RuntimeError("SMTP connection error")

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=None)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], [])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "")
        mock_to_excel.assert_not_called()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_email_fail_telegram_success(self, mock_rsvp, mock_read, mock_to_excel):
        """When email fails but Telegram succeeds, Template_Sent is set to YES."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.side_effect = RuntimeError("SMTP connection error")
        mock_tg = MagicMock()
        mock_tg.send_reschedule_proposal_request.return_value = True

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=mock_tg)
        res = service.send_reminders("TestProject", "Kickoff")

        mock_tg.send_reschedule_proposal_request.assert_called_once_with(
            chat_id="999888",
            project_name="TestProject",
            employee_name="Declined Dev",
        )
        self.assertEqual(res["escalated"], ["declined@example.com"])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "YES")
        mock_to_excel.assert_called_once()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_email_success_telegram_fail(self, mock_rsvp, mock_read, mock_to_excel):
        """When email succeeds but Telegram fails, Template_Sent is set to YES."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.return_value = True
        mock_tg = MagicMock()
        mock_tg.send_reschedule_proposal_request.side_effect = RuntimeError("Telegram API timeout")

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=mock_tg)
        res = service.send_reminders("TestProject", "Kickoff")

        mock_email.send_reschedule_proposal_request.assert_called_once_with(
            "declined@example.com",
            "TestProject",
        )
        self.assertEqual(res["escalated"], ["declined@example.com"])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "YES")
        mock_to_excel.assert_called_once()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_gmail_returns_false_telegram_disabled(self, mock_rsvp, mock_read, mock_to_excel):
        """When Gmail send returns False and Telegram is disabled, Template_Sent is not updated."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.return_value = False

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=None)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], [])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "")
        mock_to_excel.assert_not_called()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_telegram_returns_false_gmail_disabled(self, mock_rsvp, mock_read, mock_to_excel):
        """When Telegram send returns False and Gmail is disabled, Template_Sent is not updated."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_tg = MagicMock()
        mock_tg.send_reschedule_proposal_request.return_value = False

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=None, telegram_notifier=mock_tg)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], [])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "")
        mock_to_excel.assert_not_called()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_both_channels_return_false(self, mock_rsvp, mock_read, mock_to_excel):
        """When both Gmail and Telegram return False, Template_Sent is not updated."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.return_value = False
        mock_tg = MagicMock()
        mock_tg.send_reschedule_proposal_request.return_value = False

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=mock_tg)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], [])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "")
        mock_to_excel.assert_not_called()

    @patch("pandas.DataFrame.to_excel")
    @patch("pandas.read_excel")
    @patch("meetings.services.reminder_service.get_event_attendee_status")
    def test_reminder_delivery_gmail_false_telegram_true(self, mock_rsvp, mock_read, mock_to_excel):
        """When Gmail returns False but Telegram returns True, delivery succeeds and sets Template_Sent."""
        meetings_df, participants_df, employees_df, rsvp_status = self._setup_reminder_dfs()
        mock_rsvp.return_value = rsvp_status
        mock_read.side_effect = lambda path: {
            "TestProject_Meetings.xlsx": meetings_df,
            "TestProject_Participants.xlsx": participants_df,
            "employees.xlsx": employees_df,
        }[Path(path).name]

        mock_email = MagicMock()
        mock_email.send_reschedule_proposal_request.return_value = False
        mock_tg = MagicMock()
        mock_tg.send_reschedule_proposal_request.return_value = True

        service = ReminderService(output_dir=Path("/mock/output"), email_notifier=mock_email, telegram_notifier=mock_tg)
        res = service.send_reminders("TestProject", "Kickoff")

        self.assertEqual(res["escalated"], ["declined@example.com"])
        self.assertEqual(participants_df.iloc[0]["Template_Sent"], "YES")
        mock_to_excel.assert_called_once()

    def test_notification_agent_factory_respects_integrations(self):
        """build_notification_agent with disabled channels passes None notifiers."""
        cfg = {
            "smtp": {
                "host": "localhost",
                "port": 25,
                "email_env": "SENDER_EMAIL",
                "password_env": "SENDER_PASSWORD",
            },
            "telegram": {
                "enabled": True,
                "bot_token_env": "TELEGRAM_BOT_TOKEN",
                "chat_id_env": "TELEGRAM_CHAT_ID",
            }
        }
        with patch.dict(os.environ, {"SENDER_EMAIL": "a@b.com", "SENDER_PASSWORD": "pass", "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "cid"}):
            agent_both_off = build_notification_agent(cfg, integrations={"gmail": False, "telegram": False})
            self.assertIsNone(agent_both_off._email)
            self.assertIsNone(agent_both_off._telegram)

            agent_email_only = build_notification_agent(cfg, integrations={"gmail": True, "telegram": False})
            self.assertIsNotNone(agent_email_only._email)
            self.assertIsNone(agent_email_only._telegram)

            agent_tg_only = build_notification_agent(cfg, integrations={"gmail": False, "telegram": True})
            self.assertIsNone(agent_tg_only._email)
            self.assertIsNotNone(agent_tg_only._telegram)

    def test_schema_rejects_non_boolean_integration_values(self):
        """Integration values must be actual booleans."""
        for invalid_value in ("false", 1):
            with self.subTest(value=invalid_value):
                with self.assertRaises(ValidationError):
                    StartProjectRequest(
                        project_name="TestProj",
                        integrations={"gmail": invalid_value},
                    )

    def test_notification_agent_task_assigned_all_four_combinations(self):
        """notify_task_assigned handles all 4 combinations of Gmail and Telegram independently."""
        # 1. Gmail ON, Telegram ON
        mock_email = MagicMock()
        mock_email.send.return_value = True
        mock_tg = MagicMock()
        agent = NotificationAgent(email_notifier=mock_email, telegram_notifier=mock_tg)
        sent = agent.notify_task_assigned(
            to_email="alice@example.com",
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            priority="High",
            telegram_chat_id="12345",
        )
        self.assertTrue(sent)
        mock_email.send.assert_called_once()
        mock_tg.send_task_assignment.assert_called_once_with(
            chat_id="12345",
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            priority="High",
        )
        self.assertEqual(len(agent.notification_issues), 0)

        # 2. Gmail ON, Telegram OFF
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_tg_off = NotificationAgent(email_notifier=mock_email, telegram_notifier=None)
        sent = agent_tg_off.notify_task_assigned(
            to_email="alice@example.com",
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            telegram_chat_id="12345",
        )
        self.assertTrue(sent)
        mock_email.send.assert_called_once()
        mock_tg.send_task_assignment.assert_not_called()
        self.assertEqual(len(agent_tg_off.notification_issues), 0)

        # 3. Gmail OFF, Telegram ON (missing email does NOT record issue when Gmail is OFF)
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_email_off = NotificationAgent(email_notifier=None, telegram_notifier=mock_tg)
        sent = agent_email_off.notify_task_assigned(
            to_email=None,
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            telegram_chat_id="12345",
        )
        self.assertFalse(sent)
        mock_email.send.assert_not_called()
        mock_tg.send_task_assignment.assert_called_once_with(
            chat_id="12345",
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            priority="",
        )
        self.assertEqual(len(agent_email_off.notification_issues), 0)

        # 4. Gmail OFF, Telegram OFF
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_both_off = NotificationAgent(email_notifier=None, telegram_notifier=None)
        sent = agent_both_off.notify_task_assigned(
            to_email=None,
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            telegram_chat_id="12345",
        )
        self.assertFalse(sent)
        mock_email.send.assert_not_called()
        mock_tg.send_task_assignment.assert_not_called()
        self.assertEqual(len(agent_both_off.notification_issues), 0)

    @patch("agents.communication.notification_agent.record_notification_issues")
    def test_notification_agent_task_assigned_invalid_email_when_gmail_enabled(self, mock_record):
        """When Gmail is enabled and email is invalid/missing, issue is recorded and email is skipped."""
        mock_email = MagicMock()
        mock_tg = MagicMock()
        agent = NotificationAgent(email_notifier=mock_email, telegram_notifier=mock_tg)
        sent = agent.notify_task_assigned(
            to_email="",
            employee_name="Alice",
            project_name="ProjA",
            task_name="Task1",
            deadline="2026-12-01",
            telegram_chat_id="12345",
        )
        self.assertFalse(sent)
        mock_email.send.assert_not_called()
        mock_tg.send_task_assignment.assert_called_once()
        self.assertEqual(len(agent.notification_issues), 1)
        self.assertEqual(agent.notification_issues[0]["recipient"], "Alice")
        mock_record.assert_called_once()

    def test_notification_agent_project_assignment_all_four_combinations(self):
        """notify_project_assignment handles all 4 combinations and PM-fallback correctly."""
        tasks = [{"task_name": "Task 1", "time_days": 3}]

        # 1. Gmail ON, Telegram ON (with chat_id -> DM)
        mock_email = MagicMock()
        mock_email.send.return_value = True
        mock_tg = MagicMock()
        agent = NotificationAgent(email_notifier=mock_email, telegram_notifier=mock_tg)
        sent = agent.notify_project_assignment(
            to_email="alice@example.com",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id="12345",
        )
        self.assertTrue(sent)
        mock_email.send.assert_called_once()
        mock_tg.send_to_chat.assert_called_once()
        mock_tg.send.assert_not_called()
        self.assertEqual(len(agent.notification_issues), 0)

        # 1b. Gmail ON, Telegram ON (without chat_id -> PM alert fallback)
        mock_email.reset_mock()
        mock_tg.reset_mock()
        sent = agent.notify_project_assignment(
            to_email="alice@example.com",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id=None,
        )
        self.assertTrue(sent)
        mock_email.send.assert_called_once()
        mock_tg.send_to_chat.assert_not_called()
        mock_tg.send.assert_called_once()

        # 2. Gmail ON, Telegram OFF
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_tg_off = NotificationAgent(email_notifier=mock_email, telegram_notifier=None)
        sent = agent_tg_off.notify_project_assignment(
            to_email="alice@example.com",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id="12345",
        )
        self.assertTrue(sent)
        mock_email.send.assert_called_once()
        mock_tg.send_to_chat.assert_not_called()
        mock_tg.send.assert_not_called()

        # 3. Gmail OFF, Telegram ON (missing email does NOT record issue when Gmail is OFF)
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_email_off = NotificationAgent(email_notifier=None, telegram_notifier=mock_tg)
        sent = agent_email_off.notify_project_assignment(
            to_email="",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id="12345",
        )
        self.assertFalse(sent)
        mock_email.send.assert_not_called()
        mock_tg.send_to_chat.assert_called_once()
        mock_tg.send.assert_not_called()
        self.assertEqual(len(agent_email_off.notification_issues), 0)

        # 3b. Gmail OFF, Telegram ON (no chat_id -> PM fallback)
        mock_tg.reset_mock()
        sent = agent_email_off.notify_project_assignment(
            to_email="",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id=None,
        )

        self.assertFalse(sent)
        mock_tg.send.assert_called_once()
        mock_tg.send_to_chat.assert_not_called()

        fallback_message = mock_tg.send.call_args[0][0]
        self.assertIn("Assignment details are available in PMassistant.", fallback_message)
        self.assertNotIn("Check email", fallback_message)

        # 4. Gmail OFF, Telegram OFF
        mock_email.reset_mock()
        mock_tg.reset_mock()
        agent_both_off = NotificationAgent(email_notifier=None, telegram_notifier=None)
        sent = agent_both_off.notify_project_assignment(
            to_email="",
            employee_name="Alice",
            project_name="ProjA",
            tasks=tasks,
            telegram_chat_id=None,
        )
        self.assertFalse(sent)
        mock_email.send.assert_not_called()
        mock_tg.send_to_chat.assert_not_called()
        mock_tg.send.assert_not_called()
        self.assertEqual(len(agent_both_off.notification_issues), 0)

    def test_notification_agent_notify_meeting_created(self):
        """notify_meeting_created skips cleanly when Telegram is disabled and dispatches when enabled."""
        # Telegram OFF
        agent_off = NotificationAgent(email_notifier=None, telegram_notifier=None)
        agent_off.notify_meeting_created(
            project_name="ProjA",
            meeting_type="Kickoff",
            meeting_time="2026-12-01 10:00",
            attendees=["Alice"],
            attendee_telegram_map={"Alice": "12345"},
        )

        # Telegram ON
        mock_tg = MagicMock()
        agent_on = NotificationAgent(email_notifier=None, telegram_notifier=mock_tg)
        agent_on.notify_meeting_created(
            project_name="ProjA",
            meeting_type="Kickoff",
            meeting_time="2026-12-01 10:00",
            attendees=["Alice"],
            attendee_telegram_map={"Alice": "12345"},
        )
        mock_tg.send.assert_called_once()
        mock_tg.send_meeting_alert.assert_called_once_with(
            chat_id="12345",
            employee_name="Alice",
            project_name="ProjA",
            meeting_type="Kickoff",
            meeting_time="2026-12-01 10:00",
        )

    def test_notification_agent_broadcast_telegram(self):
        """broadcast_telegram handles disabled Telegram safely and delegates when enabled."""
        employees = [{"Employee_Name": "Alice", "telegram_chat_id": "12345"}]

        # Telegram OFF
        agent_off = NotificationAgent(email_notifier=None, telegram_notifier=None)
        res = agent_off.broadcast_telegram("Hello team", employees)
        self.assertEqual(res, {"sent": [], "failed": [], "skipped": True})

        # Telegram ON
        mock_tg = MagicMock()
        mock_tg.broadcast.return_value = {"sent": ["12345"], "failed": []}
        agent_on = NotificationAgent(email_notifier=None, telegram_notifier=mock_tg)
        res = agent_on.broadcast_telegram("Hello team", employees)
        mock_tg.broadcast.assert_called_once_with(employees, "Hello team")
        self.assertEqual(res, {"sent": ["12345"], "failed": []})

    @patch("agents.communication.notification_agent.record_notification_issues")
    def test_notification_agent_task_assignments_batch_contract(self, mock_record):
        """notify_task_assignments_batch return contract counts email delivery only."""
        assignments = [
            {
                "to_email": "alice@example.com",
                "employee_name": "Alice",
                "task_name": "Task 1",
                "deadline": "2026-12-01",
                "telegram_chat_id": "111",
            },
            {
                "to_email": "",
                "employee_name": "Bob",
                "task_name": "Task 2",
                "deadline": "2026-12-02",
                "telegram_chat_id": "222",
            },
        ]

        # Case 1: Gmail ON, Telegram ON
        mock_email = MagicMock()
        mock_email.send.return_value = True
        mock_tg = MagicMock()
        agent = NotificationAgent(email_notifier=mock_email, telegram_notifier=mock_tg)
        res = agent.notify_task_assignments_batch("ProjA", assignments)

        self.assertEqual(res["total"], 2)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(len(res["issues"]), 1)
        self.assertEqual(res["issues"][0]["recipient"], "Bob")
        self.assertEqual(mock_tg.send_task_assignment.call_count, 2)

        # Case 2: Gmail OFF, Telegram ON
        mock_tg.reset_mock()
        agent_no_gmail = NotificationAgent(email_notifier=None, telegram_notifier=mock_tg)
        res_no_gmail = agent_no_gmail.notify_task_assignments_batch("ProjA", assignments)

        self.assertEqual(res_no_gmail["total"], 2)
        self.assertEqual(res_no_gmail["sent"], 0)
        self.assertEqual(res_no_gmail["skipped"], 2)
        self.assertEqual(len(res_no_gmail["issues"]), 0)
        self.assertEqual(mock_tg.send_task_assignment.call_count, 2)


if __name__ == "__main__":
    unittest.main()
