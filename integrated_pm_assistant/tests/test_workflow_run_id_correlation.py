import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

import communication.telegram.telegram_bot as tg_bot
import tools.resources.resource_validator as rv


class TestWorkflowRunIdCorrelation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp_dir.name)
        self.rv_patch = patch.object(rv, "DEFAULT_OUTPUT_DIR", self.out_dir)
        self.tg_patch = patch.object(tg_bot, "_output_dir", return_value=self.out_dir)
        self.rv_patch.start()
        self.tg_patch.start()

    def tearDown(self):
        self.tg_patch.stop()
        self.rv_patch.stop()
        self.tmp_dir.cleanup()

    def _make_bot(self):
        bot = MagicMock(spec=tg_bot.TelegramBot)
        bot.send = MagicMock(return_value=True)
        return bot

    def test_update_workflow_state_persists_preserves_and_replaces_run_id(self):
        path = Path(
            rv.update_workflow_state(
                project_name="ProjA",
                status="RESOURCE_BLOCKED",
                missing_roles={"Backend Developer": 1},
                resolution="PENDING",
                run_id="run-111",
            )
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "run-111")

        # Subsequent update omitting run_id preserves existing run_id
        rv.update_workflow_state(
            project_name="ProjA",
            status="RETRYING",
            missing_roles={"Backend Developer": 1},
            resolution="PENDING",
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "run-111")

        # Explicit new run_id replaces old run_id
        rv.update_workflow_state(
            project_name="ProjA",
            status="RESOURCE_BLOCKED",
            missing_roles={"Backend Developer": 1},
            resolution="PENDING",
            run_id="run-222",
        )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "run-222")

    def test_telegram_rejects_missing_mismatched_and_non_pending_approvals(self):
        rv.update_workflow_state(
            project_name="ProjB",
            status="RESOURCE_BLOCKED",
            missing_roles={"QA Engineer": 1},
            resolution="PENDING",
            run_id="run-abc",
        )
        bot = self._make_bot()
        log_path = self.out_dir / "decision_log.json"

        # 1. Missing run_id
        tg_bot._dispatch(bot, "123", "/approve_external_hiring", project_name="ProjB", run_id="run-abc")
        self.assertIn("Invalid command format", bot.send.call_args[0][1])
        self.assertFalse(log_path.exists())

        # 2. Stale / mismatched run_id
        tg_bot._dispatch(bot, "123", "/approve_external_hiring run-wrong", project_name="ProjB", run_id="run-abc")
        self.assertIn("stale or mismatched", bot.send.call_args[0][1])
        self.assertFalse(log_path.exists())

        # 3. Non-RESOURCE_BLOCKED status
        rv.update_workflow_state(
            project_name="ProjB",
            status="RETRYING",
            missing_roles={"QA Engineer": 1},
            resolution="PENDING",
        )
        tg_bot._dispatch(bot, "123", "/approve_external_hiring run-abc", project_name="ProjB", run_id="run-abc")
        self.assertIn("not RESOURCE_BLOCKED", bot.send.call_args[0][1])
        self.assertFalse(log_path.exists())

        # 4. Non-PENDING resolution
        rv.update_workflow_state(
            project_name="ProjB",
            status="RESOURCE_BLOCKED",
            missing_roles={"QA Engineer": 1},
            resolution="REBALANCE",
        )
        tg_bot._dispatch(bot, "123", "/approve_external_hiring run-abc", project_name="ProjB", run_id="run-abc")
        self.assertIn("not PENDING", bot.send.call_args[0][1])
        self.assertFalse(log_path.exists())

    def test_telegram_accepts_valid_approval_and_ignores_detect_active_project(self):
        rv.update_workflow_state(
            project_name="TargetProj",
            status="RESOURCE_BLOCKED",
            missing_roles={"DevOps Engineer": 1},
            resolution="PENDING",
            run_id="run-target",
        )
        # Create a newer RESOURCE_BLOCKED project to ensure embedded mode never picks it
        rv.update_workflow_state(
            project_name="OtherProj",
            status="RESOURCE_BLOCKED",
            missing_roles={"DevOps Engineer": 1},
            resolution="PENDING",
            run_id="run-other",
        )

        bot = self._make_bot()
        with patch.object(tg_bot, "_detect_active_project") as mock_detect:
            tg_bot._dispatch(
                bot,
                "123",
                "/approve_external_hiring run-target",
                project_name="TargetProj",
                run_id="run-target",
            )
            mock_detect.assert_not_called()

        self.assertIn("External Hiring Approved", bot.send.call_args[0][1])
        log = json.loads((self.out_dir / "decision_log.json").read_text(encoding="utf-8"))
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["project"], "TargetProj")
        self.assertEqual(log[0]["run_id"], "run-target")
        self.assertEqual(log[0]["decision"], "APPROVE_EXTERNAL_HIRING")

        target_state = rv.check_workflow_resolution("TargetProj")
        self.assertEqual(target_state["resolution"], "APPROVE_EXTERNAL_HIRING")
        self.assertEqual(target_state["run_id"], "run-target")

        other_state = rv.check_workflow_resolution("OtherProj")
        self.assertEqual(other_state["resolution"], "PENDING")

    def test_update_workflow_state_preserves_custom_fields(self):
        rv.update_workflow_state(
            project_name="CustomProj",
            status="RESOURCE_BLOCKED",
            missing_roles={"Developer": 1},
            run_id="run-custom",
        )

        path = self.out_dir / "CustomProj_workflow_state.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["custom_field"] = {"source": "test", "value": 42}
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

        rv.update_workflow_state(
            project_name="CustomProj",
            status="RETRYING",
            missing_roles={"Developer": 1},
        )

        updated = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(
            updated["custom_field"],
            {"source": "test", "value": 42},
        )
        self.assertEqual(updated["run_id"], "run-custom")

    def test_conditional_update_preserves_custom_fields(self):
        rv.update_workflow_state(
            project_name="ConditionalProj",
            status="RESOURCE_BLOCKED",
            missing_roles={"QA": 1},
            run_id="run-conditional",
        )

        path = self.out_dir / "ConditionalProj_workflow_state.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["custom_field"] = "preserve-me"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

        result = rv.conditional_update_workflow_state(
            project_name="ConditionalProj",
            expected_run_id="run-conditional",
            status="RETRYING",
            missing_roles={"QA": 1},
            expected_status="RESOURCE_BLOCKED",
            expected_resolution="PENDING",
        )

        self.assertTrue(result)
        updated = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(updated["custom_field"], "preserve-me")

    def test_update_workflow_state_does_not_overwrite_malformed_yaml(self):
        path = self.out_dir / "MalformedProj_workflow_state.yaml"
        original = "status: [invalid yaml"
        path.write_text(original, encoding="utf-8")

        with self.assertRaises(yaml.YAMLError):
            rv.update_workflow_state(
                project_name="MalformedProj",
                status="RETRYING",
                missing_roles={},
            )

        self.assertEqual(path.read_text(encoding="utf-8"), original)

    
    def test_telegram_rejects_non_mapping_workflow_state(self):
        path = self.out_dir / "NonMappingProj_workflow_state.yaml"
        original = yaml.safe_dump(["RESOURCE_BLOCKED", "run-123"])
        path.write_text(original, encoding="utf-8")

        accepted, error = tg_bot._conditional_update_approval_resolution(
            outdir=self.out_dir,
            project_name="NonMappingProj",
            supplied_run_id="run-123",
        )

        self.assertFalse(accepted)
        self.assertIn("expected a YAML mapping", error)
        self.assertEqual(path.read_text(encoding="utf-8"), original)


    def test_update_workflow_state_does_not_overwrite_unreadable_file(self):
        rv.update_workflow_state(
            project_name="UnreadableProj",
            status="RESOURCE_BLOCKED",
            missing_roles={},
            run_id="run-safe",
        )

        path = self.out_dir / "UnreadableProj_workflow_state.yaml"
        original = path.read_text(encoding="utf-8")

        real_open = open

        def failing_open(file, *args, **kwargs):
            if Path(file) == path:
                raise PermissionError("Simulated read failure")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=failing_open):
            with self.assertRaises(PermissionError):
                rv.update_workflow_state(
                    project_name="UnreadableProj",
                    status="RETRYING",
                    missing_roles={},
                )

        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_conditional_update_rejects_mismatched_run_id(self):
        rv.update_workflow_state(
            project_name="MismatchProj",
            status="RESOURCE_BLOCKED",
            missing_roles={},
            run_id="current-run",
        )

        result = rv.conditional_update_workflow_state(
            project_name="MismatchProj",
            expected_run_id="stale-run",
            status="RETRYING",
            missing_roles={},
        )

        self.assertFalse(result)
        state = rv.check_workflow_resolution("MismatchProj")
        self.assertEqual(state["run_id"], "current-run")
        self.assertEqual(state["status"], "RESOURCE_BLOCKED")

    def test_conditional_update_rejects_mismatched_status(self):
        rv.update_workflow_state(
            project_name="StatusProj",
            status="RETRYING",
            missing_roles={},
            run_id="status-run",
        )

        result = rv.conditional_update_workflow_state(
            project_name="StatusProj",
            expected_run_id="status-run",
            status="COMPLETED",
            missing_roles={},
            expected_status="RESOURCE_BLOCKED",
        )

        self.assertFalse(result)
        state = rv.check_workflow_resolution("StatusProj")
        self.assertEqual(state["status"], "RETRYING")

    def test_conditional_update_rejects_mismatched_resolution(self):
        rv.update_workflow_state(
            project_name="ResolutionProj",
            status="RESOURCE_BLOCKED",
            missing_roles={},
            resolution="APPROVE_EXTERNAL_HIRING",
            run_id="resolution-run",
        )

        result = rv.conditional_update_workflow_state(
            project_name="ResolutionProj",
            expected_run_id="resolution-run",
            status="RETRYING",
            missing_roles={},
            expected_status="RESOURCE_BLOCKED",
            expected_resolution="PENDING",
        )

        self.assertFalse(result)
        state = rv.check_workflow_resolution("ResolutionProj")
        self.assertEqual(state["resolution"], "APPROVE_EXTERNAL_HIRING")

    def test_agent_update_cannot_overwrite_telegram_approval(self):
        rv.update_workflow_state(
            project_name="RaceProj",
            status="RESOURCE_BLOCKED",
            missing_roles={"Developer": 1},
            resolution="PENDING",
            run_id="race-run",
        )

        # Simulate Telegram accepting the PM approval first.
        approval_updated = rv.conditional_update_workflow_state(
            project_name="RaceProj",
            expected_run_id="race-run",
            status="RESOURCE_BLOCKED",
            missing_roles={"Developer": 1},
            resolution="APPROVE_EXTERNAL_HIRING",
            expected_status="RESOURCE_BLOCKED",
            expected_resolution="PENDING",
        )
        self.assertTrue(approval_updated)

        # Simulate the old agent's employee-file update arriving afterwards.
        agent_updated = rv.conditional_update_workflow_state(
            project_name="RaceProj",
            expected_run_id="race-run",
            status="RETRYING",
            missing_roles={"Developer": 1},
            resolution="PENDING",
            expected_status="RESOURCE_BLOCKED",
            expected_resolution="PENDING",
        )

        self.assertFalse(agent_updated)

        state = rv.check_workflow_resolution("RaceProj")
        self.assertEqual(state["resolution"], "APPROVE_EXTERNAL_HIRING")
        self.assertEqual(state["status"], "RESOURCE_BLOCKED")
        self.assertEqual(state["run_id"], "race-run")


if __name__ == "__main__":
    unittest.main()
