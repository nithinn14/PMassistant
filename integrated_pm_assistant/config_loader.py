import yaml
from pathlib import Path
from typing import Any, Dict

from paths import CONFIG_PATH


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path is not None else CONFIG_PATH

    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def get_data_backend_config(path: str | Path | None = None) -> Dict[str, Any]:
    config = load_config(path)
    return config["data_backend"]


def get_data_backend(path: str | Path | None = None):
    from data_backend.registry import get_backend
    return get_backend(get_data_backend_config(path))
