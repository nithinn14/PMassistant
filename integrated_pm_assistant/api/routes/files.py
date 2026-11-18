"""
api/routes/files.py
───────────────────
FastAPI route definitions for file uploads and downloads.
"""

from pathlib import Path
from typing import Union

from fastapi import APIRouter, File, UploadFile
from starlette.responses import FileResponse

from api.serialization import _safe_jsonify
from api.services.files import handle_upload_file, resolve_download_path

router = APIRouter()


@router.post("/api/upload-file")
def upload_file(file: Union[UploadFile, str, None] = File(None)):
    """Accept an uploaded data file and save it to the base directory."""
    payload, status_code = handle_upload_file(file)
    return _safe_jsonify(payload, status=status_code)


@router.get("/api/download/{filename}")
def download_file(filename: str):
    """Serve a file from the output directory with directory traversal checks."""
    file_path, error_payload, status_code = resolve_download_path(filename)
    if error_payload:
        return _safe_jsonify(error_payload, status=status_code)

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


__all__ = [
    "router",
    "upload_file",
    "download_file",
]
