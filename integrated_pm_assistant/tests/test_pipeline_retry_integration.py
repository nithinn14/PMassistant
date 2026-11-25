
import os
import sys
import types
import unittest
from unittest.mock import patch

from api.services import pipeline


class TestPipelineRetryIntegration(unittest.TestCase):

    def _run_pipeline_with_mocks(
        self,
        runner_behaviors,
        verification_status="completed",
        rollback_status="rolled_back",
    ):
        job_id = "test_batch_c_job"
        project_name = "Test Project"

        job = {
            "status": "queued",
            "current_step": "",
            "completed_steps": [],
            "error": None,
            "logs": [],
        }

        pipeline.jobs[job_id] = job
        runner_state = {"count": 0}

        class FakeClient:
            def __init__(self, api_key):
                self.models = types.SimpleNamespace(
                    generate_content=lambda **kwargs: None
                )

        class FakeSessionService:
            def create_session_sync(self, **kwargs):
                pass

        class FakeRunner:
            def __init__(self, **kwargs):
                self.index = runner_state["count"]
                runner_state["count"] += 1

            async def run_async(self, **kwargs):
                attempt_id = pipeline._current_attempt_id_var.get()
                tracker = pipeline._allocation_tracker_var.get()

                tracker["seq"] += 1
                tx_id = f"{attempt_id}_alloc{tracker['seq']}"

                tracker["transactions"].append({
                    "project_name": project_name,
                    "attempt_id": tx_id,
                    "finished": True,
                })

                behavior = runner_behaviors[self.index]

                if behavior == "rate_limit":
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")

                if False:
                    yield None

        genai_module = types.ModuleType("google.genai")
        genai_module.Client = FakeClient

        types_module = types.ModuleType("google.genai.types")

        class FakeContent:
            def __init__(self, **kwargs):
                pass

        class FakePart:
            def __init__(self, **kwargs):
                pass

        types_module.Content = FakeContent
        types_module.Part = FakePart

        adk_module = types.ModuleType("google.adk")
        adk_module.Runner = FakeRunner

        sessions_module = types.ModuleType("google.adk.sessions")
        sessions_module.InMemorySessionService = FakeSessionService

        fake_modules = {
            "google.genai": genai_module,
            "google.genai.types": types_module,
            "google.adk": adk_module,
            "google.adk.sessions": sessions_module,
        }

        verification = {
            "status": verification_status,
            "current_step": "Resource validation",
            "error": "Test verification failure",
        }

        with (
            patch.dict(sys.modules, fake_modules),
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}),
            patch("config_loader.load_config", return_value={
                "models": {"default": "test-model"}
            }),
            patch(
                "agents.orchestrator_agent.build_orchestrator",
                return_value=object(),
            ),
            patch.object(
                pipeline,
                "find_actual_project_name",
                return_value=project_name,
            ),
            patch.object(
                pipeline,
                "verify_pipeline_output",
                return_value=verification,
            ) as mock_verify,
            patch.object(pipeline, "_save_project_integrations"),
            patch.object(
                pipeline,
                "rollback_allocations",
                return_value={
                    "status": rollback_status,
                    "message": "Simulated rollback result",
                },
            ) as mock_rollback,
            patch.object(pipeline.time, "sleep"),
            patch.object(pipeline, "MAX_RETRIES", 2),
        ):
            try:
                pipeline._run_pipeline(
                    job_id=job_id,
                    project_name=project_name,
                    project_description="Test description",
                )
            finally:
                pipeline.jobs.pop(job_id, None)

        return job, runner_state, mock_rollback, mock_verify

    def test_rate_limit_rolls_back_before_retry(self):
        job, runner, rollback, verify = self._run_pipeline_with_mocks(
            ["rate_limit", "success"]
        )

        self.assertEqual(runner["count"], 2)
        self.assertEqual(rollback.call_count, 1)

        self.assertEqual(
            rollback.call_args.kwargs["attempt_id"],
            "test_batch_c_job_attempt1_alloc1",
        )

        self.assertEqual(job["status"], "completed")

    def test_successful_attempt_preserves_allocations(self):
        job, runner, rollback, verify = self._run_pipeline_with_mocks(
            ["success"]
        )

        self.assertEqual(runner["count"], 1)
        rollback.assert_not_called()
        self.assertEqual(job["status"], "completed")

    def test_resource_blocked_attempt_preserves_allocations(self):
        job, runner, rollback, verify = self._run_pipeline_with_mocks(
            ["success"],
            verification_status="resource_blocked",
        )

        self.assertEqual(runner["count"], 1)
        rollback.assert_not_called()
        self.assertEqual(job["status"], "resource_blocked")

    def test_rollback_failure_stops_retry(self):
        job, runner, rollback, verify = self._run_pipeline_with_mocks(
            ["rate_limit", "success"],
            rollback_status="failed",
        )

        self.assertEqual(runner["count"], 1)
        self.assertEqual(rollback.call_count, 1)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(
            job["current_step"],
            "Error: Allocation Rollback Failed",
        )
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
