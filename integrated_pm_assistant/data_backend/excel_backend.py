"""
Excel backend — the **default** backend.

Preserves full backward compatibility with the original project.
Logical table names are mapped to ``.xlsx`` files:

* ``"employees"``         → ``employees.xlsx``   (project root)
* ``"MyProject_Tasks"``   → ``output/MyProject_Tasks.xlsx``
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from data_backend.base import DataBackend


class ExcelBackend(DataBackend):
    """Read/write data as ``.xlsx`` files via ``openpyxl``."""

    def __init__(self, output_dir: str = "output", root_dir: str = ".", file_map: Optional[Dict[str, str]] = None):
        self._output_dir = Path(root_dir) / output_dir
        self._root_dir = Path(root_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._file_map = file_map or {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_path(self, table_name: str) -> Path:
        """
        Translate a logical table name to a file path.

        1) Check explicit file_map config.
        2) Fallback: special tables that live in the project root.
        3) Fallback: output directory.
        """
        if table_name in self._file_map:
            return self._root_dir / self._file_map[table_name]

        root_tables = {"employees"}

        if table_name.lower() in root_tables:
            path = self._root_dir / f"{table_name}.xlsx"
        else:
            path = self._output_dir / f"{table_name}.xlsx"

        print(f"📊 [Excel] Dataset '{table_name}' -> file: {path}")
        return path

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    def read(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        path = self._resolve_path(table_name)

        if not path.exists():
            raise FileNotFoundError(f"{path} not found")

        df = pd.read_excel(path)

        # Sanitize blank / NaN email cells to None at data-loading time
        if table_name.lower() == "employees" and "Email" in df.columns:
            df["Email"] = df["Email"].apply(
                lambda v: None if pd.isna(v) or str(v).strip().lower() in {"nan", "none", "null", ""} else str(v).strip()
            )

        if filters:
            for col, val in filters.items():
                if col in df.columns:
                    df = df[df[col] == val]

        return df

    def write(
        self,
        table_name: str,
        df: pd.DataFrame,
        mode: str = "replace",
    ) -> None:
        path = self._resolve_path(table_name)
        print(f"📊 [Excel] Dataset '{table_name}' -> Writing to: {path} (mode: {mode})")

        if mode == "append" and path.exists():
            existing = pd.read_excel(path)
            df = pd.concat([existing, df], ignore_index=True)

        df.to_excel(path, index=False)

    def append(self, table_name: str, df: pd.DataFrame) -> None:
        self.write(table_name, df, mode="append")

    def exists(self, table_name: str) -> bool:
        return self._resolve_path(table_name).exists()

    def update(
        self,
        table_name: str,
        df: pd.DataFrame,
        key_columns: List[str],
    ) -> None:
        path = self._resolve_path(table_name)

        if not path.exists():
            df.to_excel(path, index=False)
            return

        existing = pd.read_excel(path)

        # Drop rows from existing that match keys in the incoming df
        if key_columns:
            merge_keys = existing[key_columns].apply(tuple, axis=1)
            new_keys = df[key_columns].apply(tuple, axis=1)
            existing = existing[~merge_keys.isin(new_keys)]

        merged = pd.concat([existing, df], ignore_index=True)
        merged.to_excel(path, index=False)
