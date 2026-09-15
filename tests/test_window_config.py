import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import stock_floater.config as config


class WindowConfigTests(unittest.TestCase):
    def test_preserves_negative_window_position_for_left_monitor(self) -> None:
        original_path = config.CONFIG_PATH
        temp_path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        try:
            config.CONFIG_PATH = temp_path
            settings = config.AppSettings(window_x=-900, window_y=120)
            config.save_settings(settings)
            loaded = config.load_settings()
        finally:
            config.CONFIG_PATH = original_path

        self.assertEqual(loaded.window_x, -900)
        self.assertEqual(loaded.window_y, 120)


if __name__ == "__main__":
    unittest.main()
