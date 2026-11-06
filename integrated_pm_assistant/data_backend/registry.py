"""
BackendFactory — creates and caches the configured DataBackend.

Usage:
    from data_backend import get_backend
    backend = get_backend()          # reads config.yaml
    backend = get_backend(config)    # explicit config dict
"""

from __future__ import annotations

import contextvars
import threading

from typing import Any, Dict, Optional

from config_loader import load_config 
from data_backend.base import DataBackend
from paths import APP_ROOT

# Context variable for thread-local / task-local backend binding
_current_backend_var: contextvars.ContextVar[Optional[DataBackend]] = contextvars.ContextVar(
    "current_backend", default=None
)


class BackendFactory:
    """
    Thread-safe singleton factory.
    Reads config once, creates the correct backend, caches it.
    Swap backends by changing config.yaml — no code changes needed.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._instance: Optional[DataBackend] = None

    def get(self, config: Optional[Dict[str, Any]] = None) -> DataBackend:
        """Return the singleton backend, creating it on first call."""
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                cfg = config if config is not None else self._load_config()
                self._instance = self.create_backend(cfg)
        return self._instance

    def reset(self) -> None:
        """Reset the singleton — useful when switching backends."""
        with self._lock:
            self._instance = None

    # ── Private / Instance creation ──────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        return load_config()["data_backend"]

    def create_backend(self, config: Dict[str, Any]) -> DataBackend:
        """Create a new DataBackend instance without caching it in the singleton."""
        backend_type = config["type"].lower()
        creator = self._creators().get(backend_type)
        if not creator:
            raise ValueError(
                f"Unknown data_backend type: '{backend_type}'. "
                "Supported: excel, sql, odoo, csv"
            )
        return creator(config)

    def _creators(self) -> Dict[str, Any]:
        return {
            "excel": self._create_excel,
            "sql":   self._create_sql,
            "odoo":  self._create_odoo,
            "csv":   self._create_csv,
        }

    def _create_excel(self, config: Dict) -> DataBackend:
        from data_backend.excel_backend import ExcelBackend
        cfg = config["excel"]
        return ExcelBackend(
            output_dir=cfg["output_dir"],
            root_dir=cfg["root_dir"],
            file_map=cfg.get("file_map", {}),
        )

    def _create_sql(self, config: Dict) -> DataBackend:
        from data_backend.sql_backend import SQLBackend
        cfg = config["sql"]
        return SQLBackend(
            connection_string=cfg["connection_string"],
            table_map=cfg.get("table_map", {}),
        )

    def _create_odoo(self, config: Dict) -> DataBackend:
        from data_backend.odoo_backend import OdooBackend
        cfg = config["odoo"]
        return OdooBackend(
            url=cfg["url"],
            db=cfg["db"],
            username=cfg["username"],
            api_key=cfg["api_key"],
            model_map=cfg.get("model_map", {}),
        )

    def _create_csv(self, config: Dict) -> DataBackend:
        from data_backend.csv_backend import CSVBackend
        return CSVBackend(
            directory=config["csv"]["directory"],
            root_dir=APP_ROOT
        )


# Module-level singleton factory instance
_factory = BackendFactory()


def create_backend(config: Dict[str, Any]) -> DataBackend:
    """Create a standalone DataBackend instance from config dictionary."""
    return _factory.create_backend(config)


def bind_backend(backend: Optional[DataBackend]) -> contextvars.Token:
    """Bind a DataBackend to the current execution context."""
    return _current_backend_var.set(backend)


def reset_bound_backend(token: contextvars.Token) -> None:
    """Reset the context-bound DataBackend back to its previous value."""
    _current_backend_var.reset(token)


def get_backend(config: Optional[Dict[str, Any]] = None) -> DataBackend:
    """Return the context-bound backend if set; otherwise return the singleton."""
    bound = _current_backend_var.get()
    if bound is not None:
        return bound
    return _factory.get(config)


def reset_backend() -> None:
    """Reset the backend singleton (e.g. when switching config)."""
    _factory.reset()
