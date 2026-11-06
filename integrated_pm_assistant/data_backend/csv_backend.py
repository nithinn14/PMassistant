"""
CSV backend — simple flat-file storage using ``.csv``.

    data_backend:
      type: csv
      csv:
        directory: "output/"
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from data_backend.base import DataBackend
from paths import resolve_path

class CSVBackend(DataBackend):
    """Read/write data as ``.csv`` files."""

    def __init__(self, directory: str = "output", root_dir: str = "."):
        self._root_dir = resolve_path(root_dir)
        self._dir = resolve_path(directory, base=self._root_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, table_name: str) -> Path:
        root_tables = {"employees"}
        if table_name.lower() in root_tables:
            return self._root_dir / f"{table_name}.csv"
        return self._dir / f"{table_name}.csv"

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    def read(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        path = self._path(table_name)
        if not path.exists():
            raise FileNotFoundError(f"{path} not found")

        df = pd.read_csv(path)

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
        path = self._path(table_name)

        if mode == "append" and path.exists():
            existing = pd.read_csv(path)
            df = pd.concat([existing, df], ignore_index=True)

        df.to_csv(path, index=False)

    def append(self, table_name: str, df: pd.DataFrame) -> None:
        self.write(table_name, df, mode="append")

    def exists(self, table_name: str) -> bool:
        return self._path(table_name).exists()

    def update(
        self,
        table_name: str,
        df: pd.DataFrame,
        key_columns: List[str],
    ) -> None:
        path = self._path(table_name)

        if not path.exists():
            df.to_csv(path, index=False)
            return

        existing = pd.read_csv(path)

        if key_columns:
            merge_keys = existing[key_columns].apply(tuple, axis=1)
            new_keys = df[key_columns].apply(tuple, axis=1)
            existing = existing[~merge_keys.isin(new_keys)]

        merged = pd.concat([existing, df], ignore_index=True)
        merged.to_csv(path, index=False)
