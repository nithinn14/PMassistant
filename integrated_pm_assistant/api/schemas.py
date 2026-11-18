"""
Pydantic request models for PMassistant API endpoints.
"""

from typing import Optional
from pydantic import BaseModel


class StartProjectRequest(BaseModel):
    project_name: Optional[str] = None
    project_description: Optional[str] = None
    data_backend: Optional[str] = "excel"


class SyncMeetingsRequest(BaseModel):
    project_name: Optional[str] = None
    meeting_type: Optional[str] = "Kickoff"


__all__ = ["StartProjectRequest", "SyncMeetingsRequest"]
