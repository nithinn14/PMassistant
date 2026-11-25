
import unittest
from unittest.mock import patch

from api.services import pipeline


class TestPipelineAttemptRollback(unittest.TestCase):

    def setUp(self):
        self.job = {
            "status": "running",
            "current_step": "Processing",
            "error": None,
        }

    @patch("api.services.pipeline.rollback_allocations")
    def test_rollbacks_happen_in_reverse_order(self, mock_rollback):
        mock_rollback.return_value = {"status": "rolled_back"}

        tracker = {
            "transactions": [
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc1",
                    "finished": True,
                },
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc2",
                    "finished": True,
                },
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc3",
                    "finished": True,
                },
            ]
        }

        result = pipeline._rollback_attempt_transactions(
            tracker, self.job, "test"
        )

        self.assertTrue(result)
        self.assertEqual(
            [call.kwargs["attempt_id"] for call in mock_rollback.call_args_list],
            [
                "attempt1_alloc3",
                "attempt1_alloc2",
                "attempt1_alloc1",
            ],
        )

    @patch("api.services.pipeline.rollback_allocations")
    def test_already_rolled_back_is_accepted(self, mock_rollback):
        mock_rollback.return_value = {"status": "already_rolled_back"}

        tracker = {
            "transactions": [
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc1",
                    "finished": True,
                }
            ]
        }

        result = pipeline._rollback_attempt_transactions(
            tracker, self.job, "test"
        )

        self.assertTrue(result)
        self.assertEqual(self.job["status"], "running")

    @patch("api.services.pipeline.rollback_allocations")
    def test_not_found_is_accepted_for_unfinished_allocation(
        self, mock_rollback
    ):
        mock_rollback.return_value = {"status": "not_found"}

        tracker = {
            "transactions": [
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc1",
                    "finished": False,
                }
            ]
        }

        result = pipeline._rollback_attempt_transactions(
            tracker, self.job, "test"
        )

        self.assertTrue(result)

    @patch("api.services.pipeline.rollback_allocations")
    def test_rollback_failure_stops_processing(self, mock_rollback):
        mock_rollback.side_effect = [
            {"status": "rolled_back"},
            {"status": "failed", "message": "Could not restore employee hours"},
        ]

        tracker = {
            "transactions": [
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc1",
                    "finished": True,
                },
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc2",
                    "finished": True,
                },
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc3",
                    "finished": True,
                },
            ]
        }

        result = pipeline._rollback_attempt_transactions(
            tracker, self.job, "test"
        )

        self.assertFalse(result)
        self.assertEqual(mock_rollback.call_count, 2)
        self.assertEqual(self.job["status"], "failed")
        self.assertEqual(
            self.job["current_step"],
            "Error: Allocation Rollback Failed",
        )

    @patch("api.services.pipeline.rollback_allocations")
    def test_rollback_exception_stops_processing(self, mock_rollback):
        mock_rollback.side_effect = RuntimeError("Journal unavailable")

        tracker = {
            "transactions": [
                {
                    "project_name": "Project A",
                    "attempt_id": "attempt1_alloc1",
                    "finished": True,
                }
            ]
        }

        result = pipeline._rollback_attempt_transactions(
            tracker, self.job, "test"
        )

        self.assertFalse(result)
        self.assertEqual(self.job["status"], "failed")
        self.assertIn("Journal unavailable", self.job["error"])

    def test_empty_tracker_requires_no_rollback(self):
        result = pipeline._rollback_attempt_transactions(
            {"transactions": []}, self.job, "test"
        )

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
