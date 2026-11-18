"""
api/services/files.py
─────────────────────
File storage, validation, and smart-merge services for PMassistant.

Provides:
  - handle_upload_file: Validates and saves uploaded data files, performing
    smart merge on employees.xlsx to retain historical workload data.
  - resolve_download_path: Validates filenames against path-traversal attacks
    and ensures target files exist within the output directory.
"""

import os
from pathlib import Path
import shutil
from typing import Optional, Tuple, Union

import pandas as pd
from werkzeug.utils import secure_filename

from paths import APP_ROOT, DEFAULT_OUTPUT_DIR

BASE_DIR = APP_ROOT
OUTPUT_DIR = DEFAULT_OUTPUT_DIR


def _save_file(file, target_path: Path) -> None:
    """Save an UploadFile object to a target path on disk."""
    file.file.seek(0)
    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)


def handle_upload_file(file, base_dir: Optional[Path] = None) -> tuple[dict, int]:
    """
    Validate and save an uploaded file.
    Performs smart-merging if uploading 'employees.xlsx' over an existing copy.

    Returns:
        tuple[dict, int]: (response_data, status_code)
    """
    if base_dir is None:
        base_dir = BASE_DIR

    if file is None:
        return {"error": "No file provided"}, 400
    if isinstance(file, str) or not getattr(file, "filename", None):
        return {"error": "Empty filename"}, 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv", ".json"):
        return {"error": "Only .xlsx, .csv, .json files are accepted"}, 400

    safe_name = secure_filename(file.filename)
    dest = base_dir / safe_name

    # Smart merge for employees.xlsx to prevent workload data loss
    if safe_name == "employees.xlsx" and dest.exists():
        try:
            print(f"📊 [Server] Smart Merge: {safe_name} detected. Preserving project history...")
            old_df = pd.read_excel(dest)
            temp_path = str(dest) + ".tmp"
            _save_file(file, Path(temp_path))
            new_df = pd.read_excel(temp_path)
            os.remove(temp_path)

            # Preserve workload and project state for existing employees
            # Find a matching key: Email, Employee_ID, or Employee_Name
            key = next(
                (k for k in ["Email", "Employee_ID", "Employee_Name"]
                 if k in old_df.columns and k in new_df.columns),
                None,
            )

            if key:
                # Normalize keys for robust matching
                old_df[key] = old_df[key].astype(str).str.strip()
                new_df[key] = new_df[key].astype(str).str.strip()

                cols_to_sync = ["Current_Project", "Allocated_Hours", "Free_Hours"]
                synced = []
                for col in cols_to_sync:
                    if col in old_df.columns:
                        # Re-create column in new_df if missing
                        if col not in new_df.columns:
                            new_df[col] = None

                        # Map old state to new dataframe
                        mapping = old_df.set_index(key)[col].to_dict()

                        def merge_row(row):
                            k = str(row[key]).strip()
                            old_v = mapping.get(k)
                            # Convert null/NaN to empty string for comparison
                            old_s = str(old_v).strip() if pd.notnull(old_v) else ""
                            new_v = row[col]
                            new_s = str(new_v).strip() if pd.notnull(new_v) else ""

                            # If server has historical data (non-empty), keep it
                            if old_s and old_s.lower() != "nan":
                                return old_v
                            # Otherwise use whatever was in the upload
                            return new_v

                        new_df[col] = new_df.apply(merge_row, axis=1)
                        synced.append(col)

                print(f"✅ [Server] Merged {len(new_df)} employees using key '{key}'. Synced: {synced}")
                new_df.to_excel(dest, index=False)
                return {
                    "saved_as": safe_name,
                    "merged": True,
                    "key_used": key,
                    "synced_cols": synced,
                }, 200

            # If no key found, fallback to standard save
            print(f"⚠️ [Server] No common key found for merge. Saving {safe_name} as-is.")
            new_df.to_excel(dest, index=False)
            return {
                "saved_as": safe_name,
                "merged": False,
                "reason": "No common key found",
            }, 200

        except Exception as e:
            print(f"❌ [Server] Smart Merge failed: {str(e)}")
            # Fallback in case of error
            _save_file(file, dest)
            return {"saved_as": safe_name, "error_merging": str(e)}, 200

    _save_file(file, dest)
    return {"saved_as": safe_name, "path": str(dest)}, 200


def resolve_download_path(
    filename: str, output_dir: Optional[Path] = None
) -> tuple[Optional[Path], Optional[dict], int]:
    """
    Validate that filename is safe (no path traversal) and refers to an existing
    file within output_dir.

    Returns:
        tuple[Optional[Path], Optional[dict], int]:
            (file_path, None, 200) on success
            (None, error_payload, status_code) on failure
    """
    if "/" in filename or "\\" in filename:
        return None, {"error": "File not found"}, 404

    if output_dir is None:
        output_dir = OUTPUT_DIR

    file_path = (output_dir / filename).resolve()
    output_dir_resolved = output_dir.resolve()

    try:
        file_path.relative_to(output_dir_resolved)
    except ValueError:
        return None, {"error": "File not found"}, 404

    if not file_path.is_file():
        return None, {"error": "File not found"}, 404

    return file_path, None, 200


__all__ = [
    "BASE_DIR",
    "OUTPUT_DIR",
    "handle_upload_file",
    "resolve_download_path",
]
