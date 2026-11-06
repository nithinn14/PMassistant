"""
Regression tests for Excel and CSV backend operations in data_backend/.

Covers:
A. Write and read:
   - Excel and CSV save a new project dataset.
   - Saved data can be read back.
   - Writing in replace mode overwrites existing data.
   - Empty datasets can be saved and read.
   - Reading a missing dataset raises FileNotFoundError.

B. Append:
   - Appending adds new rows without removing existing rows.
   - Appending to a dataset that does not exist creates it.

C. Update:
   - Updating a matching Task_ID replaces the existing record.
   - Updating a new Task_ID inserts a new record.
   - Updating a missing dataset creates it.
   - Missing key columns produce a predictable KeyError.
   - Empty key_columns must not silently append data (documents current bug as expectedFailure).

D. Read filters:
   - A valid filter returns only matching records.
   - A filter referencing a nonexistent column must not silently return all rows (documents current bug as expectedFailure).

E. Excel/CSV consistency:
   - Equivalent write, read, append, and update operations produce equivalent results.
   - Employee-file path resolution comparison (isolated from real employee data).
   - Blank email handling comparison using synthetic records.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

# Ensure integrated_pm_assistant is on sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from data_backend.excel_backend import ExcelBackend
from data_backend.csv_backend import CSVBackend


class BaseBackendTestCase(unittest.TestCase):
    """Base test case providing isolated temporary directories and stdout redirection."""

    def setUp(self):
        # Redirect stdout to suppress console emoji prints on Windows cp1252
        self._stdout_trap = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self._stdout_trap)
        self._redirect.__enter__()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.tmp_dir.name)
        self.excel_backend = ExcelBackend(
            output_dir="output",
            root_dir=str(self.test_dir)
        )
        self.csv_backend = CSVBackend(
            directory=str(self.test_dir / "output"),
            root_dir=str(self.test_dir)
        )

    def tearDown(self):
        self._redirect.__exit__(None, None, None)
        self.tmp_dir.cleanup()


# ==============================================================================
# A. Write and Read Operations
# ==============================================================================
class TestWriteAndReadOperations(BaseBackendTestCase):
    """Tests for basic write and read operations across Excel and CSV backends."""

    def test_excel_write_and_read_new_dataset(self):
        """ExcelBackend saves a new project dataset and reads it back accurately."""
        df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Requirements", "Role": "PM", "Days": 2.0},
            {"Task_ID": "T2", "Task_Name": "Architecture", "Role": "Architect", "Days": 3.0},
        ])
        table_name = "ProjA_Tasks"

        self.excel_backend.write(table_name, df)
        self.assertTrue(self.excel_backend.exists(table_name))

        read_df = self.excel_backend.read(table_name)
        self.assertEqual(len(read_df), 2)
        self.assertEqual(list(read_df["Task_ID"]), ["T1", "T2"])
        self.assertEqual(list(read_df["Days"]), [2.0, 3.0])

    def test_csv_write_and_read_new_dataset(self):
        """CSVBackend saves a new project dataset and reads it back accurately."""
        df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Requirements", "Role": "PM", "Days": 2.0},
            {"Task_ID": "T2", "Task_Name": "Architecture", "Role": "Architect", "Days": 3.0},
        ])
        table_name = "ProjA_Tasks"

        self.csv_backend.write(table_name, df)
        self.assertTrue(self.csv_backend.exists(table_name))

        read_df = self.csv_backend.read(table_name)
        self.assertEqual(len(read_df), 2)
        self.assertEqual(list(read_df["Task_ID"]), ["T1", "T2"])
        self.assertEqual(list(read_df["Days"]), [2.0, 3.0])

    def test_excel_write_replace_mode_overwrites(self):
        """ExcelBackend in replace mode overwrites existing dataset completely."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Old 1"},
            {"Task_ID": "T2", "Task_Name": "Old 2"},
        ])
        self.excel_backend.write("Proj_Replace", initial_df)
        self.assertEqual(len(self.excel_backend.read("Proj_Replace")), 2)

        replacement_df = pd.DataFrame([
            {"Task_ID": "T3", "Task_Name": "New Only"},
        ])
        self.excel_backend.write("Proj_Replace", replacement_df, mode="replace")

        read_df = self.excel_backend.read("Proj_Replace")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T3")

    def test_csv_write_replace_mode_overwrites(self):
        """CSVBackend in replace mode overwrites existing dataset completely."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Old 1"},
            {"Task_ID": "T2", "Task_Name": "Old 2"},
        ])
        self.csv_backend.write("Proj_Replace", initial_df)
        self.assertEqual(len(self.csv_backend.read("Proj_Replace")), 2)

        replacement_df = pd.DataFrame([
            {"Task_ID": "T3", "Task_Name": "New Only"},
        ])
        self.csv_backend.write("Proj_Replace", replacement_df, mode="replace")

        read_df = self.csv_backend.read("Proj_Replace")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T3")

    def test_excel_save_and_read_empty_dataset(self):
        """ExcelBackend can save and read back an empty dataset with schema preserved."""
        empty_df = pd.DataFrame(columns=["Task_ID", "Task_Name", "Role"])
        self.excel_backend.write("Empty_Table", empty_df)

        read_df = self.excel_backend.read("Empty_Table")
        self.assertEqual(len(read_df), 0)
        self.assertEqual(list(read_df.columns), ["Task_ID", "Task_Name", "Role"])

    def test_csv_save_and_read_empty_dataset(self):
        """CSVBackend can save and read back an empty dataset with schema preserved."""
        empty_df = pd.DataFrame(columns=["Task_ID", "Task_Name", "Role"])
        self.csv_backend.write("Empty_Table", empty_df)

        read_df = self.csv_backend.read("Empty_Table")
        self.assertEqual(len(read_df), 0)
        self.assertEqual(list(read_df.columns), ["Task_ID", "Task_Name", "Role"])

    def test_excel_read_missing_dataset_raises(self):
        """ExcelBackend raises FileNotFoundError when reading a missing dataset."""
        with self.assertRaises(FileNotFoundError):
            self.excel_backend.read("Nonexistent_Dataset")

    def test_csv_read_missing_dataset_raises(self):
        """CSVBackend raises FileNotFoundError when reading a missing dataset."""
        with self.assertRaises(FileNotFoundError):
            self.csv_backend.read("Nonexistent_Dataset")


# ==============================================================================
# B. Append Operations
# ==============================================================================
class TestAppendOperations(BaseBackendTestCase):
    """Tests for appending rows across Excel and CSV backends."""

    def test_excel_append_adds_rows_without_removing_existing(self):
        """ExcelBackend.append() adds new rows while preserving existing rows."""
        df1 = pd.DataFrame([{"Task_ID": "T1", "Status": "Done"}])
        self.excel_backend.write("Append_Test", df1)

        df2 = pd.DataFrame([{"Task_ID": "T2", "Status": "Pending"}])
        self.excel_backend.append("Append_Test", df2)

        read_df = self.excel_backend.read("Append_Test")
        self.assertEqual(len(read_df), 2)
        self.assertEqual(list(read_df["Task_ID"]), ["T1", "T2"])
        self.assertEqual(list(read_df["Status"]), ["Done", "Pending"])

    def test_csv_append_adds_rows_without_removing_existing(self):
        """CSVBackend.append() adds new rows while preserving existing rows."""
        df1 = pd.DataFrame([{"Task_ID": "T1", "Status": "Done"}])
        self.csv_backend.write("Append_Test", df1)

        df2 = pd.DataFrame([{"Task_ID": "T2", "Status": "Pending"}])
        self.csv_backend.append("Append_Test", df2)

        read_df = self.csv_backend.read("Append_Test")
        self.assertEqual(len(read_df), 2)
        self.assertEqual(list(read_df["Task_ID"]), ["T1", "T2"])
        self.assertEqual(list(read_df["Status"]), ["Done", "Pending"])

    def test_excel_append_to_missing_dataset_creates_it(self):
        """ExcelBackend.append() creates the dataset if it does not already exist."""
        df = pd.DataFrame([{"Task_ID": "T1", "Name": "AutoCreated"}])
        self.assertFalse(self.excel_backend.exists("Auto_Created_Excel"))

        self.excel_backend.append("Auto_Created_Excel", df)
        self.assertTrue(self.excel_backend.exists("Auto_Created_Excel"))

        read_df = self.excel_backend.read("Auto_Created_Excel")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T1")

    def test_csv_append_to_missing_dataset_creates_it(self):
        """CSVBackend.append() creates the dataset if it does not already exist."""
        df = pd.DataFrame([{"Task_ID": "T1", "Name": "AutoCreated"}])
        self.assertFalse(self.csv_backend.exists("Auto_Created_CSV"))

        self.csv_backend.append("Auto_Created_CSV", df)
        self.assertTrue(self.csv_backend.exists("Auto_Created_CSV"))

        read_df = self.csv_backend.read("Auto_Created_CSV")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T1")


# ==============================================================================
# C. Update Operations
# ==============================================================================
class TestUpdateOperations(BaseBackendTestCase):
    """Tests for upsert (update) logic across Excel and CSV backends."""

    def test_excel_update_matching_key_replaces_record(self):
        """ExcelBackend.update() replaces existing record when key matches."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Status": "In Progress", "Hours": 10},
            {"Task_ID": "T2", "Status": "Pending", "Hours": 5},
        ])
        self.excel_backend.write("Update_Tasks", initial_df)

        update_df = pd.DataFrame([
            {"Task_ID": "T1", "Status": "Completed", "Hours": 12},
        ])
        self.excel_backend.update("Update_Tasks", update_df, key_columns=["Task_ID"])

        read_df = self.excel_backend.read("Update_Tasks")
        self.assertEqual(len(read_df), 2)
        t1_row = read_df[read_df["Task_ID"] == "T1"].iloc[0]
        self.assertEqual(t1_row["Status"], "Completed")
        self.assertEqual(t1_row["Hours"], 12)

        t2_row = read_df[read_df["Task_ID"] == "T2"].iloc[0]
        self.assertEqual(t2_row["Status"], "Pending")

    def test_csv_update_matching_key_replaces_record(self):
        """CSVBackend.update() replaces existing record when key matches."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Status": "In Progress", "Hours": 10},
            {"Task_ID": "T2", "Status": "Pending", "Hours": 5},
        ])
        self.csv_backend.write("Update_Tasks", initial_df)

        update_df = pd.DataFrame([
            {"Task_ID": "T1", "Status": "Completed", "Hours": 12},
        ])
        self.csv_backend.update("Update_Tasks", update_df, key_columns=["Task_ID"])

        read_df = self.csv_backend.read("Update_Tasks")
        self.assertEqual(len(read_df), 2)
        t1_row = read_df[read_df["Task_ID"] == "T1"].iloc[0]
        self.assertEqual(t1_row["Status"], "Completed")
        self.assertEqual(t1_row["Hours"], 12)

        t2_row = read_df[read_df["Task_ID"] == "T2"].iloc[0]
        self.assertEqual(t2_row["Status"], "Pending")

    def test_excel_update_new_key_inserts_record(self):
        """ExcelBackend.update() inserts a new record when key does not exist."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Task 1"},
        ])
        self.excel_backend.write("Insert_Tasks", initial_df)

        new_record = pd.DataFrame([
            {"Task_ID": "T2", "Task_Name": "Task 2"},
        ])
        self.excel_backend.update("Insert_Tasks", new_record, key_columns=["Task_ID"])

        read_df = self.excel_backend.read("Insert_Tasks")
        self.assertEqual(len(read_df), 2)
        self.assertCountEqual(list(read_df["Task_ID"]), ["T1", "T2"])

    def test_csv_update_new_key_inserts_record(self):
        """CSVBackend.update() inserts a new record when key does not exist."""
        initial_df = pd.DataFrame([
            {"Task_ID": "T1", "Task_Name": "Task 1"},
        ])
        self.csv_backend.write("Insert_Tasks", initial_df)

        new_record = pd.DataFrame([
            {"Task_ID": "T2", "Task_Name": "Task 2"},
        ])
        self.csv_backend.update("Insert_Tasks", new_record, key_columns=["Task_ID"])

        read_df = self.csv_backend.read("Insert_Tasks")
        self.assertEqual(len(read_df), 2)
        self.assertCountEqual(list(read_df["Task_ID"]), ["T1", "T2"])

    def test_excel_update_missing_dataset_creates_it(self):
        """ExcelBackend.update() on a missing dataset creates it with incoming rows."""
        df = pd.DataFrame([{"Task_ID": "T1", "Name": "First"}])
        self.assertFalse(self.excel_backend.exists("Missing_Update_Excel"))

        self.excel_backend.update("Missing_Update_Excel", df, key_columns=["Task_ID"])
        self.assertTrue(self.excel_backend.exists("Missing_Update_Excel"))

        read_df = self.excel_backend.read("Missing_Update_Excel")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T1")

    def test_csv_update_missing_dataset_creates_it(self):
        """CSVBackend.update() on a missing dataset creates it with incoming rows."""
        df = pd.DataFrame([{"Task_ID": "T1", "Name": "First"}])
        self.assertFalse(self.csv_backend.exists("Missing_Update_CSV"))

        self.csv_backend.update("Missing_Update_CSV", df, key_columns=["Task_ID"])
        self.assertTrue(self.csv_backend.exists("Missing_Update_CSV"))

        read_df = self.csv_backend.read("Missing_Update_CSV")
        self.assertEqual(len(read_df), 1)
        self.assertEqual(read_df.iloc[0]["Task_ID"], "T1")

    def test_excel_update_missing_key_column_raises_key_error(self):
        """ExcelBackend.update() raises KeyError when a key column is missing from the data."""
        df1 = pd.DataFrame([{"Task_ID": "T1", "Name": "A"}])
        self.excel_backend.write("Key_Error_Excel", df1)

        df2 = pd.DataFrame([{"Task_ID": "T1", "Name": "B"}])
        with self.assertRaises(KeyError):
            self.excel_backend.update("Key_Error_Excel", df2, key_columns=["Nonexistent_Key"])

    def test_csv_update_missing_key_column_raises_key_error(self):
        """CSVBackend.update() raises KeyError when a key column is missing from the data."""
        df1 = pd.DataFrame([{"Task_ID": "T1", "Name": "A"}])
        self.csv_backend.write("Key_Error_CSV", df1)

        df2 = pd.DataFrame([{"Task_ID": "T1", "Name": "B"}])
        with self.assertRaises(KeyError):
            self.csv_backend.update("Key_Error_CSV", df2, key_columns=["Nonexistent_Key"])

    @unittest.expectedFailure
    def test_excel_update_empty_key_columns_must_not_silently_append(self):
        """
        [KNOWN BUG] Intended safe behaviour: update() with empty key_columns should raise ValueError.
        Currently, ExcelBackend silently appends all rows without deduplication.
        """
        df1 = pd.DataFrame([{"Task_ID": "T1", "Name": "A"}])
        self.excel_backend.write("Empty_Key_Excel", df1)

        df2 = pd.DataFrame([{"Task_ID": "T1", "Name": "B"}])
        # Safe behaviour should reject empty key_columns to prevent accidental silent duplication
        with self.assertRaises(ValueError):
            self.excel_backend.update("Empty_Key_Excel", df2, key_columns=[])

    @unittest.expectedFailure
    def test_csv_update_empty_key_columns_must_not_silently_append(self):
        """
        [KNOWN BUG] Intended safe behaviour: update() with empty key_columns should raise ValueError.
        Currently, CSVBackend silently appends all rows without deduplication.
        """
        df1 = pd.DataFrame([{"Task_ID": "T1", "Name": "A"}])
        self.csv_backend.write("Empty_Key_CSV", df1)

        df2 = pd.DataFrame([{"Task_ID": "T1", "Name": "B"}])
        # Safe behaviour should reject empty key_columns to prevent accidental silent duplication
        with self.assertRaises(ValueError):
            self.csv_backend.update("Empty_Key_CSV", df2, key_columns=[])


# ==============================================================================
# D. Read Filter Operations
# ==============================================================================
class TestReadFilterOperations(BaseBackendTestCase):
    """Tests for filter handling in read() across Excel and CSV backends."""

    def test_excel_read_valid_filter(self):
        """ExcelBackend.read() with valid filters returns only matching rows."""
        df = pd.DataFrame([
            {"Task_ID": "T1", "Role": "Backend Developer", "Status": "Done"},
            {"Task_ID": "T2", "Role": "Frontend Developer", "Status": "Done"},
            {"Task_ID": "T3", "Role": "Backend Developer", "Status": "Pending"},
        ])
        self.excel_backend.write("Filter_Tasks", df)

        # Single filter
        filtered_single = self.excel_backend.read("Filter_Tasks", filters={"Role": "Backend Developer"})
        self.assertEqual(len(filtered_single), 2)
        self.assertEqual(list(filtered_single["Task_ID"]), ["T1", "T3"])

        # Multiple filters
        filtered_multi = self.excel_backend.read(
            "Filter_Tasks", filters={"Role": "Backend Developer", "Status": "Done"}
        )
        self.assertEqual(len(filtered_multi), 1)
        self.assertEqual(filtered_multi.iloc[0]["Task_ID"], "T1")

    def test_csv_read_valid_filter(self):
        """CSVBackend.read() with valid filters returns only matching rows."""
        df = pd.DataFrame([
            {"Task_ID": "T1", "Role": "Backend Developer", "Status": "Done"},
            {"Task_ID": "T2", "Role": "Frontend Developer", "Status": "Done"},
            {"Task_ID": "T3", "Role": "Backend Developer", "Status": "Pending"},
        ])
        self.csv_backend.write("Filter_Tasks", df)

        # Single filter
        filtered_single = self.csv_backend.read("Filter_Tasks", filters={"Role": "Backend Developer"})
        self.assertEqual(len(filtered_single), 2)
        self.assertEqual(list(filtered_single["Task_ID"]), ["T1", "T3"])

        # Multiple filters
        filtered_multi = self.csv_backend.read(
            "Filter_Tasks", filters={"Role": "Backend Developer", "Status": "Done"}
        )
        self.assertEqual(len(filtered_multi), 1)
        self.assertEqual(filtered_multi.iloc[0]["Task_ID"], "T1")

    @unittest.expectedFailure
    def test_excel_read_nonexistent_column_filter_must_not_return_all_rows(self):
        """
        [KNOWN BUG] Intended safe behaviour: filter referencing nonexistent column should raise KeyError.
        Currently, ExcelBackend silently ignores absent filter columns and returns the entire unfiltered dataset.
        """
        df = pd.DataFrame([
            {"Task_ID": "T1", "Role": "Dev"},
            {"Task_ID": "T2", "Role": "QA"},
        ])
        self.excel_backend.write("Ignore_Filter_Excel", df)

        # Filtering on a nonexistent column should raise KeyError or return empty DataFrame, not all rows
        with self.assertRaises(KeyError):
            self.excel_backend.read("Ignore_Filter_Excel", filters={"Nonexistent_Column": "Value"})

    @unittest.expectedFailure
    def test_csv_read_nonexistent_column_filter_must_not_return_all_rows(self):
        """
        [KNOWN BUG] Intended safe behaviour: filter referencing nonexistent column should raise KeyError.
        Currently, CSVBackend silently ignores absent filter columns and returns the entire unfiltered dataset.
        """
        df = pd.DataFrame([
            {"Task_ID": "T1", "Role": "Dev"},
            {"Task_ID": "T2", "Role": "QA"},
        ])
        self.csv_backend.write("Ignore_Filter_CSV", df)

        # Filtering on a nonexistent column should raise KeyError or return empty DataFrame, not all rows
        with self.assertRaises(KeyError):
            self.csv_backend.read("Ignore_Filter_CSV", filters={"Nonexistent_Column": "Value"})


# ==============================================================================
# E. Excel / CSV Consistency Operations
# ==============================================================================
class TestExcelCsvConsistency(BaseBackendTestCase):
    """Cross-backend consistency tests comparing ExcelBackend and CSVBackend."""

    def test_excel_csv_operations_equivalence(self):
        """
        Equivalent write, append, and update operations produce equivalent results
        in both backends, accounting for expected file-format type conversions.
        """
        initial_tasks = [
            {"Task_ID": "T1", "Task_Name": "Design", "Role": "Architect", "Days": 2.0},
            {"Task_ID": "T2", "Task_Name": "Backend", "Role": "Developer", "Days": 5.0},
        ]
        init_df = pd.DataFrame(initial_tasks)

        # 1. Write initial
        self.excel_backend.write("Equiv_Tasks", init_df)
        self.csv_backend.write("Equiv_Tasks", init_df)

        # 2. Append new row
        append_df = pd.DataFrame([{"Task_ID": "T3", "Task_Name": "QA", "Role": "Tester", "Days": 3.0}])
        self.excel_backend.append("Equiv_Tasks", append_df)
        self.csv_backend.append("Equiv_Tasks", append_df)

        # 3. Update existing row (T2) and insert new row (T4)
        update_df = pd.DataFrame([
            {"Task_ID": "T2", "Task_Name": "Backend API", "Role": "Developer", "Days": 6.0},
            {"Task_ID": "T4", "Task_Name": "Deployment", "Role": "DevOps", "Days": 1.0},
        ])
        self.excel_backend.update("Equiv_Tasks", update_df, key_columns=["Task_ID"])
        self.csv_backend.update("Equiv_Tasks", update_df, key_columns=["Task_ID"])

        # 4. Read back and compare
        excel_res = self.excel_backend.read("Equiv_Tasks").sort_values("Task_ID").reset_index(drop=True)
        csv_res = self.csv_backend.read("Equiv_Tasks").sort_values("Task_ID").reset_index(drop=True)

        self.assertEqual(len(excel_res), 4)
        self.assertEqual(len(csv_res), 4)
        self.assertEqual(list(excel_res["Task_ID"]), list(csv_res["Task_ID"]))
        self.assertEqual(list(excel_res["Task_Name"]), list(csv_res["Task_Name"]))
        self.assertEqual(list(excel_res["Role"]), list(csv_res["Role"]))
        self.assertEqual(list(excel_res["Days"]), list(csv_res["Days"]))

    def test_employee_file_path_resolution_consistency(self):
        """Both backends resolve employee files under root_dir."""

        excel_path = self.excel_backend._resolve_path("employees")
        csv_path = self.csv_backend._path("employees")

        self.assertEqual(excel_path, self.test_dir / "employees.xlsx")
        self.assertEqual(csv_path, self.test_dir / "employees.csv")

    def test_excel_file_map_resolves_relative_path_under_root(self):
        backend = ExcelBackend(
            root_dir=str(self.test_dir),
            file_map={"employees": "storage/employees.xlsx"}
        )

        expected_path = self.test_dir / "storage" / "employees.xlsx"
        self.assertEqual(backend._resolve_path("employees"), expected_path)

    def test_blank_email_handling_consistency(self):
        """
        Compare blank email handling between ExcelBackend and CSVBackend using synthetic records.
        Confirms that ExcelBackend sanitizes blank/NaN emails to None, whereas CSVBackend does not.
        """
        synthetic_employees = [
            {"Employee_ID": "E1", "Employee_Name": "Alice", "Email": "alice@example.com"},
            {"Employee_ID": "E2", "Employee_Name": "Bob", "Email": ""},
            {"Employee_ID": "E3", "Employee_Name": "Charlie", "Email": np.nan},
        ]
        emp_df = pd.DataFrame(synthetic_employees)

        # 1. Test ExcelBackend email sanitization
        self.excel_backend.write("employees", emp_df)
        excel_emp = self.excel_backend.read("employees")

        alice_excel = excel_emp[excel_emp["Employee_ID"] == "E1"].iloc[0]
        bob_excel = excel_emp[excel_emp["Employee_ID"] == "E2"].iloc[0]
        charlie_excel = excel_emp[excel_emp["Employee_ID"] == "E3"].iloc[0]

        self.assertEqual(alice_excel["Email"], "alice@example.com")
        self.assertIsNone(bob_excel["Email"])
        self.assertIsNone(charlie_excel["Email"])

        # 2. Test CSVBackend email handling in an isolated temporary working directory
        orig_cwd = os.getcwd()
        try:
            os.chdir(self.test_dir)
            emp_df.to_csv("employees.csv", index=False)

            # CSVBackend reads from Path("employees.csv") in current working directory
            csv_emp = self.csv_backend.read("employees")
            alice_csv = csv_emp[csv_emp["Employee_ID"] == "E1"].iloc[0]
            bob_csv = csv_emp[csv_emp["Employee_ID"] == "E2"].iloc[0]
            charlie_csv = csv_emp[csv_emp["Employee_ID"] == "E3"].iloc[0]

            self.assertEqual(alice_csv["Email"], "alice@example.com")
            # In CSVBackend, empty string or NaN is parsed by pandas as NaN, not sanitized to None
            self.assertTrue(pd.isna(bob_csv["Email"]) or bob_csv["Email"] == "")
            self.assertTrue(pd.isna(charlie_csv["Email"]))
            # Confirm inconsistency: CSVBackend does not normalize to Python None
            self.assertIsNotNone(charlie_csv["Email"])
        finally:
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()

