"""
Pluggable Data Backend for PM Assistant.

Switch between Excel, SQL, Odoo, or CSV by changing config.yaml:

    data_backend:
      type: "excel"  # excel | sql | odoo | csv

Usage in tools/agents:
    from data_backend import get_backend
    backend = get_backend()
    df = backend.read("employees")
    backend.write("MyProject_Tasks", df)
"""

from data_backend.registry import (
    get_backend,
    reset_backend,
    create_backend,
    bind_backend,
    reset_bound_backend,
    BackendFactory,
)
from data_backend.base import DataBackend

__all__ = [
    "get_backend",
    "reset_backend",
    "create_backend",
    "bind_backend",
    "reset_bound_backend",
    "BackendFactory",
    "DataBackend",
]
