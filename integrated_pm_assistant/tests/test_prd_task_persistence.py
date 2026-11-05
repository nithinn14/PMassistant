"""
Regression tests for PRD and task persistence:
1. PRD persistence (save_prd_tool.py):
   - Valid PRD input creates the expected JSON file with all required fields.
   - Single-item and multi-item list-wrapped strings are normalized correctly with warnings.
   - PDF generation is mocked so no real PDF or production output files are created.
2. Task persistence (save_tasks_tool.py):
   - Valid task JSON is saved through a temporary test backend.
   - Task IDs and dependencies are preserved in the saved data.
   - Expected task columns are present.
   - Empty task lists raise ValueError.
   - Missing project_name or tasks raises KeyError.
   - Invalid JSON raises JSONDecodeError.
   - Passing a Python dictionary instead of a JSON string produces TypeError.
3. Backend round-trip:
   - ExcelBackend can write and read task data in a temporary directory.
   - CSVBackend can write and read task data in a temporary directory.
"""

import ast
import contextlib
import io
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from data_backend.base import DataBackend
from data_backend.excel_backend import ExcelBackend
from data_backend.csv_backend import CSVBackend
from data_backend.registry import bind_backend, reset_bound_backend
from tools.save_prd_tool import save_prd
from tools.save_tasks_tool import save_tasks


class InMemoryBackend(DataBackend):
    """In-memory data backend for isolated tool testing."""

    def __init__(self, initial_data=None):
        self.storage = {}
        if initial_data:
            for k, v in initial_data.items():
                self.storage[k] = v.copy(deep=True) if isinstance(v, pd.DataFrame) else v

    def read(self, table_name: str, filters: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        if table_name in self.storage:
            df = self.storage[table_name].copy(deep=True)
            if filters:
                for col, val in filters.items():
                    if col in df.columns:
                        df = df[df[col] == val]
            return df
        raise FileNotFoundError(f"{table_name} not found")

    def write(self, table_name: str, df: pd.DataFrame, mode: str = "replace") -> None:
        if mode == "append" and table_name in self.storage:
            self.storage[table_name] = pd.concat([self.storage[table_name], df], ignore_index=True)
        else:
            self.storage[table_name] = df.copy(deep=True)

    def append(self, table_name: str, df: pd.DataFrame) -> None:
        self.write(table_name, df, mode="append")

    def exists(self, table_name: str) -> bool:
        return table_name in self.storage

    def update(self, table_name: str, df: pd.DataFrame, key_columns: List[str]) -> None:
        self.storage[table_name] = df.copy(deep=True)


class TestPrdPersistence(unittest.TestCase):
    """Tests for save_prd_tool.py."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _fake_path(self, arg):
        """Redirect __file__ relative paths in save_prd_tool to the temporary directory."""
        if "save_prd_tool" in str(arg):
            return self.test_dir / "tools" / "save_prd_tool.py"
        return Path(arg)

    def test_valid_prd_input_creates_expected_json(self):
        """Valid PRD input creates the expected JSON file with all required fields."""
        pname = "Synthetic_PRD_Project"
        goals = ["Deliver MVP in 4 weeks", "Achieve 99% uptime"]
        personas = ["Project Manager", "Software Engineer"]
        func_reqs = ["Task assignment", "Gantt chart generation"]
        non_func_reqs = ["Sub-second API response", "Data persistence"]
        constraints = ["Use Python 3.11", "No external paid APIs"]
        assumptions = ["Team works 8 hours per day", "Weekends are non-working"]
        out_of_scope = ["Mobile native app", "Billing integration"]

        buf = io.StringIO()
        with patch("tools.save_prd_tool.Path", side_effect=self._fake_path), \
             patch("tools.save_prd_tool.save_prd_pdf", return_value=str(self.test_dir / "mock.pdf")) as mock_pdf, \
             contextlib.redirect_stdout(buf):

            res = save_prd(
                product_name=pname,
                problem_statement="Projects lack automated coordination.",
                goals=goals,
                user_personas=personas,
                functional_requirements=func_reqs,
                non_functional_requirements=non_func_reqs,
                constraints=constraints,
                assumptions=assumptions,
                out_of_scope=out_of_scope,
            )

        self.assertEqual(res, {"project_name": pname})
        mock_pdf.assert_called_once()

        # Check created JSON file
        json_path = self.test_dir / "output" / f"{pname}_PRD.json"
        self.assertTrue(json_path.exists(), f"PRD JSON file was not created at {json_path}")

        saved_data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["product_name"], pname)
        self.assertEqual(saved_data["problem_statement"], "Projects lack automated coordination.")
        self.assertEqual(saved_data["goals"], goals)
        self.assertEqual(saved_data["user_personas"], personas)
        self.assertEqual(saved_data["functional_requirements"], func_reqs)
        self.assertEqual(saved_data["non_functional_requirements"], non_func_reqs)
        self.assertEqual(saved_data["constraints"], constraints)
        self.assertEqual(saved_data["assumptions"], assumptions)
        self.assertEqual(saved_data["out_of_scope"], out_of_scope)

    def test_string_field_normalization_for_list_wrapped_inputs(self):
        """Single-item and multi-item list-wrapped strings are normalized with warnings."""
        buf = io.StringIO()
        with patch("tools.save_prd_tool.Path", side_effect=self._fake_path), \
             patch("tools.save_prd_tool.save_prd_pdf", return_value=str(self.test_dir / "mock.pdf")), \
             contextlib.redirect_stdout(buf), \
             warnings.catch_warnings(record=True) as recorded_warnings:

            warnings.simplefilter("always")

            res = save_prd(
                product_name=["Wrapped Project Name"],  # single-item list
                problem_statement=["Part One of Problem.", "Part Two of Problem."],  # multi-item list
                goals=["G1"],
                user_personas=["P1"],
                functional_requirements=["F1"],
                non_functional_requirements=["NF1"],
                constraints=["C1"],
                assumptions=["A1"],
                out_of_scope=["O1"],
            )

        self.assertEqual(res["project_name"], "Wrapped Project Name")

        # Verify warnings were emitted for both list-wrapped fields
        warn_messages = [str(w.message) for w in recorded_warnings]
        self.assertTrue(any("arrived as a single-item list" in m and "product_name" in m for m in warn_messages))
        self.assertTrue(any("arrived as a multi-item list" in m and "problem_statement" in m for m in warn_messages))

        # Check saved JSON reflects normalized strings
        json_path = self.test_dir / "output" / "Wrapped Project Name_PRD.json"
        self.assertTrue(json_path.exists())
        saved_data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["product_name"], "Wrapped Project Name")
        self.assertEqual(saved_data["problem_statement"], "Part One of Problem. Part Two of Problem.")

    def test_pdf_generation_is_mocked_preventing_real_pdf_creation(self):
        """PDF generation can be mocked so no ReportLab document is compiled to disk."""
        mock_pdf_func = MagicMock(return_value="/mock/path/doc.pdf")

        buf = io.StringIO()
        with patch("tools.save_prd_tool.Path", side_effect=self._fake_path), \
             patch("tools.save_prd_tool.save_prd_pdf", mock_pdf_func), \
             contextlib.redirect_stdout(buf):

            save_prd(
                product_name="Mock_PDF_Project",
                problem_statement="Problem Statement",
                goals=[],
                user_personas=[],
                functional_requirements=[],
                non_functional_requirements=[],
                constraints=[],
                assumptions=[],
                out_of_scope=[],
            )

        mock_pdf_func.assert_called_once()
        prd_arg, name_arg = mock_pdf_func.call_args[0]
        self.assertEqual(name_arg, "Mock_PDF_Project")
        self.assertEqual(prd_arg["product_name"], "Mock_PDF_Project")


class TestTaskPersistence(unittest.TestCase):
    """Tests for save_tasks_tool.py."""

    def setUp(self):
        self.backend = InMemoryBackend()
        self.token = bind_backend(self.backend)

    def tearDown(self):
        if self.token is not None:
            reset_bound_backend(self.token)

    def test_valid_task_json_saved_through_backend(self):
        """Valid task JSON is saved through the active DataBackend."""
        payload = json.dumps({
            "project_name": "TestProj",
            "tasks": [
                {
                    "task_id": "T1",
                    "task_name": "Architecture Design",
                    "assigned_role": "Backend Developer",
                    "time_days": 2.0,
                    "dependencies": [],
                },
                {
                    "task_id": "T2",
                    "task_name": "Frontend Wireframes",
                    "assigned_role": "UI/UX Designer",
                    "time_days": 1.5,
                    "dependencies": ["T1"],
                },
            ],
        })

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result_str = save_tasks(payload)

        # Confirm return value
        result = json.loads(result_str)
        self.assertEqual(result["project_name"], "TestProj")
        self.assertEqual(len(result["tasks"]), 2)

        # Confirm data was written to backend under "TestProj_Tasks"
        self.assertTrue(self.backend.exists("TestProj_Tasks"))
        saved_df = self.backend.read("TestProj_Tasks")
        self.assertEqual(len(saved_df), 2)

    def test_task_ids_and_dependencies_preserved(self):
        """Task IDs and dependency lists are preserved in the saved data."""
        payload = json.dumps({
            "project_name": "DepProj",
            "tasks": [
                {"task_id": "TASK-101", "task_name": "A", "assigned_role": "Backend Developer", "dependencies": []},
                {"task_id": "TASK-102", "task_name": "B", "assigned_role": "Backend Developer", "dependencies": ["TASK-101"]},
                {"task_id": "TASK-103", "task_name": "C", "assigned_role": "Backend Developer", "dependencies": ["TASK-101", "TASK-102"]},
            ],
        })

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            save_tasks(payload)

        saved_df = self.backend.read("DepProj_Tasks")
        self.assertEqual(list(saved_df["task_id"]), ["TASK-101", "TASK-102", "TASK-103"])
        self.assertEqual(saved_df.iloc[0]["dependencies"], [])
        self.assertEqual(saved_df.iloc[1]["dependencies"], ["TASK-101"])
        self.assertEqual(saved_df.iloc[2]["dependencies"], ["TASK-101", "TASK-102"])

    def test_expected_task_columns_present(self):
        """Expected columns (task_id, task_name, assigned_role, time_days, dependencies) are present in the DataFrame."""
        payload = json.dumps({
            "project_name": "ColProj",
            "tasks": [
                {
                    "task_id": "T1",
                    "task_name": "Initial Task",
                    "assigned_role": "DevOps Engineer",
                    "time_days": 1.0,
                    "dependencies": [],
                }
            ],
        })

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            save_tasks(payload)

        saved_df = self.backend.read("ColProj_Tasks")
        for expected_col in ["task_id", "task_name", "assigned_role", "time_days", "dependencies"]:
            self.assertIn(expected_col, saved_df.columns)

    def test_empty_task_list_raises_value_error(self):
        """Empty task list raises ValueError('Valid \\'tasks\\' list is required.')."""
        payload = json.dumps({
            "project_name": "EmptyProj",
            "tasks": [],
        })

        with self.assertRaises(ValueError) as ctx:
            save_tasks(payload)

        self.assertIn("Valid 'tasks' list is required", str(ctx.exception))

    def test_missing_project_name_raises_key_error(self):
        """Payload missing 'project_name' raises KeyError."""
        payload = json.dumps({
            "tasks": [{"task_id": "T1", "task_name": "Task"}],
        })

        with self.assertRaises(KeyError) as ctx:
            save_tasks(payload)

        self.assertIn("project_name", str(ctx.exception))

    def test_missing_tasks_raises_key_error(self):
        """Payload missing 'tasks' raises KeyError."""
        payload = json.dumps({
            "project_name": "NoTasksProj",
        })

        with self.assertRaises(KeyError) as ctx:
            save_tasks(payload)

        self.assertIn("tasks", str(ctx.exception))

    def test_invalid_json_raises_json_decode_error(self):
        """Malformed JSON string raises JSONDecodeError."""
        malformed = '{"project_name": "Broken", "tasks": [broken json'

        with self.assertRaises(json.JSONDecodeError):
            save_tasks(malformed)

    def test_passing_dictionary_instead_of_json_string_raises_type_error(self):
        """Passing a Python dict instead of a JSON string raises TypeError in json.loads()."""
        raw_dict = {
            "project_name": "DictProj",
            "tasks": [{"task_id": "T1"}],
        }

        with self.assertRaises(TypeError):
            save_tasks(raw_dict)  # type: ignore[arg-type]


class TestBackendRoundTrip(unittest.TestCase):
    """Tests for ExcelBackend and CSVBackend reading and writing tasks."""

    def test_excel_backend_write_and_read_roundtrip(self):
        """ExcelBackend writes and reads task data in a temporary directory, accounting for list serialization."""
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, contextlib.redirect_stdout(buf):
            backend = ExcelBackend(output_dir="output", root_dir=tmp_dir)

            tasks = [
                {
                    "task_id": "T1",
                    "task_name": "Database Schema",
                    "assigned_role": "Backend Developer",
                    "time_days": 2.0,
                    "dependencies": [],
                },
                {
                    "task_id": "T2",
                    "task_name": "API Endpoints",
                    "assigned_role": "Backend Developer",
                    "time_days": 3.0,
                    "dependencies": ["T1"],
                },
            ]
            df = pd.DataFrame(tasks)
            table_name = "ExcelProj_Tasks"

            # Write via ExcelBackend
            backend.write(table_name, df)
            self.assertTrue(backend.exists(table_name))

            # Read back
            read_df = backend.read(table_name)
            self.assertEqual(len(read_df), 2)
            self.assertEqual(list(read_df["task_id"]), ["T1", "T2"])
            self.assertEqual(list(read_df["time_days"]), [2.0, 3.0])

            # Excel serializes list as string "['T1']"; verify string representation and parseability
            raw_dep = read_df.iloc[1]["dependencies"]
            parsed_dep = ast.literal_eval(raw_dep) if isinstance(raw_dep, str) else raw_dep
            self.assertEqual(parsed_dep, ["T1"])

    def test_csv_backend_write_and_read_roundtrip(self):
        """CSVBackend writes and reads task data in a temporary directory, accounting for list serialization."""
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, contextlib.redirect_stdout(buf):
            backend = CSVBackend(directory=tmp_dir)

            tasks = [
                {
                    "task_id": "T1",
                    "task_name": "Architecture Setup",
                    "assigned_role": "DevOps Engineer",
                    "time_days": 1.0,
                    "dependencies": [],
                },
                {
                    "task_id": "T2",
                    "task_name": "Containerization",
                    "assigned_role": "DevOps Engineer",
                    "time_days": 2.0,
                    "dependencies": ["T1"],
                },
            ]
            df = pd.DataFrame(tasks)
            table_name = "CsvProj_Tasks"

            # Write via CSVBackend
            backend.write(table_name, df)
            self.assertTrue(backend.exists(table_name))

            # Read back
            read_df = backend.read(table_name)
            self.assertEqual(len(read_df), 2)
            self.assertEqual(list(read_df["task_id"]), ["T1", "T2"])
            self.assertEqual(list(read_df["time_days"]), [1.0, 2.0])

            # CSV serializes list as string "['T1']"; verify string representation and parseability
            raw_dep = read_df.iloc[1]["dependencies"]
            parsed_dep = ast.literal_eval(raw_dep) if isinstance(raw_dep, str) else raw_dep
            self.assertEqual(parsed_dep, ["T1"])


if __name__ == "__main__":
    unittest.main()
