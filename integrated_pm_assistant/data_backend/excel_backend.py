"""
Excel backend — the **default** backend.

Preserves full backward compatibility with the original project.
Logical table names are mapped to ``.xlsx`` files:

* ``"employees"``         → ``employees.xlsx``   (project root)
* ``"MyProject_Tasks"``   → ``output/MyProject_Tasks.xlsx``
"""

import errno
import os
import time
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pandas as pd

from data_backend.base import DataBackend
from paths import resolve_path


# ---------------------------------------------------------------------------
# Cross-platform non-blocking file locking helpers
# ---------------------------------------------------------------------------

if os.name == "nt":
    import msvcrt

    _LOCK_CONTENTION_ERRNOS = {errno.EACCES, getattr(errno, "EDEADLK", 36)}

    def _lock_file_nonblocking(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_file(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    _LOCK_CONTENTION_ERRNOS = {
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        errno.EACCES,
    }

    def _lock_file_nonblocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def _acquire_file_lock(fd: int, timeout: float = 30.0, poll_interval: float = 0.05) -> None:
    start_time = time.time()
    while True:
        try:
            _lock_file_nonblocking(fd)
            return
        except OSError as exc:
            if exc.errno in _LOCK_CONTENTION_ERRNOS:
                if time.time() - start_time >= timeout:
                    raise TimeoutError(f"Timed out after {timeout}s waiting for storage file lock.") from exc
                time.sleep(poll_interval)
            else:
                raise


_process_thread_locks: Dict[Path, threading.RLock] = {}
_process_locks_mutex = threading.Lock()


def _get_thread_lock(lock_path: Path) -> threading.RLock:
    resolved = lock_path.resolve()
    with _process_locks_mutex:
        if resolved not in _process_thread_locks:
            _process_thread_locks[resolved] = threading.RLock()
        return _process_thread_locks[resolved]


_thread_local = threading.local()


class ExcelBackend(DataBackend):
    """Read/write data as ``.xlsx`` files via ``openpyxl``."""

    def __init__(self, output_dir: str = "output", root_dir: str = ".", file_map: Optional[Dict[str, str]] = None):
        self._root_dir = resolve_path(root_dir)
        self._output_dir = resolve_path(output_dir, base=self._root_dir)
        self._file_map = file_map or {}
        self._output_dir.mkdir(parents=True, exist_ok=True)

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
            return resolve_path(self._file_map[table_name], base=self._root_dir)

        root_tables = {"employees"}

        if table_name.lower() in root_tables:
            path = self._root_dir / f"{table_name}.xlsx"
        else:
            path = self._output_dir / f"{table_name}.xlsx"

        print(f"📊 [Excel] Dataset '{table_name}' -> file: {path}")
        return path

    def _atomic_write_excel(
        self,
        path: Path,
        df: pd.DataFrame,
        max_retries: int = 5,
        base_delay: float = 0.05,
    ) -> None:
        """
        Write DataFrame to a temporary file in the same directory,
        then atomically replace the destination file via os.replace().
        Handles transient Windows PermissionError with bounded retries.
        Cleans up the temporary file on success or failure.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".tmp_{path.stem}_{uuid.uuid4().hex}{path.suffix}"
        try:
            df.to_excel(temp_path, index=False)
            for attempt in range(max_retries):
                try:
                    os.replace(temp_path, path)
                    break
                except PermissionError:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(base_delay * (2 ** attempt))
        finally:
            if temp_path.exists():
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _safe_read_excel(
        self,
        path: Path,
        max_retries: int = 5,
        base_delay: float = 0.05,
    ) -> pd.DataFrame:
        """
        Read Excel file with bounded retries for transient PermissionError.
        """
        for attempt in range(max_retries):
            try:
                return pd.read_excel(path)
            except PermissionError:
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self, table_name: str, timeout: float = 30.0) -> Iterator[None]:
        """
        Acquire an exclusive transaction lock on *table_name* across
        all threads and processes. Safe for re-entrant calls within the same thread.
        """
        path = self._resolve_path(table_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f".{path.name}.lock"
        resolved_lock_path = lock_path.resolve()

        thread_lock = _get_thread_lock(resolved_lock_path)
        acquired = thread_lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Timed out after {timeout}s waiting for thread lock on table '{table_name}'.")

        if not hasattr(_thread_local, "held_locks"):
            _thread_local.held_locks = {}

        held_info = _thread_local.held_locks.get(resolved_lock_path)
        if held_info is not None:
            # Re-entrant call within the same thread
            held_info["count"] += 1
            try:
                yield
            finally:
                held_info["count"] -= 1
                thread_lock.release()
            return

        # First acquisition in this thread: acquire cross-process OS file lock
        fd = None
        try:
            fd = os.open(str(resolved_lock_path), os.O_RDWR | os.O_CREAT, 0o666)
            _acquire_file_lock(fd, timeout=timeout)
            _thread_local.held_locks[resolved_lock_path] = {"count": 1, "fd": fd}
            try:
                yield
            finally:
                _thread_local.held_locks.pop(resolved_lock_path, None)
                _unlock_file(fd)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            thread_lock.release()

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

        df = self._safe_read_excel(path)

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

        with self.transaction(table_name):
            if mode == "append" and path.exists():
                existing = self._safe_read_excel(path)
                df = pd.concat([existing, df], ignore_index=True)

            self._atomic_write_excel(path, df)

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

        with self.transaction(table_name):
            if not path.exists():
                self._atomic_write_excel(path, df)
                return

            existing = self._safe_read_excel(path)

            # Drop rows from existing that match keys in the incoming df
            if key_columns:
                merge_keys = existing[key_columns].apply(tuple, axis=1)
                new_keys = df[key_columns].apply(tuple, axis=1)
                existing = existing[~merge_keys.isin(new_keys)]

            merged = pd.concat([existing, df], ignore_index=True)
            self._atomic_write_excel(path, merged)
