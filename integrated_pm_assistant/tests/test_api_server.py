"""
Regression tests for api_server.py:
1. Test A: POST /api/start-project must not mutate config.yaml on disk.
2. Test B: Concurrent job progress logging must remain isolated (via JobOutputRouter).
3. Test C: Concurrent backend context vars must be isolated (via data_backend.registry).
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import api_server


class TestApiServerRegressions(unittest.TestCase):
    def setUp(self):
        # Save initial jobs dict and stdout
        self._orig_jobs = dict(api_server.jobs)
        self._orig_stdout = sys.stdout
        api_server.jobs.clear()
        api_server.app.config["TESTING"] = True
        self.client = api_server.app.test_client()

    def tearDown(self):
        # Restore global state cleanly
        api_server.jobs.clear()
        api_server.jobs.update(self._orig_jobs)
        sys.stdout = self._orig_stdout

    def test_backend_selection_must_not_mutate_config(self):
        """
        Test A: Verify that POST /api/start-project does not mutate config.yaml on disk.
        Under the existing implementation, this test fails because api_server.py
        reads and rewrites config.yaml with regex replacement.
        """
        initial_yaml_content = (
            "data_backend:\n"
            '  type: "excel"\n'
            "  excel:\n"
            '    output_dir: "output"\n'
            '    root_dir: "."\n'
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            tmp_config = tmp_path / "config.yaml"
            tmp_config.write_text(initial_yaml_content, encoding="utf-8")

            # Patch BASE_DIR to redirect config_path to our temporary directory
            # and patch _run_pipeline to prevent real thread/pipeline execution
            with patch.object(api_server, "BASE_DIR", tmp_path), \
                 patch.object(api_server, "_run_pipeline") as mock_pipeline:

                payload = {
                    "project_name": "Test Non-Mutating Project",
                    "project_description": "Validate that starting a project does not rewrite config.yaml.",
                    "data_backend": "csv",
                }

                response = self.client.post("/api/start-project", json=payload)

                # Assert HTTP 200 and expected response structure
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertIn("job_id", data)
                self.assertEqual(data["status"], "running")

                # Verify _run_pipeline was targeted
                self.assertTrue(mock_pipeline.called)

                # Read config file after request
                resulting_content = tmp_config.read_text(encoding="utf-8")

                # The config file must remain byte-for-byte identical to its initial state.
                # Under current code, this assertion FAILS because type is rewritten to "csv".
                self.assertEqual(
                    resulting_content,
                    initial_yaml_content,
                    "Regression: POST /api/start-project mutated config.yaml on disk!"
                )

    def test_concurrent_job_logs_must_remain_isolated(self):
        """
        Test B: Verify that concurrent pipeline jobs do not cross-contaminate
        their logs.

        This test exercises the PRODUCTION routing mechanism:
        - Two job entries are placed in api_server.jobs, each with its own
          OutputCapture stored under jobs[job_id]["_capture"].
        - Each worker thread sets _current_job_id_var to its own job_id (exactly
          as _run_pipeline does), then prints its unique marker while the other
          job is also active.
        - After both threads finish, the test asserts that each job's capture
          contains only its own output.

        The test must pass now that JobOutputRouter is installed process-wide
        and routes by context variable rather than by sys.stdout replacement.
        """
        job1_id = "job-concurrent-1"
        job2_id = "job-concurrent-2"

        capture1 = api_server.OutputCapture(job1_id)
        capture2 = api_server.OutputCapture(job2_id)

        api_server.jobs[job1_id] = {
            "id": job1_id,
            "completed_steps": [],
            "current_step": "Initializing...",
            "logs": [],
            "_capture": capture1,
        }
        api_server.jobs[job2_id] = {
            "id": job2_id,
            "completed_steps": [],
            "current_step": "Initializing...",
            "logs": [],
            "_capture": capture2,
        }

        # Deterministic synchronization
        job1_ready   = threading.Event()  # Job 1 has set its context var
        job2_ready   = threading.Event()  # Job 2 has set its context var
        job1_printed = threading.Event()  # Job 1 has printed its marker
        job2_printed = threading.Event()  # Job 2 has printed its marker
        finish_jobs  = threading.Event()  # Allow both workers to exit

        job1_errors: list = []
        job2_errors: list = []

        def worker_job1():
            token = None
            try:
                # Bind this thread to job1 via the production context var
                token = api_server._current_job_id_var.set(job1_id)
                job1_ready.set()

                # Wait until Job 2 is also active so the two overlap
                if not job2_ready.wait(timeout=5.0):
                    raise TimeoutError("Job 2 did not become ready in time")

                # Print while Job 2 is concurrently active
                print("JOB_1_UNIQUE_PROGRESS_LINE")
                job1_printed.set()

                if not job2_printed.wait(timeout=5.0):
                    raise TimeoutError("Job 2 did not print in time")

                finish_jobs.wait(timeout=5.0)
            except Exception as exc:
                job1_errors.append(exc)
            finally:
                if token is not None:
                    api_server._current_job_id_var.reset(token)
                api_server.jobs[job1_id]["logs"] = capture1.lines

        def worker_job2():
            token = None
            try:
                # Wait until Job 1 is established first
                if not job1_ready.wait(timeout=5.0):
                    raise TimeoutError("Job 1 did not become ready in time")

                token = api_server._current_job_id_var.set(job2_id)
                job2_ready.set()

                # Wait for Job 1 to print
                if not job1_printed.wait(timeout=5.0):
                    raise TimeoutError("Job 1 did not print in time")

                # Print while Job 1 is concurrently active
                print("JOB_2_UNIQUE_PROGRESS_LINE")
                job2_printed.set()

                finish_jobs.set()
            except Exception as exc:
                job2_errors.append(exc)
            finally:
                if token is not None:
                    api_server._current_job_id_var.reset(token)
                api_server.jobs[job2_id]["logs"] = capture2.lines

        t1 = threading.Thread(target=worker_job1, name="pipeline-job1")
        t2 = threading.Thread(target=worker_job2, name="pipeline-job2")

        try:
            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)

            self.assertFalse(t1.is_alive(), "Worker thread 1 failed to finish")
            self.assertFalse(t2.is_alive(), "Worker thread 2 failed to finish")

            if job1_errors:
                raise job1_errors[0]
            if job2_errors:
                raise job2_errors[0]

            job1_logs = api_server.jobs[job1_id]["logs"]
            job2_logs = api_server.jobs[job2_id]["logs"]

            # Each job must contain its own marker and must NOT contain the other's.
            self.assertIn(
                "JOB_1_UNIQUE_PROGRESS_LINE",
                job1_logs,
                "Regression: Job 1 did not capture its own printed progress!"
            )
            self.assertNotIn(
                "JOB_2_UNIQUE_PROGRESS_LINE",
                job1_logs,
                "Regression: Job 1 captured output from concurrent Job 2!"
            )
            self.assertIn(
                "JOB_2_UNIQUE_PROGRESS_LINE",
                job2_logs,
                "Regression: Job 2 did not capture its own printed progress!"
            )
            self.assertNotIn(
                "JOB_1_UNIQUE_PROGRESS_LINE",
                job2_logs,
                "Regression: Job 2 captured output from concurrent Job 1!"
            )
        finally:
            finish_jobs.set()


class TestBackendContextIsolation(unittest.TestCase):
    """Tests that the contextvars-based backend binding isolates each thread."""

    def test_concurrent_backend_contexts_are_isolated(self):
        """
        Test C: Two concurrent threads each bind a different mock backend via
        bind_backend().  Each thread must see only its own backend when it calls
        get_backend(); neither thread may observe the other's backend.

        This verifies the contextvars.ContextVar implementation in
        data_backend/registry.py.
        """
        from data_backend.registry import bind_backend, reset_bound_backend, get_backend

        backend_a = MagicMock(name="BackendA")
        backend_b = MagicMock(name="BackendB")

        results = {}
        errors = []

        # Synchronization: thread_b waits until thread_a has bound its backend,
        # then both threads read get_backend() while both are inside their binding.
        a_bound = threading.Event()
        b_bound = threading.Event()

        def thread_a():
            token = None
            try:
                token = bind_backend(backend_a)
                a_bound.set()               # signal: A is bound
                b_bound.wait(timeout=5.0)   # wait until B is also bound
                results["a"] = get_backend()
            except Exception as exc:
                errors.append(("a", exc))
            finally:
                if token is not None:
                    reset_bound_backend(token)

        def thread_b():
            token = None
            try:
                a_bound.wait(timeout=5.0)   # wait until A is bound first
                token = bind_backend(backend_b)
                b_bound.set()               # signal: B is bound
                results["b"] = get_backend()
            except Exception as exc:
                errors.append(("b", exc))
            finally:
                if token is not None:
                    reset_bound_backend(token)

        t1 = threading.Thread(target=thread_a, name="pipeline-a")
        t2 = threading.Thread(target=thread_b, name="pipeline-b")
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        self.assertFalse(t1.is_alive(), "Thread A did not finish in time")
        self.assertFalse(t2.is_alive(), "Thread B did not finish in time")

        if errors:
            raise AssertionError(f"Worker thread errors: {errors}")

        self.assertIs(
            results.get("a"),
            backend_a,
            "Thread A's get_backend() returned the wrong backend",
        )
        self.assertIs(
            results.get("b"),
            backend_b,
            "Thread B's get_backend() returned the wrong backend",
        )


if __name__ == "__main__":
    unittest.main()

