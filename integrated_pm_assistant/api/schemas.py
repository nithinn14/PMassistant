"""
Pydantic request models for PMassistant API endpoints.
"""

from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator, StrictBool


DEFAULT_INTEGRATIONS: Dict[str, bool] = {
    "google_calendar": True,
    "gmail": True,
    "telegram": True,
    "gantt": True,
}

ALLOWED_INTEGRATION_KEYS = set(DEFAULT_INTEGRATIONS.keys())


class StartProjectRequest(BaseModel):
    project_name: Optional[str] = None
    project_description: Optional[str] = None
    data_backend: Optional[str] = "excel"
    integrations: Optional[Dict[str, StrictBool]] = Field(
        default_factory=lambda: dict(DEFAULT_INTEGRATIONS)
    )
    @field_validator("integrations", mode="before")
    @classmethod
    def validate_integrations(cls, v):
        if v is None:
            return dict(DEFAULT_INTEGRATIONS)
        if not isinstance(v, dict):
            raise ValueError("integrations must be a dictionary")
        unknown = set(v.keys()) - ALLOWED_INTEGRATION_KEYS
        if unknown:
            raise ValueError(f"Unknown integration key(s): {', '.join(sorted(unknown))}. Allowed keys: {', '.join(sorted(ALLOWED_INTEGRATION_KEYS))}")
        merged = dict(DEFAULT_INTEGRATIONS)
        merged.update(v)
        return merged


class SyncMeetingsRequest(BaseModel):
    project_name: Optional[str] = None
    meeting_type: Optional[str] = "Kickoff"


__all__ = ["StartProjectRequest", "SyncMeetingsRequest"]
