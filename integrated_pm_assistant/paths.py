"""
Central definitions for application filesystem paths.
"""

from pathlib import Path

# Root folder containing config.yaml and the application code
APP_ROOT = Path(__file__).resolve().parent

# Fixed application paths
CONFIG_PATH = APP_ROOT / "config.yaml"
ENV_PATH = APP_ROOT / ".env"
DEFAULT_OUTPUT_DIR = APP_ROOT / "output"
CREDENTIALS_FILE = APP_ROOT / "credentials.json"
TOKEN_FILE = APP_ROOT / "token.json"


def resolve_path(path: str | Path, base: Path = APP_ROOT) -> Path:
    """Resolve relative paths against the application root."""
    path = Path(path)
    return path if path.is_absolute() else base / path