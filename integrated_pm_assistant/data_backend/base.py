"""
Abstract base class for all data backends.

Every backend (Excel, SQL, Odoo, CSV, …) must implement these five methods.
Tools/agents only depend on this interface, never on a concrete backend.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import pandas as pd


class DataBackend(ABC):
    """Uniform data access interface for the PM Assistant."""

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self, table_name: str) -> Iterator[None]:
        """
        Acquire an exclusive transaction lock on *table_name* across
        threads and processes for safe read-modify-write operations.

        Default implementation is a no-op context manager for backends
        that do not require file-based serialization (e.g. ACID databases).
        """
        yield

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    @abstractmethod
    def read(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        """
        Read all rows from *table_name*, optionally filtered.

        Parameters
        ----------
        table_name : str
            Logical name, e.g. ``"employees"`` or ``"AlphaCloud_Tasks"``.
        filters : dict, optional
            Column-value pairs used to filter rows,
            e.g. ``{"Project": "Alpha Cloud"}``.

        Returns
        -------
        pd.DataFrame
        """
        ...

    @abstractmethod
    def write(
        self,
        table_name: str,
        df: pd.DataFrame,
        mode: str = "replace",
    ) -> None:
        """
        Write *df* to *table_name*.

        Parameters
        ----------
        mode : str
            ``"replace"`` — overwrite existing data.
            ``"append"``  — add rows without deleting old ones.
        """
        ...

    @abstractmethod
    def append(self, table_name: str, df: pd.DataFrame) -> None:
        """Convenience wrapper: ``write(table_name, df, mode="append")``."""
        ...

    @abstractmethod
    def exists(self, table_name: str) -> bool:
        """Return ``True`` if *table_name* already has data."""
        ...

    @abstractmethod
    def update(
        self,
        table_name: str,
        df: pd.DataFrame,
        key_columns: List[str],
    ) -> None:
        """
        Upsert: update rows matched by *key_columns*, insert unmatched.
        """
        ...
