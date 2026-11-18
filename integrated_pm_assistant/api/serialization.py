"""
JSON sanitization and response serialization helpers.
"""

import json
import math
from starlette.responses import Response


def _sanitize(obj):
    """Recursively replace NaN / Infinity with None so JSON is valid."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _safe_jsonify(data, status=200):
    """Return a Response with sanitized JSON (no NaN)."""
    cleaned = _sanitize(data)
    body = json.dumps(cleaned, default=str, ensure_ascii=False)
    return Response(content=body, status_code=status, media_type="application/json")


__all__ = ["_sanitize", "_safe_jsonify"]
