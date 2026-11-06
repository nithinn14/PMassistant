import unittest
from pathlib import Path
from unittest.mock import patch

from config_loader import load_config
from data_backend.registry import BackendFactory
from paths import CONFIG_PATH


class TestConfigPaths(unittest.TestCase):

    def test_default_config_path_is_application_relative(self):
        self.assertTrue(CONFIG_PATH.is_absolute())
        self.assertEqual(CONFIG_PATH, Path(__file__).resolve().parents[1] / "config.yaml")

    def test_default_load_config_uses_config_path(self):
        expected_config = {"data_backend": {"type": "excel"}}

        with patch("config_loader.yaml.safe_load", return_value=expected_config), \
             patch("builtins.open") as mock_open:

            result = load_config()

            mock_open.assert_called_once_with(CONFIG_PATH, "r", encoding="utf-8")
            self.assertEqual(result, expected_config)

    def test_explicit_config_path_is_preserved(self):
        custom_path = Path("custom_config.yaml")
        expected_config = {"data_backend": {"type": "csv"}}

        with patch("config_loader.yaml.safe_load", return_value=expected_config), \
             patch("builtins.open") as mock_open:

            result = load_config(custom_path)

            mock_open.assert_called_once_with(custom_path, "r", encoding="utf-8")
            self.assertEqual(result, expected_config)

    def test_registry_uses_centralized_config_loader(self):
        expected_config = {"type": "excel"}

        with patch("data_backend.registry.load_config",
                   return_value={"data_backend": expected_config}) as mock_loader:

            result = BackendFactory()._load_config()

            mock_loader.assert_called_once_with()
            self.assertEqual(result, expected_config)


if __name__ == "__main__":
    unittest.main()
