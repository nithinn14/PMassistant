import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

import agents.resource_validation_agent as rva_mod
import api.services.pipeline as pipeline_mod
import communication.telegram.telegram_bot as tg_bot
import tools.resources.resource_validator as rv_mod
from agents.communication.notification_agent import NotificationAgent


class _DummyCtx:
    def __init__(self, payload: dict):
        self.invocation_id = "inv-1"
        self.branch = None
        self._payload = payload

    def _get_events(self, current_invocation=True):
        part = SimpleNamespace(text=json.dumps(self._payload))
        return [SimpleNamespace(content=SimpleNamespace(parts=[part]))]


class TestPMResponseTimeout(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp_dir.name)
        self.employees_path = self.out_dir / "employees.xlsx"
        self.employees_path.write_text("dummy", encoding="utf-8")

        self.rv_patch = patch.object(rv_mod, "DEFAULT_OUTPUT_DIR", self.out_dir)
        self.tg_patch = patch.object(tg_bot, "_output_dir", return_value=self.out_dir)
        self.emp_patch = patch.object(
            rva_mod, "_get_employees_path", return_value=self.employees_path
        )
        self.pm_email_patch = patch.object(
            rva_mod, "_get_pm_email", return_value="pm@example.com"
        )
        self.diag_patch = patch.object(
            rva_mod,
            "save_diagnostic_report",
            return_value=str(self.out_dir / "diag.json"),
        )
        self.rv_patch.start()
        self.tg_patch.start()
        self.emp_patch.start()
        self.pm_email_patch.start()
        self.diag_patch.start()

    def tearDown(self):
        self.diag_patch.stop()
        self.pm_email_patch.stop()
        self.emp_patch.stop()
        self.tg_patch.stop()
        self.rv_patch.stop()
        self.tmp_dir.cleanup()

    def _shortage_payload(self, project="TimeoutProj"):
        return {
            "project_name": project,
            "resource_blocked": True,
            "tasks": [
                {
                    "task_id": "T1",
                    "task_name": "Backend API",
                    "assigned_role": "Backend Developer",
                    "Assigned_Employee": "No Resource Available",
                    "task_status": "waiting_resource",
                }
            ],
        }

    def _make_config(
        self,
        poll=0.01,
        initial_wait=0.02,
        reminder_wait=0.02,
        max_attempts=3,
        retry_delay=0.0,
    ):
        return {
            "workflow": {
                "poll_interval_seconds": poll,
                "pm_initial_wait_seconds": initial_wait,
                "pm_reminder_wait_seconds": reminder_wait,
                "reminder_max_attempts": max_attempts,
                "reminder_retry_delay_seconds": retry_delay,
            }
        }

    def _run_agent(self, agent, payload):
        async def _collect():
            events = []
            async for ev in agent._run_async_impl(_DummyCtx(payload)):
                events.append(ev)
            return events

        return asyncio.run(_collect())

    def test_01_02_05_initial_timeout_sends_single_reminder_and_second_timeout_records_status(self):
        email_mock = MagicMock()
        email_mock.send.return_value = True
        notifier = NotificationAgent(email_notifier=email_mock, telegram_notifier=None)
        notifier.notify_resource_shortage = MagicMock()

        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={"gmail": True, "telegram": False, "google_calendar": False, "gantt": False},
        )
        cfg = self._make_config(poll=0.01, initial_wait=0.02, reminder_wait=0.02)

        with patch.object(rva_mod, "load_config", return_value=cfg), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ):
            with self.assertRaises(rv_mod.PMResponseTimeoutError) as ctx:
                self._run_agent(agent, self._shortage_payload("ProjTimeout"))

        self.assertEqual(ctx.exception.project_name, "ProjTimeout")
        # Reminder sent exactly once
        self.assertEqual(email_mock.send.call_count, 1)
        self.assertIn("Reminder:", email_mock.send.call_args.kwargs["subject"])

        state = rv_mod.check_workflow_resolution("ProjTimeout")
        self.assertEqual(state["status"], "RESOURCE_BLOCKED")
        self.assertEqual(state["resolution"], "PM_RESPONSE_TIMEOUT")
        self.assertEqual(state["run_id"], ctx.exception.run_id)

    def test_03_04_reminder_retries_up_to_3_and_still_starts_second_wait(self):
        email_mock = MagicMock()
        email_mock.send.return_value = False
        notifier = NotificationAgent(email_notifier=email_mock, telegram_notifier=None)
        notifier.notify_resource_shortage = MagicMock()

        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={"gmail": True, "telegram": False, "google_calendar": False, "gantt": False},
        )
        cfg = self._make_config(
            poll=0.01, initial_wait=0.01, reminder_wait=0.02, max_attempts=3, retry_delay=0.0
        )

        with patch.object(rva_mod, "load_config", return_value=cfg), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ):
            with self.assertRaises(rv_mod.PMResponseTimeoutError):
                self._run_agent(agent, self._shortage_payload("ProjRetryRem"))

        # 3 total attempts (1 initial + 2 retries), then second wait expires
        self.assertEqual(email_mock.send.call_count, 3)
        state = rv_mod.check_workflow_resolution("ProjRetryRem")
        self.assertEqual(state["resolution"], "PM_RESPONSE_TIMEOUT")

    def test_06_valid_pm_decision_during_wait_takes_precedence_over_timeout(self):
        notifier = MagicMock()
        notifier.notify_resource_shortage_reminder.return_value = True

        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={"gmail": True, "telegram": False, "google_calendar": False, "gantt": False},
        )
        cfg = self._make_config(poll=0.01, initial_wait=0.05, reminder_wait=0.05)

        orig_check = rv_mod.check_workflow_resolution

        def _approve_on_first_poll(proj):
            state = orig_check(proj)
            if state.get("run_id") and state.get("resolution") == "PENDING":
                bot = MagicMock(spec=tg_bot.TelegramBot)
                tg_bot._dispatch(
                    bot,
                    "123",
                    f"/approve_external_hiring {state['run_id']}",
                    project_name=proj,
                    run_id=state["run_id"],
                )
                return orig_check(proj)
            return state

        with patch.object(rva_mod, "load_config", return_value=cfg), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ), patch.object(rva_mod, "check_workflow_resolution", side_effect=_approve_on_first_poll):
            events = self._run_agent(agent, self._shortage_payload("ProjApproved"))

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].actions.escalate)
        state = orig_check("ProjApproved")
        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["resolution"], "APPROVE_EXTERNAL_HIRING")

    def test_07_employee_file_modification_during_second_period_triggers_retry(self):
        notifier = MagicMock()
        notifier.notify_resource_shortage_reminder.return_value = True

        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={"gmail": True, "telegram": False, "google_calendar": False, "gantt": False},
        )
        cfg = self._make_config(poll=0.01, initial_wait=0.01, reminder_wait=0.2)

        def _touch_employees_after_reminder(**kwargs):
            # Bump mtime by +2 seconds when reminder is sent
            st = os.stat(self.employees_path)
            os.utime(self.employees_path, (st.st_atime, st.st_mtime + 2))
            return True

        notifier.notify_resource_shortage_reminder.side_effect = _touch_employees_after_reminder

        with patch.object(rva_mod, "load_config", return_value=cfg), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ):
            events = self._run_agent(agent, self._shortage_payload("ProjEmpChange"))

        self.assertEqual(len(events), 1)
        self.assertFalse(getattr(events[0].actions, "escalate", False))
        state = rv_mod.check_workflow_resolution("ProjEmpChange")
        self.assertEqual(state["status"], "RETRYING")
        self.assertEqual(state["resolution"], "PENDING")

    def test_08_stale_run_cannot_overwrite_current_run(self):
        notifier = MagicMock()
        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={"gmail": True, "telegram": False, "google_calendar": False, "gantt": False},
        )
        cfg = self._make_config(poll=0.01, initial_wait=0.05, reminder_wait=0.05)

        orig_check = rv_mod.check_workflow_resolution

        def _supersede_run(proj):
            rv_mod.update_workflow_state(
                project_name=proj,
                status="RESOURCE_BLOCKED",
                missing_roles={"Backend Developer": 1},
                resolution="PENDING",
                run_id="newer-run-id",
            )
            return orig_check(proj)

        with patch.object(rva_mod, "load_config", return_value=cfg), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ), patch.object(rva_mod, "check_workflow_resolution", side_effect=_supersede_run):
            with self.assertRaises(RuntimeError) as ctx:
                self._run_agent(agent, self._shortage_payload("ProjStale"))

        self.assertIn("superseded", str(ctx.exception))
        state = orig_check("ProjStale")
        self.assertEqual(state["run_id"], "newer-run-id")
        self.assertEqual(state["resolution"], "PENDING")

    def test_09_late_telegram_approval_rejected_after_timeout(self):
        rv_mod.update_workflow_state(
            project_name="ProjLate",
            status="RESOURCE_BLOCKED",
            missing_roles={"Backend Developer": 1},
            resolution="PM_RESPONSE_TIMEOUT",
            run_id="run-late-1",
        )
        bot = MagicMock(spec=tg_bot.TelegramBot)
        tg_bot._dispatch(
            bot,
            "123",
            "/approve_external_hiring run-late-1",
            project_name="ProjLate",
            run_id="run-late-1",
        )
        self.assertIn("PM_RESPONSE_TIMEOUT", bot.send.call_args[0][1])
        self.assertFalse((self.out_dir / "decision_log.json").exists())

    def test_10_pipeline_handles_timeout_as_resource_blocked_without_retry_or_rollback(self):
        job_id = "job-timeout-1"
        pipeline_mod.jobs[job_id] = {
            "status": "running",
            "current_step": "",
            "completed_steps": [],
            "logs": [],
            "error": None,
        }

        class _TimeoutRunner:
            def __init__(self, **kwargs):
                pass

            async def run_async(self, **kwargs):
                raise rv_mod.PMResponseTimeoutError("ProjPipe", "run-pipe-1")
                yield  # pragma: no cover

        class _DummySessionService:
            def create_session_sync(self, **kwargs):
                pass

        dummy_client = MagicMock()
        with patch.object(pipeline_mod, "DEFAULT_OUTPUT_DIR", self.out_dir), patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key"}
        ), patch("config_loader.load_config", return_value={"models": {"default": "gemini-test"}}), patch(
            "google.genai.Client", return_value=dummy_client
        ), patch("agents.orchestrator_agent.build_orchestrator", return_value=MagicMock()), patch(
            "google.adk.sessions.InMemorySessionService", _DummySessionService
        ), patch("google.adk.Runner", _TimeoutRunner), patch.object(
            pipeline_mod, "rollback_allocations"
        ) as mock_rb:
            pipeline_mod._run_pipeline(job_id, "ProjPipe", "desc")

        mock_rb.assert_not_called()
        job = pipeline_mod.jobs[job_id]
        self.assertEqual(job["status"], "resource_blocked")
        self.assertEqual(job["current_step"], "PM Response Timed Out")
        self.assertTrue((self.out_dir / "ProjPipe_integrations.json").exists())

    def test_11_valid_pm_decision_during_second_wait_accepted(self):
        notifier = MagicMock()
        reminder_completed = False
        approval_dispatched = False

        def _send_reminder(**kwargs):
            nonlocal reminder_completed
            reminder_completed = True
            return True

        notifier.notify_resource_shortage_reminder.side_effect = _send_reminder

        async def _approve_during_second_wait(delay):
            nonlocal approval_dispatched

            if reminder_completed and not approval_dispatched:
                approval_dispatched = True
                bot = MagicMock(spec=tg_bot.TelegramBot)
                run_id = rv_mod.check_workflow_resolution("ProjSecondWait")["run_id"]

                tg_bot._dispatch(
                    bot,
                    "123",
                    f"/approve_external_hiring {run_id}",
                    project_name="ProjSecondWait",
                    run_id=run_id,
                )

        agent = rva_mod.ResourceValidationAgent(
            name="resource_validation_agent",
            integrations={
                "gmail": True,
                "telegram": False,
                "google_calendar": False,
                "gantt": False,
            },
        )
        cfg = self._make_config(
            poll=0.01, initial_wait=0.0, reminder_wait=0.5
        )

        with patch.object(
            rva_mod, "load_config", return_value=cfg
        ), patch.object(
            rva_mod, "build_notification_agent", return_value=notifier
        ), patch.object(
            rva_mod.asyncio, "sleep", new=_approve_during_second_wait
        ):
            events = self._run_agent(
                agent, self._shortage_payload("ProjSecondWait")
            )

        state = rv_mod.check_workflow_resolution("ProjSecondWait")

        self.assertTrue(reminder_completed)
        self.assertTrue(approval_dispatched)
        self.assertEqual(notifier.notify_resource_shortage_reminder.call_count, 1)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].actions.escalate)
        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["resolution"], "APPROVE_EXTERNAL_HIRING")


if __name__ == "__main__":
    unittest.main()
